"""Reconciliation of provider transactions into the ledger (docs/specs/m15, section 4.5).

``sync_company`` polls every active card account (``list_transactions(since=cursor - 3 days)``),
upserts ``card_transactions`` idempotently on ``(card_account_id, provider_txn_id)`` and matches
each one to the purchase or subscription that owns the card:

* an **authorization** moves the purchase to ``authorized``, completes the checkout task and wakes
  the agent;
* a **capture** settles the purchase: a ``verified`` ``external_expenses`` row (``source="card"``,
  unique per transaction, so a replayed sync records it once), the hold released, a single-use card
  closed, and a subscription created for a recurring purchase;
* a **refund** is a negative verified expense;
* a **decline** is recorded, and an "insufficient funds" decline raises the low-balance alert;
* anything that does not fit freezes the card and raises a critical alert: a transaction on one of
  our cards with no request behind it, a merchant that does not look like the approved vendor, an
  amount above the approved amount plus the tolerance, or a second charge on a single-use card.

Polling is the source of truth; webhooks only trigger ``sync_company``. A provider error on one
account never stops the others, and nothing here ever stores or logs a card number: provider
objects carry none, and ``raw`` keeps only the mapped, non-sensitive fields.

Money is integer micro-USD. ``cost_kind`` is ``verified`` only for a settled provider transaction
(or a human-confirmed amount, which WP1's mark-paid records itself); an approved-but-unsettled hold
stays ``held_micros`` and is never shown as a charge.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import utcnow
from app.integrations.cards import registry
from app.integrations.cards.base import CardProvider, CardTxn
from app.models.company import Company
from app.models.enums import (
    PURCHASE_HOLD_STATUSES,
    ActorType,
    AlertSeverity,
    CardAccountStatus,
    CardKind,
    CardStatus,
    CardTxnKind,
    CardTxnMatch,
    CardTxnSource,
    CardTxnStatus,
    CompanyStatus,
    PurchaseStatus,
    Recurrence,
    SubscriptionStatus,
    TaskStatus,
)
from app.models.guardrails import ExternalExpense
from app.models.purchasing import (
    AgentCard,
    CardAccount,
    CardTransaction,
    PurchaseRequest,
    Subscription,
)
from app.models.work import Task
from app.schemas.purchasing import WalletSyncResult
from app.services import (
    alerts,
    audit,
    connected_accounts,
    credentials,
    events,
    policy,
    subscriptions,
    tasks,
    wakes,
)

if TYPE_CHECKING:
    from app.integrations.context import IntegrationContext

logger = logging.getLogger("company_os.card_reconcile")

ProviderFactory = Callable[[Session, CardAccount], CardProvider]

SYNC_OVERLAP = timedelta(days=3)  # re-read this far back: late settlements and status changes
MAX_PAGES = 50
LOW_BALANCE_BUFFER_MICROS = 10_000_000  # $10 of slack above holds and upcoming renewals
RENEWAL_LOOKAHEAD_DAYS = 30
SYNCABLE = (CardAccountStatus.ACTIVE.value, CardAccountStatus.ATTENTION.value)
MISMATCH_MIN_TOKEN = 4

# ---------------------------------------------------------------------- entry points


def sync_company(
    db: Session,
    company_id: str,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> WalletSyncResult:
    """Poll every active card account of the company and reconcile. ``provider_factory`` lets
    tests inject the fake provider. Commits after each account; one account's failure is recorded
    on it and never stops the others."""
    now = now or utcnow()
    factory = provider_factory or registry.provider_for
    account_ids = list(
        db.scalars(
            select(CardAccount.id)
            .where(
                CardAccount.company_id == company_id,
                CardAccount.status.in_(SYNCABLE),
                CardAccount.provider != "manual",
            )
            .order_by(CardAccount.created_at, CardAccount.id)
        )
    )
    result = WalletSyncResult(accounts_synced=0, transactions_seen=0, transactions_new=0, errors=[])
    for account_id in account_ids:
        provider = None
        try:
            account = db.get(CardAccount, account_id)
            if account is None or account.company_id != company_id:
                continue
            provider = factory(db, account)
            if not provider.capabilities.list_transactions:
                continue
            seen, new, failed = _sync_account(db, account, provider, now)
            db.commit()
            result.accounts_synced += 1
            result.transactions_seen += seen
            result.transactions_new += new
            if failed:
                message = f"{failed} transaction(s) of {account.label} could not be processed"
                result.errors.append(message)
                _record_failure(db, company_id, account_id, message, now)
        except Exception as exc:
            db.rollback()
            message = _safe(exc)
            result.errors.append(message)
            _record_failure(db, company_id, account_id, message, now)
        finally:
            if provider is not None:
                registry.release(provider)
    return result


class WebhookRejected(AppError):
    """Same 401 and body as the e-mail webhook, whatever the reason."""

    status_code = 401
    code = "webhook_rejected"
    default_message = "The webhook could not be verified"


def receive_webhook(
    db: Session,
    ic: IntegrationContext,
    company_id: str,
    provider_id: str,
    headers: dict[str, str],
    body: bytes,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    """``POST /api/webhooks/cards/{company_id}/{provider}``: verify the signature, then ONLY trigger
    a sync (polling stays the source of truth: the public URL may be ephemeral). An unknown
    company, no configured webhook secret, a provider without webhooks and a bad or stale
    signature are all the same 401. The event content is never trusted or stored."""
    if provider_id not in registry.WEBHOOK_PROVIDERS:
        raise WebhookRejected()
    account = connected_accounts.find_account(db, company_id, provider_id)
    secret_id = ((account.extra_credential_ids or {}).get("webhook_secret")) if account else None
    if account is None or not secret_id:
        raise WebhookRejected()
    try:
        secret = credentials.reveal(db, ic.cipher, company_id, secret_id)
        events_seen = registry.webhook_events(provider_id, headers, body, secret)
    except Exception as exc:
        raise WebhookRejected() from exc
    result = sync_company(db, company_id, now=now, provider_factory=provider_factory)
    return {"received": len(events_seen), "synced": result.accounts_synced}


def run_all(db: Session, *, now: datetime | None = None) -> int:
    """Scheduler pass (every 15 minutes): ``sync_company`` for each company with an active card
    account. Returns the number of companies synced."""
    now = now or utcnow()
    synced = 0
    for company_id in _companies_with_accounts(db):
        try:
            sync_company(db, company_id, now=now)
            synced += 1
        except Exception:  # one company must never stop the pass
            db.rollback()
            logger.exception("card sync failed for company %s", company_id)
    return synced


def check_balances(
    db: Session,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> int:
    """Hourly pass: raises ``wallet_low_balance`` when an account's available balance does not
    cover the holds, the renewals due in 30 days and a buffer. Returns the alerts raised."""
    now = now or utcnow()
    factory = provider_factory or registry.provider_for
    raised = 0
    account_ids = list(
        db.scalars(
            select(CardAccount.id)
            .join(Company, Company.id == CardAccount.company_id)
            .where(
                CardAccount.status.in_(SYNCABLE),
                CardAccount.provider != "manual",
                Company.status == CompanyStatus.ACTIVE,
            )
            .order_by(CardAccount.created_at, CardAccount.id)
        )
    )
    for account_id in account_ids:
        provider = None
        try:
            account = db.get(CardAccount, account_id)
            if account is None:
                continue
            provider = factory(db, account)
            if not provider.capabilities.balance:
                continue
            balance = provider.balance()
            account.last_balance_micros = balance.available_micros
            account.balance_checked_at = now
            if balance.available_micros is not None:
                required = required_micros(db, account, now.date())
                key = _low_balance_key(account.id)
                if balance.available_micros < required:
                    alert = alerts.raise_alert(
                        db,
                        account.company_id,
                        kind="wallet_low_balance",
                        severity=AlertSeverity.WARNING,
                        title="The agent card balance is running low",
                        body=_low_balance_body(account, balance.available_micros, required),
                        related_type="card_account",
                        related_id=account.id,
                        dedupe_key=key,
                    )
                    raised += 1 if alert is not None else 0
                else:
                    alerts.resolve_by_key(db, account.company_id, key)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("balance check failed for card account %s: %s", account_id, _safe(exc))
        finally:
            if provider is not None:
                registry.release(provider)
    return raised


def required_micros(db: Session, account: CardAccount, today: date) -> int:
    """What the account must cover: holds not yet reserved by the provider (approved, issuing, card
    issued, awaiting checkout), renewals due within 30 days, plus the buffer."""
    default_id = db.scalar(
        select(CardAccount.id)
        .where(
            CardAccount.company_id == account.company_id,
            CardAccount.status.in_(SYNCABLE),
            CardAccount.provider != "manual",
        )
        .order_by(CardAccount.is_default.desc(), CardAccount.created_at, CardAccount.id)
        .limit(1)
    )
    cards = {
        c.id: c.card_account_id
        for c in db.scalars(select(AgentCard).where(AgentCard.company_id == account.company_id))
    }
    held = 0
    open_statuses = [s for s in PURCHASE_HOLD_STATUSES if s != PurchaseStatus.AUTHORIZED]
    for purchase in db.scalars(
        select(PurchaseRequest).where(
            PurchaseRequest.company_id == account.company_id,
            PurchaseRequest.status.in_([s.value for s in open_statuses]),
        )
    ):
        owner = cards.get(purchase.card_id) if purchase.card_id else default_id
        if owner == account.id:
            held += purchase.held_micros
    due = 0
    horizon = today + timedelta(days=RENEWAL_LOOKAHEAD_DAYS)
    for sub in db.scalars(
        select(Subscription).where(
            Subscription.company_id == account.company_id,
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.REVIEW_DUE.value]
            ),
            Subscription.next_renewal_on.is_not(None),
            Subscription.next_renewal_on <= horizon,
        )
    ):
        owner = cards.get(sub.card_id) if sub.card_id else default_id
        if owner == account.id:
            due += sub.amount_micros
    return held + due + LOW_BALANCE_BUFFER_MICROS


def tolerance_micros(db: Session, company_id: str, amount_micros: int) -> int:
    """Room above an approved amount for tax and FX: the larger of a percentage and a minimum."""
    pol = policy.load_policy(db, company_id)
    return max(
        amount_micros * pol.purchase_tolerance_bps // 10_000, pol.purchase_tolerance_min_micros
    )


# ---------------------------------------------------------------------- one account


def _companies_with_accounts(db: Session) -> list[str]:
    return list(
        db.scalars(
            select(CardAccount.company_id)
            .distinct()
            .join(Company, Company.id == CardAccount.company_id)
            .where(
                CardAccount.status.in_(SYNCABLE),
                CardAccount.provider != "manual",
                Company.status == CompanyStatus.ACTIVE,
            )
        )
    )


def _sync_account(
    db: Session, account: CardAccount, provider: CardProvider, now: datetime
) -> tuple[int, int, int]:
    """Returns (seen, new, failed). A transaction that cannot be processed is logged and counted,
    never allowed to block the rest; the high-water mark then stays put so it is read again."""
    high_water = _parse_cursor(account.sync_cursor)
    since = (high_water or account.created_at) - SYNC_OVERLAP
    cursor: str | None = None
    seen = new = failed = 0
    newest: datetime | None = None
    complete = False
    for _ in range(MAX_PAGES):
        items, cursor = provider.list_transactions(since=since, cursor=cursor)
        for txn in items:
            seen += 1
            try:
                new += 1 if _ingest(db, account, provider, txn, now) else 0
            except Exception:
                failed += 1
                logger.exception(
                    "card transaction %s of account %s could not be processed",
                    txn.provider_txn_id,
                    account.id,
                )
            if newest is None or txn.occurred_at > newest:
                newest = txn.occurred_at
        if cursor is None:
            complete = True
            break
    if not complete:
        logger.warning("card sync of account %s stopped at %s pages", account.id, MAX_PAGES)
    _close_used_cards(db, account, provider, now)
    if (
        complete
        and not failed
        and newest is not None
        and (high_water is None or newest > high_water)
    ):
        account.sync_cursor = newest.isoformat()
    account.last_synced_at = now
    if failed:
        return seen, new, failed
    if account.last_error is not None:
        account.last_error = None
        if account.status == CardAccountStatus.ATTENTION:
            account.status = CardAccountStatus.ACTIVE.value
            audit.record(
                db,
                company_id=account.company_id,
                principal=None,
                action="card_account.recovered",
                target_type="card_account",
                target_id=account.id,
                summary=f"Card account {account.label} is syncing again",
            )
    alerts.resolve_by_key(db, account.company_id, _sync_key(account.id))
    if new:
        events.emit(db, account.company_id, "wallet.updated", {"card_account_id": account.id})
    return seen, new, 0


def _record_failure(
    db: Session, company_id: str, account_id: str, message: str, now: datetime
) -> None:
    try:
        account = db.get(CardAccount, account_id)
        if account is None or account.company_id != company_id:
            return
        first = account.last_error is None
        account.last_error = message[:500]
        if account.status == CardAccountStatus.ACTIVE:
            account.status = CardAccountStatus.ATTENTION.value
        if first:
            audit.record(
                db,
                company_id=company_id,
                principal=None,
                action="card_account.sync_failed",
                target_type="card_account",
                target_id=account.id,
                summary=f"Syncing card account {account.label} failed",
                data={"provider": account.provider},
            )
        alerts.raise_alert(
            db,
            company_id,
            kind="card_sync_failed",
            severity=AlertSeverity.WARNING,
            title=f"Cannot read transactions of card account {account.label}",
            body=message[:500],
            related_type="card_account",
            related_id=account.id,
            dedupe_key=_sync_key(account.id),
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("could not record the sync failure of account %s", account_id)


# ---------------------------------------------------------------------- one transaction


def _ingest(
    db: Session, account: CardAccount, provider: CardProvider, t: CardTxn, now: datetime
) -> bool:
    """Upsert one provider transaction and apply its effects once. Returns True when new."""
    company_id = account.company_id
    card = db.scalar(
        select(AgentCard).where(
            AgentCard.company_id == company_id,
            AgentCard.card_account_id == account.id,
            AgentCard.provider_card_id == t.provider_card_id,
        )
    )
    row = db.scalar(
        select(CardTransaction).where(
            CardTransaction.card_account_id == account.id,
            CardTransaction.provider_txn_id == t.provider_txn_id,
        )
    )
    try:
        with db.begin_nested():
            is_new = row is None
            previous = None if row is None else (row.kind, row.status)
            if row is None:
                row = CardTransaction(
                    company_id=company_id,
                    card_account_id=account.id,
                    provider_txn_id=t.provider_txn_id,
                    match_status=CardTxnMatch.UNMATCHED.value,
                    source=CardTxnSource.PROVIDER.value,
                    created_at=now,
                    occurred_at=t.occurred_at,
                    kind=t.kind,
                    status=t.status,
                    amount_micros=t.amount_micros,
                )
                db.add(row)
            _copy(row, t, card)
            db.flush()
            fresh = is_new or previous != (t.kind, t.status)
            if fresh or _owes_expense(db, row):
                _apply(db, account, provider, card, row, t, now, fresh=fresh)
    except IntegrityError:
        logger.info("transaction %s was recorded concurrently", t.provider_txn_id)
        return False
    return is_new


def _copy(row: CardTransaction, t: CardTxn, card: AgentCard | None) -> None:
    row.card_id = card.id if card else None
    row.kind = t.kind
    row.status = t.status
    row.amount_micros = t.amount_micros
    row.merchant_amount_minor = t.merchant_amount_minor
    row.merchant_currency = t.merchant_currency
    row.merchant_name = t.merchant_name
    row.merchant_mcc = t.merchant_mcc
    row.merchant_country = t.merchant_country
    row.occurred_at = t.occurred_at
    row.settled_at = t.settled_at
    # Only mapped, non-sensitive fields: a provider object never carries a card number, and this
    # is what the UI may show.
    row.raw = {
        "provider_txn_id": t.provider_txn_id,
        "provider_card_id": t.provider_card_id,
        "kind": t.kind,
        "status": t.status,
        "amount_micros": t.amount_micros,
        "merchant_name": t.merchant_name,
        "merchant_mcc": t.merchant_mcc,
        "merchant_country": t.merchant_country,
        "decline_reason": t.decline_reason,
    }


def _owes_expense(db: Session, row: CardTransaction) -> bool:
    """A settled capture or refund that has no expense yet (a crash between the two)."""
    if row.kind not in (CardTxnKind.CAPTURE, CardTxnKind.REFUND) or row.status != "settled":
        return False
    return (
        db.scalar(
            select(ExternalExpense.id).where(
                ExternalExpense.company_id == row.company_id,
                ExternalExpense.card_transaction_id == row.id,
            )
        )
        is None
    )


def _apply(
    db: Session,
    account: CardAccount,
    provider: CardProvider,
    card: AgentCard | None,
    row: CardTransaction,
    t: CardTxn,
    now: datetime,
    *,
    fresh: bool,
) -> None:
    company_id = account.company_id
    if card is None:
        # A card Foundry did not issue (the owner's own card on the same provider account): shown
        # in the ledger as unmatched, never an expense, never frozen.
        row.match_status = CardTxnMatch.UNMATCHED.value
        if fresh and t.kind in (CardTxnKind.AUTHORIZATION, CardTxnKind.CAPTURE):
            alerts.raise_alert(
                db,
                company_id,
                kind="card_unknown_card",
                severity=AlertSeverity.INFO,
                title="A card Foundry did not issue was used",
                body=f"{_money(t.amount_micros)} at {t.merchant_name or 'an unknown merchant'} "
                f"on {account.label}. It is not one of the agent cards, so nothing was changed.",
                related_type="card_account",
                related_id=account.id,
                dedupe_key=f"card_unknown:{account.id}:{t.provider_card_id}",
            )
        return

    purchase = _load(db, PurchaseRequest, company_id, card.purchase_request_id)
    sub = _load(db, Subscription, company_id, card.subscription_id)
    row.purchase_request_id = purchase.id if purchase else None
    row.subscription_id = sub.id if sub else None

    if purchase is None and sub is None:
        _unmatched_on_our_card(db, provider, card, row, t, now, fresh=fresh)
        return

    row.match_status = CardTxnMatch.MATCHED.value
    if (
        fresh
        and t.kind in (CardTxnKind.AUTHORIZATION, CardTxnKind.CAPTURE)
        and not merchant_matches(card, purchase, sub, t)
    ):
        row.match_status = CardTxnMatch.MISMATCH.value
        _anomaly(
            db,
            provider,
            card,
            now,
            kind="card_merchant_mismatch",
            reason="merchant_mismatch",
            title="A card was used at a merchant that does not match the approved vendor",
            body=f"{_money(t.amount_micros)} at {t.merchant_name or 'an unknown merchant'}; "
            f"approved vendor: {(purchase or sub).vendor}. The card was frozen.",  # type: ignore[union-attr]
        )

    if t.kind == CardTxnKind.AUTHORIZATION and t.status == CardTxnStatus.PENDING:
        if fresh and purchase is not None:
            _on_authorization(db, purchase, now)
    elif t.kind == CardTxnKind.CAPTURE and t.status == CardTxnStatus.SETTLED:
        _on_capture(db, account, provider, card, purchase, sub, row, now)
    elif t.kind == CardTxnKind.REFUND and t.status == CardTxnStatus.SETTLED:
        _on_refund(db, card, purchase, sub, row, now)
    elif t.kind == CardTxnKind.DECLINE and fresh:
        _on_decline(db, account, card, purchase, sub, row, t, now)


def _unmatched_on_our_card(
    db: Session,
    provider: CardProvider,
    card: AgentCard,
    row: CardTransaction,
    t: CardTxn,
    now: datetime,
    *,
    fresh: bool,
) -> None:
    row.match_status = CardTxnMatch.UNMATCHED.value
    if t.kind == CardTxnKind.CAPTURE and t.status == CardTxnStatus.SETTLED:
        ensure_expense(db, row, None, None, now)  # the money left: the ledger must say so
    if fresh and t.kind in (CardTxnKind.AUTHORIZATION, CardTxnKind.CAPTURE):
        _anomaly(
            db,
            provider,
            card,
            now,
            kind="card_unmatched_transaction",
            reason="no_matching_request",
            title="An agent card was used with no purchase request behind it",
            body=f"{_money(t.amount_micros)} at {t.merchant_name or 'an unknown merchant'}. "
            "The card was frozen; check the provider dashboard.",
        )


# ---------------------------------------------------------------------- effects


def _on_authorization(db: Session, purchase: PurchaseRequest, now: datetime) -> None:
    if purchase.status not in (
        PurchaseStatus.CARD_ISSUED,
        PurchaseStatus.AWAITING_CHECKOUT,
        PurchaseStatus.ISSUING,
        PurchaseStatus.APPROVED,
    ):
        return
    purchase.status = PurchaseStatus.AUTHORIZED.value
    purchase.updated_at = now
    audit.record(
        db,
        company_id=purchase.company_id,
        principal=None,
        action="purchase.authorized",
        target_type="purchase_request",
        target_id=purchase.id,
        summary=f"Payment for '{purchase.item}' was authorized by the card issuer",
        data={"vendor_domain": purchase.vendor_domain},
    )
    events.emit(db, purchase.company_id, "purchase.updated", {"purchase_request_id": purchase.id})
    _complete_checkout_task(db, purchase, now)
    wakes.wake_agent(
        db,
        purchase.company_id,
        purchase.agent_id,
        "purchase",
        f"Your purchase '{purchase.item}' was authorized. It settles when the vendor captures it.",
        ref_type="purchase_request",
        ref_id=purchase.id,
        now=now,
    )


def _on_capture(
    db: Session,
    account: CardAccount,
    provider: CardProvider,
    card: AgentCard,
    purchase: PurchaseRequest | None,
    sub: Subscription | None,
    row: CardTransaction,
    now: datetime,
) -> None:
    created = ensure_expense(db, row, purchase, sub, now)
    if not created:
        return  # already recorded: replay-safe
    charged_on = (row.settled_at or row.occurred_at).date()
    amount = row.amount_micros
    renewal = sub is not None and (purchase is None or purchase.status == PurchaseStatus.SETTLED)
    if renewal:
        assert sub is not None
        ceiling = sub.amount_micros + tolerance_micros(db, sub.company_id, sub.amount_micros)
        if sub.status == SubscriptionStatus.CANCELLED:
            _anomaly(
                db,
                provider,
                card,
                now,
                kind="subscription_charged_after_cancel",
                reason="charged_after_cancellation",
                title=f"{sub.vendor} charged a subscription that was marked cancelled",
                body=f"{_money(amount)} was charged. Ask the vendor for a refund and make sure "
                "it is really cancelled on their site. The card was frozen.",
            )
            return
        if amount > ceiling:
            _over_approved(db, provider, card, row, ceiling, now)
        subscriptions.record_charge(
            db, sub, amount_micros=amount, charged_on=charged_on, card=card, now=now
        )
        return
    assert purchase is not None
    already = purchase.settled_micros or 0
    ceiling = purchase.amount_micros + tolerance_micros(
        db, purchase.company_id, purchase.amount_micros
    )
    if already + amount > ceiling:
        _over_approved(db, provider, card, row, ceiling, now)
    was_settled = purchase.status == PurchaseStatus.SETTLED
    purchase.settled_micros = already + amount
    purchase.cost_kind = "verified"
    purchase.held_micros = 0
    purchase.status = PurchaseStatus.SETTLED.value
    purchase.updated_at = now
    if not was_settled:
        audit.record(
            db,
            company_id=purchase.company_id,
            principal=None,
            action="purchase.settled",
            target_type="purchase_request",
            target_id=purchase.id,
            summary=f"Purchase '{purchase.item}' settled: {_money(amount)}",
            data={"settled_micros": purchase.settled_micros, "cost_kind": "verified"},
        )
        _complete_checkout_task(db, purchase, now)
        wakes.wake_agent(
            db,
            purchase.company_id,
            purchase.agent_id,
            "purchase",
            f"Your purchase '{purchase.item}' settled for {_money(amount)}. Continue the work "
            "that needed it.",
            ref_type="purchase_request",
            ref_id=purchase.id,
            now=now,
        )
    elif card.kind == CardKind.SINGLE_USE:
        _anomaly(
            db,
            provider,
            card,
            now,
            kind="card_second_charge",
            reason="second_charge",
            title="A single-use card was charged more than once",
            body=f"A second charge of {_money(amount)} hit the card for '{purchase.item}'. "
            "The card was frozen.",
        )
    events.emit(db, purchase.company_id, "purchase.updated", {"purchase_request_id": purchase.id})
    if purchase.recurrence != Recurrence.ONE_TIME and purchase.subscription_id is None:
        created_sub = subscriptions.create_from_settled(
            db, purchase, charged_on=charged_on, charged_micros=amount, card=card, now=now
        )
        row.subscription_id = created_sub.id
    # A single-use card is closed by the sweep at the end of the sync (``_close_used_cards``), which
    # skips a card an anomaly just froze: that one stays frozen for a person to look at.


def _on_refund(
    db: Session,
    card: AgentCard,
    purchase: PurchaseRequest | None,
    sub: Subscription | None,
    row: CardTransaction,
    now: datetime,
) -> None:
    if not ensure_expense(db, row, purchase, sub, now):
        return
    if purchase is not None and purchase.settled_micros is not None:
        purchase.settled_micros = purchase.settled_micros + row.amount_micros  # negative
        purchase.updated_at = now
        events.emit(
            db, purchase.company_id, "purchase.updated", {"purchase_request_id": purchase.id}
        )
    audit.record(
        db,
        company_id=row.company_id,
        principal=None,
        action="card.refund_recorded",
        target_type="card_transaction",
        target_id=row.id,
        summary=f"Refund of {_money(abs(row.amount_micros))} recorded",
        data={"purchase_request_id": row.purchase_request_id},
    )


def _on_decline(
    db: Session,
    account: CardAccount,
    card: AgentCard,
    purchase: PurchaseRequest | None,
    sub: Subscription | None,
    row: CardTransaction,
    t: CardTxn,
    now: datetime,
) -> None:
    reason = t.decline_reason or "declined"
    company_id = account.company_id
    if reason == "card_inactive":
        return  # somebody tried a card we froze or closed: the freeze did its job
    if sub is not None and reason == "spending_limit":
        ceiling = sub.amount_micros + tolerance_micros(db, sub.company_id, sub.amount_micros)
        if t.amount_micros > ceiling:
            subscriptions.record_price_increase(db, sub, new_amount_micros=t.amount_micros, now=now)
            return
    if reason == "insufficient_funds":
        alerts.raise_alert(
            db,
            company_id,
            kind="wallet_low_balance",
            severity=AlertSeverity.CRITICAL,
            title="An agent card was declined: the account balance is too low",
            body=f"{_money(t.amount_micros)} at {t.merchant_name or 'a merchant'} was declined. "
            f"Add funds to {account.label}: Wallet > Setup shows the exact steps.",
            related_type="card_account",
            related_id=account.id,
            dedupe_key=_low_balance_key(account.id),
        )
    else:
        alerts.raise_alert(
            db,
            company_id,
            kind="card_declined",
            severity=AlertSeverity.WARNING,
            title="An agent card payment was declined",
            body=f"{_money(t.amount_micros)} at {t.merchant_name or 'a merchant'} "
            f"({reason.replace('_', ' ')}).",
            related_type="agent_card",
            related_id=card.id,
            dedupe_key=f"card_declined:{card.id}",
        )
    if purchase is not None:
        wakes.wake_agent(
            db,
            company_id,
            purchase.agent_id,
            "purchase",
            f"The payment for '{purchase.item}' was declined ({reason.replace('_', ' ')}). "
            "The Founder has been notified; do not retry it yourself.",
            ref_type="purchase_request",
            ref_id=purchase.id,
            now=now,
        )


def _over_approved(
    db: Session,
    provider: CardProvider,
    card: AgentCard,
    row: CardTransaction,
    ceiling: int,
    now: datetime,
) -> None:
    row.match_status = CardTxnMatch.MISMATCH.value
    _anomaly(
        db,
        provider,
        card,
        now,
        kind="card_over_approved",
        reason="over_approved_amount",
        title="A card was charged more than the approved amount",
        body=f"Charged {_money(row.amount_micros)}; approved up to {_money(ceiling)}. "
        "The card was frozen.",
    )


# ---------------------------------------------------------------------- cards


def _anomaly(
    db: Session,
    provider: CardProvider,
    card: AgentCard,
    now: datetime,
    *,
    kind: str,
    reason: str,
    title: str,
    body: str,
) -> None:
    """Freeze the card (best effort) and tell the Founder, critically."""
    frozen = freeze_card(db, provider, card, reason, now)
    alerts.raise_alert(
        db,
        card.company_id,
        kind=kind,
        severity=AlertSeverity.CRITICAL,
        title=title,
        body=body + ("" if frozen else " Foundry could NOT freeze it: freeze it at the provider."),
        related_type="agent_card",
        related_id=card.id,
        dedupe_key=f"{kind}:{card.id}",
    )


def freeze_card(
    db: Session, provider: CardProvider, card: AgentCard, reason: str, now: datetime
) -> bool:
    """Freeze at the provider, then in our records. Returns False when the provider refused."""
    if card.status in (CardStatus.FROZEN, CardStatus.CLOSED):
        return True
    try:
        provider.freeze(card.provider_card_id)
    except Exception as exc:
        logger.warning("could not freeze card %s: %s", card.id, _safe(exc))
        audit.record(
            db,
            company_id=card.company_id,
            principal=None,
            action="card.freeze_failed",
            target_type="agent_card",
            target_id=card.id,
            summary="Freezing a card failed at the provider",
            data={"reason": reason},
        )
        return False
    card.status = CardStatus.FROZEN.value
    card.frozen_reason = reason[:64]
    card.updated_at = now
    audit.record(
        db,
        company_id=card.company_id,
        principal=None,
        action="card.frozen",
        target_type="agent_card",
        target_id=card.id,
        summary=f"Card ••{card.last4} frozen: {reason.replace('_', ' ')}",
        data={"reason": reason, "last4": card.last4},
    )
    events.emit(db, card.company_id, "wallet.updated", {"card_id": card.id})
    return True


def close_card(
    db: Session, provider: CardProvider, card: AgentCard, why: str, now: datetime
) -> bool:
    """Close at the provider, then in our records. A failure leaves the card as it was; the sweep
    retries it on the next sync."""
    if card.status in (CardStatus.CLOSED, CardStatus.EXPIRED):
        return True
    try:
        provider.close(card.provider_card_id)
    except Exception as exc:
        logger.warning("could not close card %s: %s", card.id, _safe(exc))
        return False
    card.status = CardStatus.CLOSED.value
    card.closed_at = now
    card.updated_at = now
    audit.record(
        db,
        company_id=card.company_id,
        principal=None,
        action="card.closed",
        target_type="agent_card",
        target_id=card.id,
        summary=f"Card ••{card.last4} closed ({why})",
        data={"why": why, "last4": card.last4},
    )
    events.emit(db, card.company_id, "wallet.updated", {"card_id": card.id})
    return True


def _close_used_cards(
    db: Session, account: CardAccount, provider: CardProvider, now: datetime
) -> None:
    """Single-use cards whose purchase settled but are still open (a close that failed before, or
    a provider that does not cancel them itself)."""
    rows = db.execute(
        select(AgentCard, PurchaseRequest)
        .join(PurchaseRequest, PurchaseRequest.id == AgentCard.purchase_request_id)
        .where(
            AgentCard.company_id == account.company_id,
            AgentCard.card_account_id == account.id,
            AgentCard.kind == CardKind.SINGLE_USE.value,
            AgentCard.status.in_([CardStatus.ACTIVE.value]),
            PurchaseRequest.status == PurchaseStatus.SETTLED.value,
        )
    ).all()
    for card, _purchase in rows:
        close_card(db, provider, card, "used", now)


# ---------------------------------------------------------------------- ledger


def ensure_expense(
    db: Session,
    row: CardTransaction,
    purchase: PurchaseRequest | None,
    sub: Subscription | None,
    now: datetime,
) -> bool:
    """Record the settled transaction as a ``verified`` expense, once. Every charge is its own
    ``one_time`` row (a recurring row would make finance project it again). Returns True when the
    expense was created now."""
    existing = db.scalar(
        select(ExternalExpense.id).where(
            ExternalExpense.company_id == row.company_id,
            ExternalExpense.card_transaction_id == row.id,
        )
    )
    if existing is not None:
        return False
    owner = purchase or sub
    vendor = (owner.vendor if owner else row.merchant_name) or "Unknown merchant"
    description = owner.item if owner else "Card transaction (no purchase request)"
    if row.amount_micros < 0:
        description = f"Refund: {description}"
    expense = ExternalExpense(
        company_id=row.company_id,
        vendor=vendor[:200],
        description=description[:500],
        amount_micros=row.amount_micros,
        recurrence="one_time",
        project_id=purchase.project_id if purchase else None,
        incurred_on=(row.settled_at or row.occurred_at).date(),
        created_by_type=ActorType.SYSTEM.value,
        created_by_id=None,
        cost_kind="verified",
        source="card",
        purchase_request_id=(
            purchase.id if purchase else (sub.purchase_request_id if sub else None)
        ),
        card_transaction_id=row.id,
    )
    db.add(expense)
    db.flush()
    audit.record(
        db,
        company_id=row.company_id,
        principal=None,
        action="expense.card_recorded",
        target_type="external_expense",
        target_id=expense.id,
        summary=f"Recorded {_money(row.amount_micros)} at {vendor[:80]} (verified, card)",
        data={
            "card_transaction_id": row.id,
            "purchase_request_id": expense.purchase_request_id,
            "cost_kind": "verified",
        },
    )
    return True


def _complete_checkout_task(db: Session, purchase: PurchaseRequest, now: datetime) -> None:
    """The payment happened: the human's checkout task is done. Best effort, never fatal."""
    if not purchase.checkout_task_id:
        return
    task = db.get(Task, purchase.checkout_task_id)
    if task is None or task.company_id != purchase.company_id:
        return
    if task.status not in (TaskStatus.READY, TaskStatus.IN_PROGRESS):
        return
    try:
        with db.begin_nested():
            if task.status == TaskStatus.READY:
                task.attempt_count += 1
                tasks.transition(
                    db,
                    task,
                    TaskStatus.IN_PROGRESS,
                    principal=None,
                    reason="payment seen at the card issuer",
                    now=now,
                )
            tasks.finish_success(
                db,
                task,
                summary="Payment authorized: Foundry saw it at the card issuer.",
                output_refs=None,
                principal=None,
                run_id=None,
                now=now,
            )
    except Exception:
        logger.exception("could not complete checkout task %s", task.id)


# ---------------------------------------------------------------------- matching


def merchant_matches(
    card: AgentCard,
    purchase: PurchaseRequest | None,
    sub: Subscription | None,
    t: CardTxn,
) -> bool:
    """Does the merchant look like the approved vendor? A heuristic that errs toward matching:
    a missing merchant name never freezes, and any shared word, the vendor's domain label or the
    vendor name inside the merchant string is a match. An explicit MCC allow-list is exact."""
    lock = card.merchant_lock or {}
    allow = [str(c) for c in (lock.get("mcc_allow") or [])]
    # Stripe locks by category NAME, which a numeric MCC on a transaction cannot be compared to.
    if allow and all(c.isdigit() for c in allow) and t.merchant_mcc not in (None, *allow):
        return False
    if not t.merchant_name:
        return True
    owner = purchase or sub
    names = [lock.get("name"), owner.vendor if owner else None]
    domains = [lock.get("domain"), owner.vendor_domain if owner else None]
    candidates = {_norm(str(n)) for n in names if n} | {_domain_label(str(d)) for d in domains if d}
    candidates.discard("")
    if not candidates:
        return True
    merchant = _norm(t.merchant_name)
    merchant_tokens = {w for w in _words(t.merchant_name) if len(w) >= MISMATCH_MIN_TOKEN}
    for cand in candidates:
        if cand in merchant or (len(merchant) >= MISMATCH_MIN_TOKEN and merchant in cand):
            return True
    for n in names:
        if n and merchant_tokens & {w for w in _words(str(n)) if len(w) >= MISMATCH_MIN_TOKEN}:
            return True
    return False


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _domain_label(domain: str) -> str:
    host = domain.lower().split("//")[-1].split("/")[0].removeprefix("www.")
    parts = host.split(".")
    return _norm(parts[-2] if len(parts) >= 2 else parts[0])


# ---------------------------------------------------------------------- small helpers


def _load(db: Session, model, company_id: str, row_id: str | None):
    if not row_id:
        return None
    row = db.get(model, row_id)
    return row if row is not None and row.company_id == company_id else None


def _parse_cursor(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _sync_key(account_id: str) -> str:
    return f"card_sync_failed:{account_id}"


def _low_balance_key(account_id: str) -> str:
    return f"wallet_low_balance:{account_id}"


def _low_balance_body(account: CardAccount, available: int, required: int) -> str:
    return (
        f"{account.label} has {_money(available)} available; open purchases, renewals due in the "
        f"next {RENEWAL_LOOKAHEAD_DAYS} days and a buffer need about {_money(required)}. "
        "Wallet > Setup shows the exact steps to add funds."
    )


def _money(micros: int) -> str:
    cents = abs(micros) // 10_000
    return f"{'-' if micros < 0 else ''}${cents // 100:,}.{cents % 100:02d}"


def _safe(exc: Exception) -> str:
    """An error message that is safe to store: providers mask their secrets, and ``scrub`` removes
    anything key- or card-number-shaped that slipped through."""
    from app.services.secrets_scan import scrub

    text = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return (scrub(text) or "")[:500]


__all__ = [
    "check_balances",
    "close_card",
    "ensure_expense",
    "freeze_card",
    "merchant_matches",
    "required_micros",
    "run_all",
    "sync_company",
    "tolerance_micros",
]
