"""The Wallet: card accounts, cards, provider catalog, funding instructions, reveal hint and the
overview behind the canvas widget (docs/specs/m15, sections 4.8 to 4.10).

Rules this module enforces (they are code, never prompts):

* Reading is for human members; every write is owner/admin only. An agent never connects an
  account, unfreezes or reveals a card, or changes a purchase limit.
* No method here can obtain a card number: ``reveal_card`` returns a link to the provider's own
  page. A key never leaves the control plane (``provider_for`` builds the adapter from the vault).
* Freezing is the safe direction and is also done by automation (emergency stop, pause, anomalies);
  unfreezing is human-only and never happens on resume.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.integrations.cards.base import (
    CardProvider,
    CardProviderError,
    CardProviderNotAvailable,
)
from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.company import Company
from app.models.enums import (
    PURCHASE_HOLD_STATUSES,
    AlertSeverity,
    CardAccountStatus,
    CardStatus,
    CompanyStatus,
    PurchaseStatus,
    SubscriptionStatus,
)
from app.models.purchasing import (
    AgentCard,
    CardAccount,
    CardTransaction,
    PurchaseRequest,
    Subscription,
)
from app.schemas.purchasing import (
    AgentPurchaseLimitUpdate,
    CardAccountCheck,
    CardAccountCreate,
    CardActionRequest,
    CardProviderList,
    CardReveal,
    FundingInstructions,
    PurchasePolicyRecommendation,
    SubscriptionOut,
    WalletBalance,
    WalletCheckoutItem,
    WalletMonth,
    WalletOverview,
    WalletRenewal,
    WalletSetupHint,
    WalletSyncResult,
)
from app.services import (
    alerts,
    audit,
    connected_accounts,
    emergency,
    events,
    ledger,
    policy,
    secrets_scan,
)
from app.services.pagination import paginate

logger = logging.getLogger("company_os.wallet")

GUIDE_UPDATED_ON = "2026-10-27"
REVEAL_MAX_AGE_SECONDS = 300
REVEAL_PER_HOUR = 10
RENEWAL_WINDOW_DAYS = 30
# Tests install a stateful fake here (account id -> provider). The ``fake`` provider is never built
# from a stored account anywhere else, so it cannot exist in production (``create_account`` refuses
# it unless the caller says it is a test environment).
PROVIDER_OVERRIDES: dict[str, CardProvider] = {}


class ReauthRequired(Forbidden):
    """Revealing where a card number can be read needs a recent sign-in (the UI sends the person
    through sign-in again, then retries)."""

    code = "reauth_required"
    default_message = "Sign in again to do this"


# ---------------------------------------------------------------- guards and lookups


def require_member_read(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot use the Wallet; request a purchase instead")


def get_account(db: Session, company_id: str, account_id: str) -> CardAccount:
    row = db.scalar(
        select(CardAccount).where(
            CardAccount.id == account_id, CardAccount.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Card account not found")
    return row


def get_card(db: Session, company_id: str, card_id: str) -> AgentCard:
    row = db.scalar(
        select(AgentCard).where(AgentCard.id == card_id, AgentCard.company_id == company_id)
    )
    if row is None:
        raise NotFound("Card not found")
    return row


def active_account(db: Session, company_id: str) -> CardAccount | None:
    """The account new purchases use: the default one, else the newest active one."""
    return db.scalar(
        select(CardAccount)
        .where(CardAccount.company_id == company_id, CardAccount.status == "active")
        .order_by(CardAccount.is_default.desc(), CardAccount.created_at.desc(), CardAccount.id)
        .limit(1)
    )


def company_halted(db: Session, company_id: str) -> str | None:
    """Why no card may be issued or unfrozen right now (None = it may)."""
    if emergency.is_active(db):
        return "the emergency stop is active"
    company = db.get(Company, company_id)
    if company is not None and company.status == CompanyStatus.PAUSED:
        return "the company is paused"
    return None


# ---------------------------------------------------------------- providers


def provider_for(db: Session, account: CardAccount) -> CardProvider:
    """The adapter for an account, built from its connected account's vault secrets (the control
    plane makes every call; agents never see a key). ``fake`` is only ever the instance a test
    installed (``PROVIDER_OVERRIDES``, or ``cards.registry.register_test_provider``)."""
    override = PROVIDER_OVERRIDES.get(account.id)
    if override is not None:
        return override
    from app.integrations.cards import registry  # local: the adapters import services

    return registry.provider_for(db, account)


def list_providers(db: Session, ctx: CompanyContext) -> CardProviderList:
    """The catalog with capabilities, eligibility, the human steps and what is verified; the
    wording and the dated guide text live in ``integrations.cards.guides``."""
    require_member_read(ctx)
    from app.integrations.cards import guides  # local: keeps the guide text out of import time

    return CardProviderList(items=guides.provider_infos(), guide_updated_on=guides.GUIDE_UPDATED_ON)


# ---------------------------------------------------------------- accounts


def list_accounts(db: Session, ctx: CompanyContext) -> list[CardAccount]:
    require_member_read(ctx)
    return list(
        db.scalars(
            select(CardAccount)
            .where(CardAccount.company_id == ctx.company_id)
            .order_by(CardAccount.created_at, CardAccount.id)
        )
    )


def create_account(
    db: Session, ctx: CompanyContext, data: CardAccountCreate, *, allow_fake: bool = False
) -> CardAccount:
    """Owner/admin humans only (``card_account.connect``). A manual account needs no key; a
    provider account points at a connected account of the same provider (the key stays in the
    vault). Audited."""
    require_human_admin(ctx, "connect a card account")
    if data.provider == "fake" and not allow_fake:
        raise ValidationFailed("The fake card provider exists only in the test suite")
    connected_id = None
    if data.provider == "manual":
        if data.connected_account_id:
            raise ValidationFailed("A manual card has no connected account")
    elif data.provider != "fake":
        if not data.connected_account_id:
            raise ValidationFailed(
                f"Connect your {data.provider} credentials first, then pass connected_account_id",
                details=[{"loc": ["body", "connected_account_id"], "msg": "required"}],
            )
        linked = connected_accounts.get_account(db, ctx, data.connected_account_id)
        if linked.provider != data.provider:
            raise ValidationFailed("That connected account belongs to another provider")
        if linked.status == "revoked":
            raise Conflict("That connected account was revoked")
        connected_id = linked.id
    # A provider account starts in ``setup`` and becomes active when its live check passes; a
    # manual card has nothing to check.
    status = (
        CardAccountStatus.ACTIVE if data.provider in ("manual", "fake") else CardAccountStatus.SETUP
    )
    row = CardAccount(
        company_id=ctx.company_id,
        provider=data.provider,
        label=data.label,
        status=status.value,
        mode=data.mode,
        connected_account_id=connected_id,
        external_ref=data.external_ref,
        manual_brand=data.manual_brand if data.provider == "manual" else None,
        manual_last4=data.manual_last4 if data.provider == "manual" else None,
        is_default=False,
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    db.flush()
    if data.make_default or active_account(db, ctx.company_id) is None:
        _make_default(db, row)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card_account.created",
        target_type="card_account",
        target_id=row.id,
        summary=f"Connected card account '{row.label}' ({row.provider}, {row.mode})",
        data={"provider": row.provider, "mode": row.mode, "status": row.status},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_account_id": row.id})
    db.commit()
    return row


def _make_default(db: Session, row: CardAccount) -> None:
    for other in db.scalars(
        select(CardAccount).where(
            CardAccount.company_id == row.company_id, CardAccount.id != row.id
        )
    ):
        other.is_default = False
    row.is_default = True


def check_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccountCheck:
    """Live check. No card is created in live mode; test mode may create and close a $1 card.
    A passing check activates a ``setup`` account; a failing one marks it ``attention``."""
    require_human_admin(ctx, "check a card account")
    account = get_account(db, ctx.company_id, account_id)
    if account.status == CardAccountStatus.DISABLED:
        raise Conflict("This card account is disabled")
    if account.provider == "manual":
        return CardAccountCheck(
            account_id=account.id,
            ok=True,
            message="A manual card has nothing to check: you pay with your own card.",
        )
    now = utcnow()
    try:
        result = provider_for(db, account).check()
    except CardProviderError as exc:
        account.status = CardAccountStatus.ATTENTION.value
        account.last_error = (secrets_scan.scrub(exc.message) or "")[:500]
        account.updated_at = now
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="card_account.check_failed",
            target_type="card_account",
            target_id=account.id,
            summary=f"Card account '{account.label}' failed its check",
            data={"provider": account.provider},
        )
        db.commit()
        return CardAccountCheck(
            account_id=account.id, ok=False, message=(secrets_scan.scrub(exc.message) or "")[:500]
        )
    ok = bool(result.get("ok"))
    account.status = (CardAccountStatus.ACTIVE if ok else CardAccountStatus.ATTENTION).value
    account.last_error = None if ok else str(result.get("message") or "the check failed")[:500]
    balance = result.get("balance_micros")
    if isinstance(balance, int) and not isinstance(balance, bool):
        account.last_balance_micros, account.balance_checked_at = balance, now
    account.updated_at = now
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card_account.checked",
        target_type="card_account",
        target_id=account.id,
        summary=f"Checked card account '{account.label}': {'ok' if ok else 'needs attention'}",
        data={"provider": account.provider, "ok": ok, "mode": result.get("mode")},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_account_id": account.id})
    db.commit()
    mode = result.get("mode")
    return CardAccountCheck(
        account_id=account.id,
        ok=ok,
        mode=mode if mode in ("live", "test") else None,
        issuing_enabled=result.get("issuing_enabled"),
        balance_readable=result.get("balance_readable"),
        balance_micros=balance
        if isinstance(balance, int) and not isinstance(balance, bool)
        else None,
        message=None if ok else account.last_error,
    )


def funding_instructions(db: Session, ctx: CompanyContext, account_id: str) -> FundingInstructions:
    """Fetched live for the owner, never cached or stored. Opening them is audited (no data)."""
    require_human_admin(ctx, "see funding instructions")
    account = get_account(db, ctx.company_id, account_id)
    if account.provider == "manual":
        raise Conflict("A manual card has no funding instructions: you pay from your own card")
    try:
        raw = provider_for(db, account).funding_instructions()
    except CardProviderError as exc:
        raise Conflict(
            f"The provider could not give funding instructions: {secrets_scan.scrub(exc.message)}"
        ) from exc
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card_account.funding_viewed",
        target_type="card_account",
        target_id=account.id,
        summary=f"Opened the funding instructions of '{account.label}'",
        data={},
    )
    db.commit()
    return FundingInstructions(
        account_id=account.id,
        provider=account.provider,  # type: ignore[arg-type]
        instructions={str(k): str(v) for k, v in raw.items()},
        fetched_at=utcnow(),
    )


def set_default_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccount:
    require_human_admin(ctx, "change the default card account")
    account = get_account(db, ctx.company_id, account_id)
    if account.status == CardAccountStatus.DISABLED:
        raise Conflict("A disabled card account cannot be the default")
    _make_default(db, account)
    account.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card_account.default_set",
        target_type="card_account",
        target_id=account.id,
        summary=f"'{account.label}' is now the default card account",
        data={},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_account_id": account.id})
    db.commit()
    return account


def disable_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccount:
    """Disabling freezes the account's cards (best effort; a card that could not be frozen raises a
    critical alert) and stops new purchases from using it."""
    require_human_admin(ctx, "disable a card account")
    account = get_account(db, ctx.company_id, account_id)
    if account.status == CardAccountStatus.DISABLED:
        return account
    frozen = _freeze_cards(
        db, list(_active_cards(db, ctx.company_id, account_id=account.id)), "account_disabled", None
    )
    account.status = CardAccountStatus.DISABLED.value
    account.is_default = False
    account.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card_account.disabled",
        target_type="card_account",
        target_id=account.id,
        summary=f"Disabled card account '{account.label}' and froze {frozen} card(s)",
        data={"cards_frozen": frozen},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_account_id": account.id})
    db.commit()
    return account


# ---------------------------------------------------------------- cards


def _active_cards(db: Session, company_id: str, *, account_id: str | None = None):
    stmt = select(AgentCard).where(
        AgentCard.company_id == company_id, AgentCard.status == CardStatus.ACTIVE
    )
    if account_id:
        stmt = stmt.where(AgentCard.card_account_id == account_id)
    return db.scalars(stmt.order_by(AgentCard.created_at, AgentCard.id))


def _freeze_cards(
    db: Session,
    cards: list[AgentCard],
    reason: str,
    principal,
    *,
    provider: CardProvider | None = None,
) -> int:
    """Freeze each card at its provider, then in our books. A provider failure leaves the card
    ``active`` and raises a critical alert (a human must act). Audit line per card. No commit."""
    frozen = 0
    now = utcnow()
    for card in cards:
        account = db.get(CardAccount, card.card_account_id)
        try:
            prov = provider or (provider_for(db, account) if account else None)
            if prov is None:
                raise CardProviderNotAvailable("no account for this card")
            prov.freeze(card.provider_card_id)
        except CardProviderError as exc:
            logger.warning("could not freeze card %s: %s", card.id, exc.message)
            alerts.raise_alert(
                db,
                card.company_id,
                kind="card_freeze_failed",
                severity=AlertSeverity.CRITICAL,
                title="A card could not be frozen",
                body=f"Card ending {card.last4} stays active at the provider ({reason}). Freeze it "
                "in the provider's dashboard.",
                related_type="card",
                related_id=card.id,
                dedupe_key=f"card_freeze_failed:{card.id}",
            )
            continue
        card.status = CardStatus.FROZEN.value
        card.frozen_reason = reason[:64]
        card.updated_at = now
        frozen += 1
        audit.record(
            db,
            company_id=card.company_id,
            principal=principal,
            action="card.frozen",
            target_type="card",
            target_id=card.id,
            summary=f"Froze the card ending {card.last4} ({reason})",
            data={"reason": reason, "purchase_request_id": card.purchase_request_id},
        )
    return frozen


def freeze_all_cards(
    db: Session, company_id: str, *, reason: str, provider: CardProvider | None = None
) -> int:
    """Emergency stop / company pause: freeze every active agent card (best effort, one audit line
    per card). Resume does not unfreeze: a human does, per card. No commit (the caller owns the
    transaction). Returns the number frozen."""
    cards = list(_active_cards(db, company_id))
    if not cards:
        return 0
    frozen = _freeze_cards(db, cards, reason, None, provider=provider)
    if frozen:
        events.emit(db, company_id, "wallet.updated", {"cards_frozen": frozen, "reason": reason})
    return frozen


def list_cards(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AgentCard], str | None]:
    require_member_read(ctx)
    stmt = select(AgentCard).where(AgentCard.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(AgentCard.status == status)
    return paginate(db, stmt, AgentCard, limit=limit, cursor=cursor, descending=True)


def freeze_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    require_human_admin(ctx, "freeze a card")
    card = get_card(db, ctx.company_id, card_id)
    if card.status == CardStatus.FROZEN:
        return card
    if card.status != CardStatus.ACTIVE:
        raise Conflict(f"A {card.status} card cannot be frozen")
    account = db.get(CardAccount, card.card_account_id)
    assert account is not None
    try:
        provider_for(db, account).freeze(card.provider_card_id)
    except CardProviderError as exc:
        raise Conflict(
            f"The provider did not freeze the card: {secrets_scan.scrub(exc.message)}"
        ) from exc
    card.status = CardStatus.FROZEN.value
    card.frozen_reason = (data.reason or "frozen by a person")[:64]
    card.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card.frozen",
        target_type="card",
        target_id=card.id,
        summary=f"Froze the card ending {card.last4}",
        data={"reason": card.frozen_reason},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_id": card.id})
    db.commit()
    return card


def unfreeze_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    """Human only (``card.unfreeze``). Refused while the emergency stop is on or the company is
    paused: a card must not come back to life behind a stop."""
    require_human_admin(ctx, "unfreeze a card")
    card = get_card(db, ctx.company_id, card_id)
    if card.status != CardStatus.FROZEN:
        raise Conflict(f"Only a frozen card can be unfrozen (this one is {card.status})")
    halted = company_halted(db, ctx.company_id)
    if halted:
        raise Conflict(f"A card cannot be unfrozen while {halted}")
    account = db.get(CardAccount, card.card_account_id)
    assert account is not None
    try:
        provider_for(db, account).unfreeze(card.provider_card_id)
    except CardProviderError as exc:
        raise Conflict(
            f"The provider did not unfreeze the card: {secrets_scan.scrub(exc.message)}"
        ) from exc
    card.status = CardStatus.ACTIVE.value
    card.frozen_reason = None
    card.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card.unfrozen",
        target_type="card",
        target_id=card.id,
        summary=f"Unfroze the card ending {card.last4}",
        data={"reason": data.reason},
    )
    events.emit(db, ctx.company_id, "wallet.updated", {"card_id": card.id})
    db.commit()
    return card


def close_card_for_system(
    db: Session,
    card: AgentCard,
    *,
    reason: str,
    principal=None,
    provider: CardProvider | None = None,
) -> bool:
    """Close a card at the provider and in our books (no commit). False when the provider refused:
    the card then stays as it is, and the caller decides (usually an alert)."""
    if card.status == CardStatus.CLOSED:
        return True
    account = db.get(CardAccount, card.card_account_id)
    try:
        prov = provider or (provider_for(db, account) if account else None)
        if prov is None:
            return False
        prov.close(card.provider_card_id)
    except CardProviderError as exc:
        logger.warning("could not close card %s: %s", card.id, exc.message)
        return False
    now = utcnow()
    card.status = CardStatus.CLOSED.value
    card.closed_at = now
    card.updated_at = now
    audit.record(
        db,
        company_id=card.company_id,
        principal=principal,
        action="card.closed",
        target_type="card",
        target_id=card.id,
        summary=f"Closed the card ending {card.last4} ({reason})",
        data={"reason": reason, "purchase_request_id": card.purchase_request_id},
    )
    return True


def close_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    require_human_admin(ctx, "close a card")
    card = get_card(db, ctx.company_id, card_id)
    if card.status == CardStatus.CLOSED:
        return card
    if card.purchase_request_id:
        purchase = db.get(PurchaseRequest, card.purchase_request_id)
        if purchase is not None and purchase.status in PURCHASE_HOLD_STATUSES:
            raise Conflict("This card belongs to a purchase in progress; cancel the purchase")
    if not close_card_for_system(
        db, card, reason=data.reason or "closed by a person", principal=ctx.principal
    ):
        raise Conflict("The provider did not close the card; try again")
    events.emit(db, ctx.company_id, "wallet.updated", {"card_id": card.id})
    db.commit()
    return card


def reveal_card(
    db: Session, ctx: CompanyContext, card_id: str, *, recent_auth: bool = False
) -> CardReveal:
    """Owner/admin with a recent sign-in (``recent_auth``, decided by the route from the session);
    rate limited; audited as ``card.reveal_opened`` with no card data. Returns a link to the
    provider's own page: the number never passes through this server."""
    require_human_admin(ctx, "reveal a card")
    card = get_card(db, ctx.company_id, card_id)
    if not recent_auth:
        raise ReauthRequired(
            "Sign in again to open the card on the provider's page",
            details={"reason": "recent_login_required", "max_age_seconds": REVEAL_MAX_AGE_SECONDS},
        )
    since = utcnow() - timedelta(hours=1)
    opened = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.company_id == ctx.company_id,
            AuditEvent.action == "card.reveal_opened",
            AuditEvent.actor_id == ctx.principal.id,
            AuditEvent.created_at >= since,
        )
    )
    if (opened or 0) >= REVEAL_PER_HOUR:
        raise RateLimited(
            "Too many card reveals in the last hour",
            details={"key": "card_reveals_per_hour", "limit": REVEAL_PER_HOUR},
        )
    account = db.get(CardAccount, card.card_account_id)
    assert account is not None
    try:
        hint = provider_for(db, account).reveal_hint(card.provider_card_id)
    except CardProviderError as exc:
        raise Conflict(
            f"The provider gave no link for this card: {secrets_scan.scrub(exc.message)}"
        ) from exc
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="card.reveal_opened",
        target_type="card",
        target_id=card.id,
        summary=f"Opened the card ending {card.last4} on the provider's page",
        data={},
    )
    db.commit()
    return CardReveal(
        kind=hint.kind,  # type: ignore[arg-type]
        url=hint.url,
        provider=account.provider,  # type: ignore[arg-type]
        note="The number is shown on the provider's own page; Foundry never sees or stores it.",
    )


def list_transactions(
    db: Session, ctx: CompanyContext, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[CardTransaction], str | None]:
    require_member_read(ctx)
    stmt = select(CardTransaction).where(CardTransaction.company_id == ctx.company_id)
    return paginate(db, stmt, CardTransaction, limit=limit, cursor=cursor, descending=True)


def sync(
    db: Session,
    ctx: CompanyContext,
    *,
    provider_factory: Callable[[Session, CardAccount], CardProvider] | None = None,
) -> WalletSyncResult:
    """``POST /wallet/sync``: reconcile now (``card_reconcile.sync_company``)."""
    require_human_admin(ctx, "sync the wallet")
    from app.services import card_reconcile  # local: it depends on this module

    result = card_reconcile.sync_company(db, ctx.company_id, provider_factory=provider_factory)
    db.commit()
    return result


# ---------------------------------------------------------------- agents and subscriptions


def set_agent_purchase_limit(
    db: Session, ctx: CompanyContext, agent_id: str, data: AgentPurchaseLimitUpdate
) -> Agent:
    """Human only (``purchase_policy.update``): an agent's monthly purchase cap (null = only the
    company policy applies). An agent can never change it, its own included."""
    require_human_admin(ctx, "change an agent's purchase limit")
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == ctx.company_id))
    if agent is None:
        raise NotFound("Agent not found")
    if agent.purchase_monthly_limit_micros != data.purchase_monthly_limit_micros:
        agent.purchase_monthly_limit_micros = data.purchase_monthly_limit_micros
        agent.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="agent.purchase_limit_changed",
            target_type="agent",
            target_id=agent.id,
            summary=f"Set the monthly purchase limit of {agent.name}",
            data={"purchase_monthly_limit_micros": data.purchase_monthly_limit_micros},
        )
        events.emit(db, ctx.company_id, "agent.updated", {"agent_id": agent.id})
        db.commit()
    return agent


def serialize_subscriptions(db: Session, rows: list[Subscription]) -> list[SubscriptionOut]:
    names = dict(
        db.execute(
            select(Agent.id, Agent.name).where(
                Agent.id.in_({r.agent_id for r in rows if r.agent_id})
            )
        ).all()
    )
    out = []
    for row in rows:
        item = SubscriptionOut.model_validate(row)
        item.agent_name = names.get(row.agent_id or "")
        out.append(item)
    return out


def on_agent_retired(db: Session, agent: Agent, principal) -> None:
    """A retired agent's unused purchases are cancelled (cards closed, holds released) and its
    subscriptions pass to its manager, else to the Co-founder. No commit."""
    from app.services import purchasing, wakes  # local: both depend on this module

    purchasing.cancel_for_retired_agent(db, agent, principal)
    heir = agent.manager_agent_id
    if heir is None:
        founder = wakes.active_founder(db, agent.company_id)
        heir = founder.id if founder is not None and founder.id != agent.id else None
    subs = list(
        db.scalars(
            select(Subscription).where(
                Subscription.company_id == agent.company_id,
                Subscription.agent_id == agent.id,
                Subscription.status.notin_(
                    [SubscriptionStatus.CANCELLED.value, SubscriptionStatus.LAPSED.value]
                ),
            )
        )
    )
    for sub in subs:
        sub.agent_id = heir
        sub.updated_at = utcnow()
        audit.record(
            db,
            company_id=agent.company_id,
            principal=principal,
            action="subscription.transferred",
            target_type="subscription",
            target_id=sub.id,
            summary=f"Subscription to {sub.vendor} passed on because {agent.name} was retired",
            data={"from_agent_id": agent.id, "to_agent_id": heir},
        )
    for card in db.scalars(
        select(AgentCard).where(
            AgentCard.company_id == agent.company_id, AgentCard.agent_id == agent.id
        )
    ):
        card.agent_id = heir


# ---------------------------------------------------------------- overview


def _month_period(db: Session, company_id: str, now: datetime) -> str:
    company = db.get(Company, company_id)
    return ledger.periods_for(now, company.timezone if company else "UTC").month


def overview(db: Session, ctx: CompanyContext) -> WalletOverview:
    """Members read. Not set up answers ``enabled: false`` with the recommended values."""
    require_member_read(ctx)
    cid = ctx.company_id
    now = utcnow()
    limits = policy.get_policy(db, ctx)
    accounts = [a for a in list_accounts(db, ctx) if a.status != CardAccountStatus.DISABLED]
    default = active_account(db, cid)
    period = _month_period(db, cid, now)
    settled = db.scalar(
        select(func.coalesce(func.sum(PurchaseRequest.settled_micros), 0)).where(
            PurchaseRequest.company_id == cid,
            PurchaseRequest.status == PurchaseStatus.SETTLED,
            PurchaseRequest.period_month == period,
        )
    )
    held = db.scalar(
        select(func.coalesce(func.sum(PurchaseRequest.held_micros), 0)).where(
            PurchaseRequest.company_id == cid,
            PurchaseRequest.status.in_([s.value for s in PURCHASE_HOLD_STATUSES]),
        )
    )
    waiting = list(
        db.scalars(
            select(PurchaseRequest)
            .where(
                PurchaseRequest.company_id == cid,
                PurchaseRequest.status == PurchaseStatus.AWAITING_CHECKOUT,
            )
            .order_by(PurchaseRequest.created_at, PurchaseRequest.id)
            .limit(20)
        )
    )
    pending = db.scalar(
        select(func.count(PurchaseRequest.id)).where(
            PurchaseRequest.company_id == cid,
            PurchaseRequest.status == PurchaseStatus.PENDING_APPROVAL,
        )
    )
    today = now.date()
    horizon = today + timedelta(days=max(limits.subscription_renewal_alert_days, 7))
    renewals = list(
        db.scalars(
            select(Subscription)
            .where(
                Subscription.company_id == cid,
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.REVIEW_DUE.value]
                ),
                Subscription.next_renewal_on.is_not(None),
                Subscription.next_renewal_on <= horizon,
            )
            .order_by(Subscription.next_renewal_on)
            .limit(20)
        )
    )
    due_30 = db.scalar(
        select(func.coalesce(func.sum(Subscription.amount_micros), 0)).where(
            Subscription.company_id == cid,
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.REVIEW_DUE.value]
            ),
            Subscription.next_renewal_on.is_not(None),
            Subscription.next_renewal_on <= today + timedelta(days=RENEWAL_WINDOW_DAYS),
        )
    )
    balance = None
    low = False
    if default is not None:
        if default.provider == "manual":
            balance = WalletBalance(available_micros=None, as_of=None, source="manual")
        else:
            balance = WalletBalance(
                available_micros=default.last_balance_micros,
                as_of=default.balance_checked_at,
                source="provider",
            )
            if default.last_balance_micros is not None:
                low = default.last_balance_micros < int(held or 0) + int(due_30 or 0)
    attention = next(
        (
            f"{a.label}: {a.last_error or 'needs attention'}"
            for a in accounts
            if a.status == CardAccountStatus.ATTENTION
        ),
        None,
    )
    if attention is None:
        frozen = db.scalar(
            select(func.count(AgentCard.id)).where(
                AgentCard.company_id == cid, AgentCard.status == CardStatus.FROZEN
            )
        )
        if frozen:
            attention = f"{frozen} card(s) are frozen; a person unfreezes them one by one"
    enabled = bool(limits.purchasing_enabled and default is not None)
    setup = None
    if not enabled:
        setup = WalletSetupHint(
            providers=["manual", "stripe_issuing", "mercury"],
            recommended_policy=PurchasePolicyRecommendation(
                purchase_cofounder_limit_micros=50_000_000,
                purchase_cofounder_monthly_micros=200_000_000,
                purchase_auto_approve_micros=0,
                purchase_recurring_requires_human=True,
                purchase_new_vendor_requires_human=True,
            ),
        )
    return WalletOverview(
        available=True,
        enabled=enabled,
        accounts=accounts,
        balance=balance,
        month=WalletMonth(
            period=period,
            settled_verified_micros=int(settled or 0),
            held_micros=int(held or 0),
            budget_micros=limits.purchase_monthly_budget_micros,
        ),
        awaiting_checkout=[
            WalletCheckoutItem(
                purchase_request_id=p.id,
                item=p.item,
                vendor=p.vendor,
                vendor_domain=p.vendor_domain,
                amount_micros=p.amount_micros,
                checkout_task_id=p.checkout_task_id,
                expires_at=p.expires_at,
            )
            for p in waiting
        ],
        pending_approvals=int(pending or 0),
        renewals_due=[
            WalletRenewal(
                subscription_id=s.id,
                vendor=s.vendor,
                amount_micros=s.amount_micros,
                next_renewal_on=s.next_renewal_on,  # type: ignore[arg-type]
            )
            for s in renewals
        ],
        low_balance=low,
        attention=attention,
        setup=setup,
    )
