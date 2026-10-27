"""Purchase requests: validation, the deterministic band decision, the approval, the issuance job,
mark-paid and cancel (docs/specs/m15, sections 4.4 and 4.5).

State machine::

    requested -> approved (auto) ------------------------------------------+
        |-> pending_approval -> approved (Co-founder / human) --------------+
        |        |-> rejected (note to the agent) / expired (7 days)        |
        |-> human_task (purchasing off or no account: a person's task)      v
        |-> rejected (denied: pause, emergency, budget or agent cap)   issuing -> card_issued
                                                                              -> awaiting_checkout
    awaiting_checkout -> authorized -> settled   (reconciliation, or a person's "mark paid")
    any open status   -> cancelled (a person) / expired (card TTL)

What is code here and never a prompt: an agent can only request; the band is computed from the
policy; an approval is bound to the exact scope and spent once; the card limit comes from the
approved amount; a request can never fall back to another provider or account; nothing here can
read, store or show a card number; every state change writes an audit event in its transaction.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    EmergencyStopped,
    Forbidden,
    InvalidTransition,
    NotFound,
    ValidationFailed,
)
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.guardrails import log as guard_log
from app.guardrails import registry, rules
from app.integrations.cards.base import CardProvider, CardProviderError, MerchantLock
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company, CompanyMember
from app.models.enums import (
    PURCHASE_HOLD_STATUSES,
    TERMINAL_PURCHASE_STATUSES,
    AgentKind,
    AgentStatus,
    AlertSeverity,
    ApprovalStatus,
    Approver,
    CardKind,
    CardStatus,
    CardTxnKind,
    CardTxnMatch,
    CardTxnSource,
    CardTxnStatus,
    CheckoutPath,
    MemberRole,
    MemberStatus,
    PurchaseDecision,
    PurchaseStatus,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.purchasing import AgentCard, CardAccount, CardTransaction, PurchaseRequest
from app.models.reachability import Mailbox
from app.models.work import Task
from app.schemas.purchasing import (
    AgentCardOut,
    CardTransactionOut,
    PurchaseAck,
    PurchaseApprovalSummary,
    PurchaseCancelRequest,
    PurchaseDecisionRequest,
    PurchaseMarkPaid,
    PurchaseRequestCreate,
    PurchaseRequestDetail,
    PurchaseRequestOut,
)
from app.schemas.work import TaskCreate
from app.services import (
    alerts,
    approvals,
    audit,
    emergency,
    events,
    ledger,
    policy,
    secrets_scan,
    wakes,
    wallet,
)
from app.services.common import get_agent_or_404, get_task_or_404
from app.services.pagination import paginate

logger = logging.getLogger("company_os.purchasing")

APPROVAL_TTL = timedelta(days=7)
DEDUPE_WINDOW = timedelta(hours=24)
ISSUING_STALE = timedelta(minutes=5)
ISSUE_MAX_ATTEMPTS = 5
ISSUE_BACKOFF_SECONDS = (60, 300, 900, 3600, 7200)
_INTERNAL_TLDS = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")
_OPEN_BEFORE_CHECKOUT = (
    PurchaseStatus.PENDING_APPROVAL,
    PurchaseStatus.APPROVED,
    PurchaseStatus.ISSUING,
    PurchaseStatus.ISSUE_FAILED,
    PurchaseStatus.CARD_ISSUED,
    PurchaseStatus.AWAITING_CHECKOUT,
)
_CHECKOUT_OPEN = (
    PurchaseStatus.CARD_ISSUED,
    PurchaseStatus.AWAITING_CHECKOUT,
    PurchaseStatus.AUTHORIZED,
)
_DECISION_OF = {
    rules.D.ALLOW: PurchaseDecision.AUTO,
    rules.D.PEER_REVIEW: PurchaseDecision.COFOUNDER,  # purchases have no peer review
    rules.D.COFOUNDER_APPROVAL: PurchaseDecision.COFOUNDER,
    rules.D.HUMAN_APPROVAL: PurchaseDecision.HUMAN,
}
_GUARD_DECISION = {
    "auto": "allow",
    "cofounder": "cofounder_approval",
    "human": "human_approval",
    "report_only": "human_approval",
    "deny": "deny",
}


@dataclass(frozen=True)
class BandDecision:
    """Result of the deterministic policy (section 4.4.2): who must approve and why."""

    decision: str  # auto | cofounder | human | report_only | deny
    reason: str
    card_limit_micros: int  # amount + max(bps of amount, minimum): room for tax and FX


# ---------------------------------------------------------------- small helpers


def _clip(text: str | None, n: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:,.2f}"


def usd_to_micros(amount: Decimal) -> int:
    """Decimal dollars to integer micro-USD. No floats anywhere on the money path."""
    return int((amount * Decimal(1_000_000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def card_limit_micros(limits, amount_micros: int) -> int:
    """The limit of the card: the approved amount plus room for tax and FX (never less than the
    minimum room, never more than the percentage when that is larger)."""
    room = max(
        amount_micros * limits.purchase_tolerance_bps // 10_000,
        limits.purchase_tolerance_min_micros,
    )
    return amount_micros + room


def vendor_domain_of(url: str) -> str:
    """The host the human is sent to. The server never fetches it, so there is no DNS lookup; the
    host is only refused when it is plainly internal (an agent must not point a person at the
    company's own network or at a metadata address)."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    bad = ValidationFailed(
        "The url must point to the vendor's public website",
        details=[{"loc": ["body", "url"], "msg": "not a public https address"}],
    )
    if not host or host == "localhost" or host.endswith(_INTERNAL_TLDS):
        raise bad
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or not re.fullmatch(r"[a-z0-9.\-]+", host):
            # IDN hosts arrive here as punycode; anything else odd is refused
            raise bad from None
    else:
        if not address.is_global:
            raise bad
    return host.removeprefix("www.")


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _halted(db: Session, company_id: str) -> str | None:
    return wallet.company_halted(db, company_id)


def _month_of(db: Session, company_id: str, now: datetime) -> str:
    return wallet._month_period(db, company_id, now)


def _committed_micros(
    db: Session,
    company_id: str,
    month: str,
    *,
    agent_id: str | None = None,
    decision: str | None = None,
) -> int:
    """Held (approved, not yet charged) plus settled this month, for the company or one agent.
    A hold is a commitment, never presented as a charge."""
    held = case(
        (
            PurchaseRequest.status.in_([s.value for s in PURCHASE_HOLD_STATUSES]),
            PurchaseRequest.held_micros,
        ),
        else_=0,
    )
    settled = case(
        (
            (PurchaseRequest.status == PurchaseStatus.SETTLED)
            & (PurchaseRequest.period_month == month),
            func.coalesce(PurchaseRequest.settled_micros, 0),
        ),
        else_=0,
    )
    stmt = select(func.coalesce(func.sum(held + settled), 0)).where(
        PurchaseRequest.company_id == company_id
    )
    if agent_id:
        stmt = stmt.where(PurchaseRequest.agent_id == agent_id)
    if decision:
        stmt = stmt.where(PurchaseRequest.decision == decision)
    return int(db.scalar(stmt) or 0)


def _known_vendor(db: Session, company_id: str, domain: str) -> bool:
    """A vendor is known once a purchase from that domain was actually paid or is being paid."""
    return (
        db.scalar(
            select(PurchaseRequest.id)
            .where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequest.vendor_domain == domain,
                PurchaseRequest.status.in_(
                    [PurchaseStatus.AUTHORIZED.value, PurchaseStatus.SETTLED.value]
                ),
            )
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------- the policy decision


def decide_band(
    db: Session,
    company_id: str,
    agent_id: str,
    *,
    amount_micros: int,
    recurrence: str,
    vendor_domain: str,
    now: datetime | None = None,
) -> BandDecision:
    """Deny / human / cofounder / auto from the policy, the monthly totals, the recurring and
    new-vendor floors and the guardrail rules (the strictest wins). Pure reads, no side effects."""
    now = now or utcnow()
    limits = policy.load_policy(db, company_id)
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
    if agent is None:
        raise NotFound("Agent not found")
    card_limit = card_limit_micros(limits, amount_micros)

    def out(decision: str, reason: str) -> BandDecision:
        return BandDecision(decision, reason, card_limit)

    halted = _halted(db, company_id)
    if halted:
        return out("deny", f"No purchase can be started while {halted}")
    month = _month_of(db, company_id, now)
    budget = limits.purchase_monthly_budget_micros
    if budget is not None:
        committed = _committed_micros(db, company_id, month)
        if committed + amount_micros > budget:
            return out(
                "deny",
                f"This would take the month's purchases to {_usd(committed + amount_micros)}, "
                f"over the purchase budget of {_usd(budget)}",
            )
    cap = agent.purchase_monthly_limit_micros
    if cap is not None:
        spent = _committed_micros(db, company_id, month, agent_id=agent.id)
        if spent + amount_micros > cap:
            return out(
                "deny",
                f"This would take {agent.name}'s month to {_usd(spent + amount_micros)}, over "
                f"its purchase limit of {_usd(cap)}",
            )
    if not limits.purchasing_enabled:
        return out("report_only", "Purchasing is not switched on: a person decides and buys")
    if wallet.active_account(db, company_id) is None:
        return out("report_only", "No card account is set up: a person decides and buys")

    band = rules.purchase_band(limits, amount_micros)
    reasons: list[str] = []
    if band == rules.D.ALLOW:
        auto_spent = _committed_micros(
            db, company_id, month, agent_id=agent.id, decision=PurchaseDecision.AUTO.value
        )
        if auto_spent + amount_micros > limits.purchase_agent_auto_monthly_micros:
            band = (
                rules.D.COFOUNDER_APPROVAL
                if limits.purchase_cofounder_limit_micros > 0
                and amount_micros <= limits.purchase_cofounder_limit_micros
                else rules.D.HUMAN_APPROVAL
            )
            reasons.append("the agent's automatic monthly total would be exceeded")
    if band == rules.D.COFOUNDER_APPROVAL:
        approved = _committed_micros(
            db, company_id, month, decision=PurchaseDecision.COFOUNDER.value
        )
        if approved + amount_micros > limits.purchase_cofounder_monthly_micros:
            band = rules.D.HUMAN_APPROVAL
            reasons.append("the Co-founder's monthly approval total would be exceeded")
    if recurrence != "one_time" and limits.purchase_recurring_requires_human:
        band = rules.D.HUMAN_APPROVAL
        reasons.append("a new subscription always goes to a person")
    if limits.purchase_new_vendor_requires_human and not _known_vendor(
        db, company_id, vendor_domain
    ):
        band = rules.D.HUMAN_APPROVAL
        reasons.append(f"{vendor_domain} is a new vendor")
    if agent.kind == AgentKind.FOUNDER:
        band = rules.D.HUMAN_APPROVAL
        reasons.append("the Co-founder cannot approve its own purchase")

    # The guardrail rules can only tighten (docs/specs/m15, section 4.4.2).
    spec = registry.get_action(
        "purchase.make" if recurrence == "one_time" else "subscription.start"
    )
    assert spec is not None
    res = rules.resolve(
        db,
        agent,
        spec,
        limits,
        rules.Facts(amount_micros=amount_micros, domain=vendor_domain, purchasing=True),
        now=now,
    )
    final = rules.strictest(band, res.decision)
    if final == rules.D.DENY:
        return out("deny", res.reason)
    if res.source == "rule" and final != band:
        reasons.append(res.reason)
    why = {
        rules.D.ALLOW: "within the automatic band",
        rules.D.COFOUNDER_APPROVAL: "within the AI Co-founder's band",
        rules.D.HUMAN_APPROVAL: "needs a person",
    }
    reason = why.get(final, "needs a person") + (f" ({'; '.join(reasons)})" if reasons else "")
    return out(_DECISION_OF[final].value, reason)


# ---------------------------------------------------------------- the request


def _check_task_access(db: Session, agent: Agent, task: Task) -> None:
    """The agent buys for a task it owns, or for a project it leads."""
    if task.assignee_agent_id == agent.id:
        return
    if task.project_id:
        from app.models.work import Project

        project = db.get(Project, task.project_id)
        if project is not None and project.lead_agent_id == agent.id:
            return
    raise Forbidden(
        "You can request a purchase only for a task assigned to you or a project you lead"
    )


def _scope_for(p: PurchaseRequest, action: str, limit_micros: int) -> dict[str, Any]:
    """What an approval is bound to: a changed amount, vendor, recurrence or limit is new."""
    return {
        "v": 1,
        "action": action,
        "purchase_request_id": p.id,
        "vendor_domain": p.vendor_domain,
        "amount_micros": p.amount_micros,
        "recurrence": p.recurrence,
        "limit_micros": limit_micros,
        "merchant_lock": {"name": p.vendor, "domain": p.vendor_domain},
    }


def _action_for(p: PurchaseRequest) -> str:
    return "purchase.make" if p.recurrence == "one_time" else "subscription.start"


def _packet(
    p: PurchaseRequest,
    *,
    limit_micros: int | None,
    company_email: str | None = None,
    card: AgentCard | None = None,
    account: CardAccount | None = None,
    keep: dict | None = None,
) -> dict[str, Any]:
    reveal_on = {"stripe_issuing": "Stripe", "mercury": "Mercury"}.get(
        account.provider if account else "", "the card provider"
    )
    steps = ["Check the vendor address shown here before you open it."]
    if card is not None:
        steps += [
            "Choose the plan and quantity.",
            f"Open the card on {reveal_on} (Wallet, Cards, Reveal) to read its number.",
            f"Pay no more than {_usd(limit_micros or p.amount_micros)}.",
            "Come back: the payment is picked up automatically, or mark it paid.",
        ]
    else:
        last4 = f" ending {account.manual_last4}" if account and account.manual_last4 else ""
        steps += [
            "Choose the plan and quantity.",
            f"Pay with the company's own card{last4}.",
            "Come back and mark it paid with the amount you were charged.",
        ]
    data: dict[str, Any] = {
        "vendor": p.vendor,
        "vendor_domain": p.vendor_domain,
        "url": p.url,
        "item": p.item,
        "quantity": p.quantity,
        "amount_micros": p.amount_micros,
        "card_limit_micros": limit_micros,
        "recurrence": p.recurrence,
        "company_email": company_email,
        "bring_back": "Tell the agent the account is ready; it is woken when the purchase is "
        "recorded and carries on with its task.",
        "card_last4": card.last4 if card else (account.manual_last4 if account else None),
        "card_brand": card.brand if card else (account.manual_brand if account else None),
        "card_exp_month": card.exp_month if card else None,
        "card_exp_year": card.exp_year if card else None,
        "steps": steps,
    }
    for key, value in (keep or {}).items():
        if key.startswith("_"):  # private notes (issue retry state, outcome note) survive rewrites
            data[key] = value
    return data


def _company_email(db: Session, company_id: str) -> str | None:
    row = db.scalar(
        select(Mailbox)
        .where(Mailbox.company_id == company_id, Mailbox.status == "active")
        .order_by(
            (func.substr(Mailbox.address, 1, 8) != "company@"), Mailbox.created_at, Mailbox.id
        )
        .limit(1)
    )
    return row.address if row is not None else None


def _checkout_member(db: Session, company_id: str) -> str | None:
    """Who gets the checkout task: the person named in the policy, else the owner."""
    limits = policy.load_policy(db, company_id)
    if limits.purchase_checkout_member_id:
        member = db.get(CompanyMember, limits.purchase_checkout_member_id)
        if (
            member is not None
            and member.company_id == company_id
            and member.status == MemberStatus.ACTIVE
            and member.role
            in (MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value)
        ):
            return member.id
    owner = db.scalar(
        select(CompanyMember)
        .where(
            CompanyMember.company_id == company_id,
            CompanyMember.status == MemberStatus.ACTIVE,
            CompanyMember.role == MemberRole.OWNER.value,
        )
        .order_by(CompanyMember.created_at, CompanyMember.id)
        .limit(1)
    )
    return owner.id if owner is not None else None


def _create_task(
    db: Session, p: PurchaseRequest, title: str, lines: list[str], now: datetime
) -> Task | None:
    member_id = _checkout_member(db, p.company_id)
    if member_id is None:
        return None
    description = "\n".join(lines)
    task, _ = _tasks().create_system_task(
        db,
        p.company_id,
        TaskCreate(
            title=_clip(title, 290),
            description=description[:20_000],
            kind="work",
            priority="high",
            project_id=p.project_id,
            assignee_ref=f"member:{member_id}",
            idempotency_key=f"purchase-checkout:{p.id}",
        ),
        now=now,
    )
    return task


def _tasks():
    from app.services import tasks  # local: tasks depends on approvals -> purchasing

    return tasks


def _task_lines(p: PurchaseRequest, packet: dict[str, Any]) -> list[str]:
    limit = packet.get("card_limit_micros")
    lines = [
        f"{p.vendor} ({p.vendor_domain}): {p.item}, quantity {p.quantity}.",
        f"Amount: {_usd(p.amount_micros)}"
        + (f"; the card allows up to {_usd(limit)}." if limit else "."),
        f"Page the agent gave (check the address, it was written by an agent): {p.url}",
        "",
        "Steps:",
        *(f"{i}. {step}" for i, step in enumerate(packet.get("steps", []), start=1)),
        "",
        f"Why it is needed (agent's words): {_clip(p.justification, 600)}",
        "Open Wallet, Purchases for the full details.",
    ]
    return lines


def _wake(db: Session, p: PurchaseRequest, text: str) -> None:
    wakes.wake_agent(
        db, p.company_id, p.agent_id, "purchase", text, ref_type="purchase_request", ref_id=p.id
    )


def _log_guard(
    db: Session, p: PurchaseRequest, decision: str, reason: str, run_id: str | None
) -> None:
    spec = registry.get_action(_action_for(p))
    guard_log.record(
        db,
        company_id=p.company_id,
        agent_id=p.agent_id,
        run_id=run_id,
        task_id=p.task_id,
        action=_action_for(p),
        tier=spec.tier if spec else "spend",
        decision=_GUARD_DECISION[decision],
        approval_id=p.approval_id,
        payload_digest=hashlib.sha256(
            f"{p.vendor_domain}|{p.amount_micros}|{p.recurrence}".encode()
        ).hexdigest(),
        reason=_clip(reason, 480),
    )


def request_purchase(
    db: Session, ctx: CompanyContext, data: PurchaseRequestCreate, *, run_id: str | None = None
) -> PurchaseRequest:
    """Agent entry (``POST /api/agent/v1/purchases``): validates, dedupes, decides, and either
    approves, parks behind an Approval, or degrades to a human task. Audited in one transaction.
    The provider call (issuing the card) happens after the commit."""
    if not ctx.principal.is_agent:
        raise Forbidden("Only an agent requests a purchase; a person buys directly")
    agent = get_agent_or_404(db, ctx.company_id, ctx.principal.id)
    if agent.status != AgentStatus.ACTIVE:
        raise Conflict(f"An agent that is {agent.status} cannot request purchases")
    if emergency.is_active(db):
        raise EmergencyStopped()
    task = get_task_or_404(db, ctx.company_id, data.task_id)
    if TaskStatus(task.status) in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise Conflict("That task is finished; request purchases for open work")
    _check_task_access(db, agent, task)
    domain = vendor_domain_of(data.url)
    amount = usd_to_micros(data.amount_usd)
    if (data.original_amount is None) != (data.original_currency is None):
        raise ValidationFailed("original_amount and original_currency go together")
    original_minor = (
        int((data.original_amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if data.original_amount is not None
        else None
    )
    recurrence = "one_time" if data.recurring == "none" else data.recurring
    now = utcnow()
    prefix = hashlib.sha256(
        f"{agent.id}|{_normal(data.vendor)}|{_normal(data.item)}".encode()
    ).hexdigest()[:32]
    existing = db.scalar(
        select(PurchaseRequest)
        .where(
            PurchaseRequest.company_id == ctx.company_id,
            PurchaseRequest.dedupe_key.like(f"{prefix}:%"),
            PurchaseRequest.created_at >= now - DEDUPE_WINDOW,
        )
        .order_by(PurchaseRequest.created_at.desc())
        .limit(1)
    )
    if existing is not None:  # the same request again: the same answer, nothing new
        return existing

    ledger.acquire_budget_lock(db, ctx.company_id)  # serialize the monthly totals (D9)
    band = decide_band(
        db,
        ctx.company_id,
        agent.id,
        amount_micros=amount,
        recurrence=recurrence,
        vendor_domain=domain,
        now=now,
    )
    row = PurchaseRequest(
        company_id=ctx.company_id,
        agent_id=agent.id,
        run_id=run_id,
        task_id=task.id,
        project_id=task.project_id,
        item=data.item,
        vendor=data.vendor,
        vendor_domain=domain,
        url=data.url,
        quantity=data.quantity,
        amount_micros=amount,
        original_amount_minor=original_minor,
        original_currency=data.original_currency.upper() if data.original_currency else None,
        recurrence=recurrence,
        justification=data.justification,
        alternatives=data.alternatives_considered,
        status=PurchaseStatus.REQUESTED.value,
        held_micros=0,
        dedupe_key=f"{prefix}:{hashlib.sha256(str(now.timestamp()).encode()).hexdigest()[:12]}",
        period_month=_month_of(db, ctx.company_id, now),
        checkout_packet={},
    )
    db.add(row)
    db.flush()
    row.checkout_packet = _packet(row, limit_micros=band.card_limit_micros)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="purchase.requested",
        target_type="purchase_request",
        target_id=row.id,
        summary=f"{agent.name} asked to buy {_clip(row.item, 80)} from {row.vendor_domain} "
        f"for {_usd(amount)}",
        data={
            "amount_micros": amount,
            "recurrence": recurrence,
            "vendor_domain": domain,
            "decision": band.decision,
            "task_id": task.id,
        },
    )
    events.emit(db, ctx.company_id, "purchase.updated", {"purchase_request_id": row.id})
    _log_guard(db, row, band.decision, band.reason, run_id)
    _apply_band(db, row, agent, task, band, ctx.principal, now)
    db.commit()
    if row.status == PurchaseStatus.APPROVED:
        _safe_issue(db, row.id)
        db.refresh(row)
    return row


def _apply_band(
    db: Session,
    row: PurchaseRequest,
    agent: Agent,
    task: Task,
    band: BandDecision,
    principal: Principal,
    now: datetime,
) -> None:
    if band.decision == "deny":
        _finish_without_purchase(db, row, PurchaseStatus.REJECTED, band.reason)
        alerts.raise_alert(
            db,
            row.company_id,
            kind="purchase_denied",
            severity=AlertSeverity.WARNING,
            title=f"A purchase was refused: {_clip(row.item, 80)}",
            body=band.reason,
            related_type="purchase_request",
            related_id=row.id,
            dedupe_key=f"purchase_denied:{row.id}",
            wake_founder=False,
        )
        return
    row.decision = band.decision
    if band.decision == "report_only":
        row.status = PurchaseStatus.HUMAN_TASK.value
        row.checkout_path = CheckoutPath.HUMAN_BUYS.value
        packet = _packet(row, limit_micros=None)
        packet["steps"] = [
            "Decide whether the company should buy this, and buy it off-system if so.",
            "Record the cost under Finance, Expenses.",
            "Tell the agent what you decided (reply on its task).",
        ]
        packet["_note"] = band.reason
        row.checkout_packet = packet
        task_row = _create_task(
            db,
            row,
            f"Decide: buy {row.item} from {row.vendor}?",
            [*_task_lines(row, packet), "", f"Why it came to you: {band.reason}."],
            now,
        )
        row.checkout_task_id = task_row.id if task_row else None
        _audit_state(
            db, row, None, "purchase.handed_to_human", "was handed to a person", band.reason
        )
        return
    if band.decision == "auto":
        row.status = PurchaseStatus.APPROVED.value
        row.held_micros = row.amount_micros
        _audit_state(db, row, None, "purchase.approved", "was approved by the policy", band.reason)
        return
    # cofounder / human: park behind an Approval bound to the exact scope
    founder = wakes.active_founder(db, row.company_id)
    approver = (
        Approver.FOUNDER
        if band.decision == "cofounder" and founder is not None and founder.id != agent.id
        else Approver.HUMAN
    )
    if approver == Approver.HUMAN:
        row.decision = PurchaseDecision.HUMAN.value
    scope = _scope_for(row, _action_for(row), band.card_limit_micros)
    approval = approvals.create_approval_record(
        db,
        company_id=row.company_id,
        principal=principal,
        kind="external_purchase",
        title=f"Buy {_clip(row.item, 110)} from {_clip(row.vendor, 70)} "
        f"({_usd(row.amount_micros)})",
        reason=band.reason,
        amount_micros=row.amount_micros,
        vendor=row.vendor,
        recurrence=row.recurrence,
        risk="high"
        if row.amount_micros > 100_000_000 or row.recurrence != "one_time"
        else "medium",
        evidence={
            "purchase_request_id": row.id,
            "vendor_domain": row.vendor_domain,
            "url": row.url,
            "quantity": row.quantity,
            "card_limit_micros": band.card_limit_micros,
            "justification": _clip(row.justification, 600),
            "alternatives": _clip(row.alternatives, 300) if row.alternatives else None,
            "agent_id": agent.id,
        },
        scope=scope,
        task_id=task.id,
        subject=("purchase_request", row.id),
        approver=approver,
        tier="spend",
        action=_action_for(row),
        requested_by_agent_id=agent.id,
    )
    row.approval_id = approval.id
    row.status = PurchaseStatus.PENDING_APPROVAL.value
    row.checkout_path = CheckoutPath.HUMAN_CHECKOUT.value
    if approver == Approver.FOUNDER:
        wakes.wake_founder(
            db,
            row.company_id,
            "approval",
            f"Approval needed: {approval.title}",
            ref_type="approval",
            ref_id=approval.id,
            actor_agent_id=agent.id,
        )
    _audit_state(
        db,
        row,
        None,
        "purchase.pending_approval",
        f"waits for {approver.value} approval",
        band.reason,
    )


def _audit_state(
    db: Session,
    row: PurchaseRequest,
    principal: Principal | None,
    action: str,
    what: str,
    reason: str | None = None,
    **data: Any,
) -> None:
    audit.record(
        db,
        company_id=row.company_id,
        principal=principal,
        action=action,
        target_type="purchase_request",
        target_id=row.id,
        summary=f"Purchase of {_clip(row.item, 80)} from {row.vendor_domain} {what}",
        data={
            "status": row.status,
            "decision": row.decision,
            "amount_micros": row.amount_micros,
            "reason": _clip(reason, 300) if reason else None,
            **data,
        },
    )
    events.emit(db, row.company_id, "purchase.updated", {"purchase_request_id": row.id})


def _finish_without_purchase(
    db: Session,
    row: PurchaseRequest,
    status: PurchaseStatus,
    note: str | None,
    principal: Principal | None = None,
) -> None:
    """A request that ends without a payment: the hold is released and the agent is told why."""
    row.status = status.value
    row.held_micros = 0
    row.updated_at = utcnow()
    if note:
        row.checkout_packet = {**row.checkout_packet, "_note": _clip(note, 500)}
    _audit_state(db, row, principal, f"purchase.{status.value}", f"is {status.value}", note)


# ---------------------------------------------------------------- reading


def ack_for(purchase: PurchaseRequest) -> PurchaseAck:
    """The agent's answer: id, status, decision and a plain-language next step; no card data."""
    note = (purchase.checkout_packet or {}).get("_note")
    why = f" {note}" if note else ""
    steps = {
        PurchaseStatus.REQUESTED: "Your request is being processed.",
        PurchaseStatus.PENDING_APPROVAL: (
            "Waiting for the AI Co-founder's approval; you are woken when it is decided."
            if purchase.decision == PurchaseDecision.COFOUNDER
            else "Waiting for the Founder's approval; you are woken when it is decided."
        ),
        PurchaseStatus.APPROVED: "Approved. The Founder will complete the checkout; you are woken "
        "when it is paid.",
        PurchaseStatus.ISSUING: "Approved. The Founder will complete the checkout; you are woken "
        "when it is paid.",
        PurchaseStatus.ISSUE_FAILED: "Approved, but the payment card could not be prepared yet. "
        "The system retries; the Founder is told if it keeps failing.",
        PurchaseStatus.CARD_ISSUED: "Approved. The Founder will complete the checkout; you are "
        "woken when it is paid.",
        PurchaseStatus.AWAITING_CHECKOUT: "Approved. The Founder will complete the checkout; you "
        "are woken when it is paid.",
        PurchaseStatus.AUTHORIZED: "The payment is being processed; you are woken when it is "
        "recorded.",
        PurchaseStatus.SETTLED: "Paid and recorded. Carry on with your task.",
        PurchaseStatus.HUMAN_TASK: "Purchasing is not set up: your request was handed to the "
        f"Founder as a task.{why}",
        PurchaseStatus.REJECTED: f"Not approved.{why} Do not request it again; find another way.",
        PurchaseStatus.EXPIRED: f"Expired without being paid.{why} Ask again only if it is "
        "still needed.",
        PurchaseStatus.CANCELLED: f"Cancelled by the Founder.{why}",
    }
    return PurchaseAck(
        purchase_request_id=purchase.id,
        status=purchase.status,  # type: ignore[arg-type]
        decision=purchase.decision,  # type: ignore[arg-type]
        next_step=steps[PurchaseStatus(purchase.status)],
    )


def get_purchase(db: Session, ctx: CompanyContext, purchase_id: str) -> PurchaseRequest:
    """Members read; an agent reads only its own. Another company's id is a 404."""
    row = db.scalar(
        select(PurchaseRequest).where(
            PurchaseRequest.id == purchase_id, PurchaseRequest.company_id == ctx.company_id
        )
    )
    if row is None or (ctx.principal.is_agent and row.agent_id != ctx.principal.id):
        raise NotFound("Purchase request not found")
    return row


def list_purchases(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    agent_id: str | None = None,
    checkout_task_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[PurchaseRequest], str | None]:
    stmt = select(PurchaseRequest).where(PurchaseRequest.company_id == ctx.company_id)
    if ctx.principal.is_agent:
        stmt = stmt.where(PurchaseRequest.agent_id == ctx.principal.id)
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    if agent_id:
        stmt = stmt.where(PurchaseRequest.agent_id == agent_id)
    if checkout_task_id:
        stmt = stmt.where(PurchaseRequest.checkout_task_id == checkout_task_id)
    return paginate(db, stmt, PurchaseRequest, limit=limit, cursor=cursor, descending=True)


def serialize_purchases(db: Session, rows: list[PurchaseRequest]) -> list[PurchaseRequestOut]:
    names = dict(
        db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_({r.agent_id for r in rows}))
        ).all()
    )
    out = []
    for row in rows:
        item = PurchaseRequestOut.model_validate(row)
        item.agent_name = names.get(row.agent_id)
        out.append(item)
    return out


def purchase_detail(db: Session, row: PurchaseRequest) -> PurchaseRequestDetail:
    detail = PurchaseRequestDetail.model_validate(serialize_purchases(db, [row])[0].model_dump())
    approval = db.get(Approval, row.approval_id) if row.approval_id else None
    if approval is not None and approval.company_id == row.company_id:
        detail.approval = PurchaseApprovalSummary.model_validate(approval)
    card = db.get(AgentCard, row.card_id) if row.card_id else None
    if card is not None and card.company_id == row.company_id:
        detail.card = AgentCardOut.model_validate(card)
    txns = db.scalars(
        select(CardTransaction)
        .where(
            CardTransaction.company_id == row.company_id,
            CardTransaction.purchase_request_id == row.id,
        )
        .order_by(CardTransaction.occurred_at, CardTransaction.id)
    )
    detail.transactions = [CardTransactionOut.model_validate(t) for t in txns]
    return detail


# ---------------------------------------------------------------- approval decisions


def apply_decision(db: Session, ctx: CompanyContext, approval: Approval, decision: str) -> None:
    """Called by ``approvals.decide`` inside its transaction (no commit) for an approval whose
    subject is a purchase request. The card is issued after the commit
    (``issue_after_decision``)."""
    p = db.scalar(
        select(PurchaseRequest).where(
            PurchaseRequest.id == approval.subject_id,
            PurchaseRequest.company_id == approval.company_id,
        )
    )
    if p is None or p.status != PurchaseStatus.PENDING_APPROVAL:
        return
    principal = ctx.principal
    if decision != "approve":
        verb = "rejected" if decision == "reject" else "sent back with changes requested"
        _finish_without_purchase(
            db, p, PurchaseStatus.REJECTED, approval.decision_note or verb, principal
        )
        return
    # The hard monthly cap is re-checked at approval time: two requests that each fit can not
    # both be approved past it.
    limits = policy.load_policy(db, p.company_id)
    now = utcnow()
    month = _month_of(db, p.company_id, now)
    if limits.purchase_monthly_budget_micros is not None:
        committed = _committed_micros(db, p.company_id, month)
        if committed + p.amount_micros > limits.purchase_monthly_budget_micros:
            reason = "The monthly purchase budget no longer has room for this purchase"
            _finish_without_purchase(db, p, PurchaseStatus.REJECTED, reason)
            alerts.raise_alert(
                db,
                p.company_id,
                kind="purchase_denied",
                severity=AlertSeverity.WARNING,
                title=f"An approved purchase was stopped: {_clip(p.item, 80)}",
                body=reason,
                related_type="purchase_request",
                related_id=p.id,
                dedupe_key=f"purchase_denied:{p.id}",
                wake_founder=False,
            )
            return
    p.status = PurchaseStatus.APPROVED.value
    p.held_micros = p.amount_micros
    p.updated_at = now
    _audit_state(
        db,
        p,
        principal,
        "purchase.approved",
        "was approved",
        approval.decision_note,
        approved_by_type=principal.type.value,
        approval_id=approval.id,
    )


def issue_after_decision(db: Session, approval: Approval) -> None:
    """After ``approvals.decide`` committed an approval: issue the card (never raises)."""
    if approval.subject_id:
        _safe_issue(db, approval.subject_id)


def approve(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseDecisionRequest
) -> PurchaseRequest:
    """Human approve through the Wallet: a thin wrapper over ``approvals.decide``."""
    return _decide(db, ctx, purchase_id, "approve", data)


def reject(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseDecisionRequest
) -> PurchaseRequest:
    return _decide(db, ctx, purchase_id, "reject", data)


def _decide(
    db: Session, ctx: CompanyContext, purchase_id: str, decision: str, data: PurchaseDecisionRequest
) -> PurchaseRequest:
    require_human_admin(ctx, f"{decision} a purchase")
    p = get_purchase(db, ctx, purchase_id)
    if p.status != PurchaseStatus.PENDING_APPROVAL or p.approval_id is None:
        raise InvalidTransition(
            f"Only a purchase waiting for approval can be {decision}d (this one is {p.status})",
            details={"from": p.status},
        )
    approvals.decide(db, ctx, p.approval_id, decision, data.note)
    db.refresh(p)
    return p


# ---------------------------------------------------------------- the issuing job


def _safe_issue(db: Session, purchase_id: str) -> None:
    try:
        issue_card(db, purchase_id)
    except Exception:  # the request is already approved and committed; never lose the caller
        db.rollback()
        logger.exception("issuing the card for purchase %s failed", purchase_id)


def _issue_state(p: PurchaseRequest) -> dict[str, Any]:
    return dict((p.checkout_packet or {}).get("_issue") or {})


def _spend_approval(db: Session, p: PurchaseRequest, limit_micros: int, now: datetime) -> None:
    """Spend the approval on exactly this scope, in the transaction that records the intent."""
    if not p.approval_id:
        return
    approval = db.get(Approval, p.approval_id)
    if approval is None or approval.consumed_at is not None:
        return
    approvals.consume_internal(
        db, p.company_id, p.approval_id, _scope_for(p, _action_for(p), limit_micros), now=now
    )


def issue_card(
    db: Session,
    purchase_id: str,
    *,
    provider: CardProvider | None = None,
    now: datetime | None = None,
) -> PurchaseRequest:
    """The idempotent issuing job: consumes the approval, issues the card (after commit, with the
    purchase id as idempotency key), creates the checkout task. Transient provider errors back off,
    permanent ones alert; it never falls back to another provider or account."""
    now = now or utcnow()
    p = db.get(PurchaseRequest, purchase_id)
    if p is None:
        raise NotFound("Purchase request not found")
    if p.status == PurchaseStatus.ISSUING and now - p.updated_at < ISSUING_STALE:
        return p  # another worker is on it
    if p.status not in (
        PurchaseStatus.APPROVED,
        PurchaseStatus.ISSUE_FAILED,
        PurchaseStatus.ISSUING,
    ):
        return p
    if _halted(db, p.company_id):
        return p  # stays approved; the pass that follows the resume picks it up
    limits = policy.load_policy(db, p.company_id)
    limit_micros = card_limit_micros(limits, p.amount_micros)
    account = wallet.active_account(db, p.company_id)
    if account is None or account.provider == "manual":
        return _to_checkout(db, p, account, None, limit_micros, now)

    # Phase A: spend the approval and record the intent, in one transaction.
    try:
        _spend_approval(db, p, limit_micros, now)
    except Conflict as exc:
        return _issue_failed(db, p, exc.message, transient=False, now=now)
    p.status = PurchaseStatus.ISSUING.value
    p.updated_at = now
    _audit_state(db, p, None, "card.issue_started", "is having its card issued", None)
    db.commit()

    # Phase B: the provider call, outside any transaction.
    caps = None
    try:
        prov = provider or wallet.provider_for(db, account)
        caps = prov.capabilities
        recurring = p.recurrence != "one_time"
        if recurring:
            interval = p.recurrence if p.recurrence in caps.interval_limits else "all_time"
        else:
            interval = "per_authorization" if caps.per_auth_limit else "all_time"
        issued = prov.issue_card(
            limit_micros=limit_micros,
            limit_interval=interval,
            merchant_lock=MerchantLock(name=p.vendor, domain=p.vendor_domain),
            single_use=not recurring,
            expires_at=None if recurring else now + timedelta(hours=limits.purchase_card_ttl_hours),
            idempotency_key=p.id,
            metadata={"cos_purchase": p.id, "cos_company": p.company_id},
        )
    except CardProviderError as exc:
        return _issue_failed(db, p, exc.message, transient=exc.transient, now=now)

    # Phase C: record the card and hand the checkout to a person.
    card = db.scalar(
        select(AgentCard).where(
            AgentCard.card_account_id == account.id,
            AgentCard.provider_card_id == issued.provider_card_id,
        )
    )
    if card is None:
        card = AgentCard(
            company_id=p.company_id,
            card_account_id=account.id,
            provider_card_id=issued.provider_card_id,
            kind=(
                CardKind.SUBSCRIPTION if p.recurrence != "one_time" else CardKind.SINGLE_USE
            ).value,
            status=CardStatus.ACTIVE.value if issued.status == "active" else issued.status,
            purchase_request_id=p.id,
            agent_id=p.agent_id,
            last4=issued.last4,
            brand=issued.brand,
            exp_month=issued.exp_month,
            exp_year=issued.exp_year,
            limit_micros=issued.limit_micros,
            limit_interval=issued.limit_interval,
            merchant_lock={
                "name": p.vendor,
                "domain": p.vendor_domain,
                "mcc_allow": [],
                "country": None,
            },
            expires_at=None
            if p.recurrence != "one_time"
            else now + timedelta(hours=limits.purchase_card_ttl_hours),
        )
        db.add(card)
        db.flush()
    p.card_id = card.id
    audit.record(
        db,
        company_id=p.company_id,
        principal=None,
        action="card.issued",
        target_type="card",
        target_id=card.id,
        summary=f"Issued a card ending {card.last4} for {_clip(p.item, 80)} "
        f"(limit {_usd(card.limit_micros)})",
        data={
            "purchase_request_id": p.id,
            "limit_micros": card.limit_micros,
            "kind": card.kind,
            "provider": account.provider,
        },
    )
    return _to_checkout(db, p, account, card, limit_micros, now)


def _to_checkout(
    db: Session,
    p: PurchaseRequest,
    account: CardAccount | None,
    card: AgentCard | None,
    limit_micros: int,
    now: datetime,
) -> PurchaseRequest:
    """The card (if any) is ready: build the packet, create the human task, wait for the payment."""
    if card is None:  # manual card or no account: nothing was issued, a person pays
        try:
            _spend_approval(db, p, limit_micros, now)
        except Conflict as exc:
            return _issue_failed(db, p, exc.message, transient=False, now=now)
        p.checkout_path = CheckoutPath.HUMAN_BUYS.value
    else:
        p.checkout_path = CheckoutPath.HUMAN_CHECKOUT.value
    limits = policy.load_policy(db, p.company_id)
    packet = _packet(
        p,
        limit_micros=limit_micros if card is not None else None,
        company_email=_company_email(db, p.company_id),
        card=card,
        account=account,
        keep=p.checkout_packet,
    )
    packet.pop("_issue", None)
    p.checkout_packet = packet
    task = _create_task(
        db,
        p,
        f"Buy {p.item} from {p.vendor} (up to {_usd(limit_micros if card else p.amount_micros)})",
        _task_lines(p, packet),
        now,
    )
    p.checkout_task_id = task.id if task is not None else None
    p.status = PurchaseStatus.AWAITING_CHECKOUT.value
    p.expires_at = now + timedelta(hours=limits.purchase_card_ttl_hours)
    p.held_micros = p.amount_micros
    p.updated_at = now
    _audit_state(
        db,
        p,
        None,
        "purchase.awaiting_checkout",
        "is waiting for a person to pay",
        None,
        card_id=p.card_id,
    )
    if task is None:
        alerts.raise_alert(
            db,
            p.company_id,
            kind="purchase_no_checkout_person",
            severity=AlertSeverity.WARNING,
            title="Nobody can do the checkout",
            body="An approved purchase has no active owner or member to hand the checkout to.",
            related_type="purchase_request",
            related_id=p.id,
            dedupe_key=f"purchase_no_checkout_person:{p.id}",
        )
    db.commit()
    return p


def _issue_failed(
    db: Session, p: PurchaseRequest, message: str, *, transient: bool, now: datetime
) -> PurchaseRequest:
    message = secrets_scan.scrub(message) or "the provider refused"
    state = _issue_state(p)
    attempts = int(state.get("attempts", 0)) + 1
    transient = transient and attempts < ISSUE_MAX_ATTEMPTS
    wait = ISSUE_BACKOFF_SECONDS[min(attempts - 1, len(ISSUE_BACKOFF_SECONDS) - 1)]
    state.update(
        attempts=attempts,
        transient=transient,
        error=_clip(message, 200),
        next_attempt_at=(now + timedelta(seconds=wait)).isoformat() if transient else None,
    )
    p.checkout_packet = {**p.checkout_packet, "_issue": state}
    p.status = PurchaseStatus.ISSUE_FAILED.value
    p.updated_at = now
    _audit_state(
        db,
        p,
        None,
        "card.issue_failed",
        "could not get its card yet" if transient else "could not get a card",
        message,
        attempts=attempts,
        transient=transient,
    )
    if not transient:
        alerts.raise_alert(
            db,
            p.company_id,
            kind="card_issue_failed",
            severity=AlertSeverity.CRITICAL,
            title=f"A card could not be issued for {_clip(p.item, 80)}",
            body=f"{_clip(message, 200)} The purchase is approved but stuck; cancel it or fix the "
            "card account and ask the agent to request it again.",
            related_type="purchase_request",
            related_id=p.id,
            dedupe_key=f"card_issue_failed:{p.id}",
        )
    db.commit()
    return p


# ---------------------------------------------------------------- settlement (mark paid)


def _company_date(db: Session, company_id: str, at: datetime) -> date:
    from zoneinfo import ZoneInfo

    company = db.get(Company, company_id)
    return at.astimezone(ZoneInfo(company.timezone if company else "UTC")).date()


def _finish_checkout_task(db: Session, p: PurchaseRequest, principal: Principal | None) -> None:
    if not p.checkout_task_id:
        return
    task = db.get(Task, p.checkout_task_id)
    if task is None or task.company_id != p.company_id:
        return
    svc = _tasks()
    if TaskStatus(task.status) in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return
    if task.status == TaskStatus.READY:
        task.attempt_count += 1
        svc.transition(db, task, TaskStatus.IN_PROGRESS, principal=principal, reason="payment seen")
    if task.status in (TaskStatus.BACKLOG, TaskStatus.BLOCKED):
        svc.transition(db, task, TaskStatus.CANCELLED, principal=principal, reason="payment seen")
        return
    svc.finish_success(
        db,
        task,
        summary="Paid and recorded in the Wallet.",
        output_refs=[],
        principal=principal,
        run_id=None,
    )


def _cancel_checkout_task(
    db: Session, p: PurchaseRequest, principal: Principal | None, reason: str
) -> None:
    if not p.checkout_task_id:
        return
    task = db.get(Task, p.checkout_task_id)
    if task is None or task.company_id != p.company_id:
        return
    if TaskStatus(task.status) in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return
    _tasks().transition(db, task, TaskStatus.CANCELLED, principal=principal, reason=reason)


def settle_purchase(
    db: Session,
    p: PurchaseRequest,
    txn: CardTransaction,
    *,
    principal: Principal | None,
    now: datetime | None = None,
) -> ExternalExpense:
    """Record a payment against its purchase (no commit): the hold becomes a ``verified`` charge,
    one expense row is written (once per transaction), the checkout task is finished and the agent
    is woken. Shared by "mark paid" and by reconciliation."""
    now = now or utcnow()
    existing = db.scalar(
        select(ExternalExpense).where(
            ExternalExpense.company_id == p.company_id,
            ExternalExpense.card_transaction_id == txn.id,
        )
    )
    if existing is not None:
        return existing
    amount = txn.amount_micros
    p.status = PurchaseStatus.SETTLED.value
    p.settled_micros = amount
    p.held_micros = 0
    p.cost_kind = "verified"
    p.updated_at = now
    txn.purchase_request_id = p.id
    txn.card_id = txn.card_id or p.card_id
    txn.match_status = CardTxnMatch.MATCHED.value
    source = "human" if txn.source == CardTxnSource.HUMAN else "card"
    expense = ExternalExpense(
        company_id=p.company_id,
        vendor=p.vendor,
        description=_clip(p.item, 500),
        amount_micros=amount,
        recurrence="one_time",  # each charge is its own row; the renewal rows come with the charges
        project_id=p.project_id,
        incurred_on=_company_date(db, p.company_id, txn.occurred_at),
        created_by_type="system" if principal is None else principal.type.value,
        created_by_id=principal.id if principal else None,
        cost_kind="verified",
        source=source,
        purchase_request_id=p.id,
        card_transaction_id=txn.id,
    )
    db.add(expense)
    db.flush()
    _audit_state(
        db,
        p,
        principal,
        "purchase.settled",
        f"was paid ({'confirmed by a person' if source == 'human' else 'seen on the card'})",
        None,
        settled_micros=amount,
        cost_kind="verified",
        card_transaction_id=txn.id,
    )
    audit.record(
        db,
        company_id=p.company_id,
        principal=principal,
        action="expense.created",
        target_type="expense",
        target_id=expense.id,
        summary=f"Recorded a verified expense: {p.vendor}",
        data={"amount_micros": amount, "source": source, "purchase_request_id": p.id},
    )
    events.emit(
        db,
        p.company_id,
        "expense.created",
        {"expense_id": expense.id, "vendor": p.vendor, "amount_micros": amount},
    )
    _finish_checkout_task(db, p, principal)
    if p.recurrence != "one_time":
        from app.services import subscriptions  # local: subscriptions depends on this module

        subscriptions.create_from_settled(
            db, p, charged_on=expense.incurred_on, charged_micros=amount, card=None, now=now
        )
    _wake(
        db, p, f"Your purchase was paid: {_clip(p.item, 100)}. Carry on with the task it was for."
    )
    return expense


def _require_checkout_person(db: Session, ctx: CompanyContext, p: PurchaseRequest) -> None:
    if ctx.is_human_admin:
        return
    task = db.get(Task, p.checkout_task_id) if p.checkout_task_id else None
    if (
        ctx.principal.is_human
        and ctx.member_id is not None
        and task is not None
        and task.assignee_member_id == ctx.member_id
    ):
        return
    raise Forbidden("Only the person doing the checkout, or an owner or admin, can mark it paid")


def mark_paid(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseMarkPaid
) -> PurchaseRequest:
    """Manual mode (or a checkout the provider could not see): the amount the human confirms is
    recorded ``verified``, source ``human`` ("confirmed by you"). An INR amount is stored for
    reference, never converted."""
    p = get_purchase(db, ctx, purchase_id)
    _require_checkout_person(db, ctx, p)
    if p.status not in _CHECKOUT_OPEN:
        raise InvalidTransition(
            f"Only a purchase waiting for its checkout can be marked paid (this one is {p.status})",
            details={"from": p.status},
        )
    if p.card_id is not None:
        raise Conflict(
            "This purchase was paid with a card Foundry issued: the payment is picked up from the "
            "card provider, so it cannot also be marked paid by hand. Cancel it if it was not paid."
        )
    now = utcnow()
    amount = usd_to_micros(data.merchant_amount_usd)
    account = wallet.active_account(db, p.company_id)
    if account is None:
        raise Conflict("There is no card account to record this payment against")
    receipt = None
    if data.receipt_artifact_id:
        found = db.scalar(
            select(Artifact.id).where(
                Artifact.id == data.receipt_artifact_id, Artifact.company_id == ctx.company_id
            )
        )
        if found is None:
            raise NotFound("Artifact not found")
        receipt = found
    raw: dict[str, Any] = {"confirmed_by": ctx.principal.id}
    if data.billed_amount_inr is not None:
        raw["billed_amount_inr"] = str(data.billed_amount_inr)  # reference only, not converted
    if receipt:
        raw["receipt_artifact_id"] = receipt
    if data.note:
        raw["note"] = _clip(data.note, 300)
    txn = CardTransaction(
        company_id=p.company_id,
        card_account_id=account.id,
        card_id=p.card_id,
        purchase_request_id=p.id,
        provider_txn_id=f"human-{p.id}",
        kind=CardTxnKind.CAPTURE.value,
        status=CardTxnStatus.SETTLED.value,
        amount_micros=amount,
        merchant_name=p.vendor[:200],
        match_status=CardTxnMatch.MATCHED.value,
        source=CardTxnSource.HUMAN.value,
        occurred_at=now,
        settled_at=now,
        raw=raw,
    )
    db.add(txn)
    try:
        db.flush()
    except Exception as exc:  # unique (account, provider_txn_id): already recorded
        db.rollback()
        raise Conflict("This purchase was already marked paid") from exc
    settle_purchase(db, p, txn, principal=ctx.principal, now=now)
    limits = policy.load_policy(db, p.company_id)
    if amount > card_limit_micros(limits, p.amount_micros):
        alerts.raise_alert(
            db,
            p.company_id,
            kind="purchase_over_limit",
            severity=AlertSeverity.WARNING,
            title=f"{_clip(p.item, 80)} cost more than approved",
            body=f"Approved {_usd(p.amount_micros)}, charged {_usd(amount)} "
            "(confirmed by a person).",
            related_type="purchase_request",
            related_id=p.id,
            dedupe_key=f"purchase_over_limit:{p.id}",
            wake_founder=False,
        )
    db.commit()
    return p


# ---------------------------------------------------------------- cancel / expire / retire


def _release_card(
    db: Session, p: PurchaseRequest, reason: str, principal: Principal | None
) -> bool:
    """Close the purchase's card. False when the provider would not (the card is left as it is)."""
    card = db.get(AgentCard, p.card_id) if p.card_id else None
    if card is None or card.status in (CardStatus.CLOSED, CardStatus.EXPIRED):
        return True
    return wallet.close_card_for_system(db, card, reason=reason, principal=principal)


def cancel(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseCancelRequest
) -> PurchaseRequest:
    """Human cancel: closes the card, releases the hold, wakes the agent."""
    require_human_admin(ctx, "cancel a purchase")
    p = get_purchase(db, ctx, purchase_id)
    if p.status in TERMINAL_PURCHASE_STATUSES:
        raise InvalidTransition(
            f"A {p.status} purchase cannot be cancelled", details={"from": p.status}
        )
    if not _release_card(db, p, "purchase cancelled", ctx.principal):
        raise Conflict("The provider did not close the card, so the purchase stays open; try again")
    _cancel_open(db, p, ctx.principal, data.reason)
    db.commit()
    return p


def _cancel_open(
    db: Session, p: PurchaseRequest, principal: Principal | None, reason: str | None
) -> None:
    approval = db.get(Approval, p.approval_id) if p.approval_id else None
    if approval is not None and approval.status == ApprovalStatus.PENDING:
        approval.status = ApprovalStatus.EXPIRED.value
        approval.decision_note = "The purchase was cancelled"
        approval.updated_at = utcnow()
        audit.record(
            db,
            company_id=p.company_id,
            principal=principal,
            action="approval.expired",
            target_type="approval",
            target_id=approval.id,
            summary=f"Approval withdrawn: {approval.title}",
            data={"reason": "purchase cancelled"},
        )
    _cancel_checkout_task(db, p, principal, "purchase cancelled")
    _finish_without_purchase(db, p, PurchaseStatus.CANCELLED, reason, principal)
    _wake(
        db, p, f"The purchase was cancelled: {_clip(p.item, 100)}.{' ' + reason if reason else ''}"
    )


def cancel_for_retired_agent(db: Session, agent: Agent, principal: Principal | None) -> int:
    """A retired agent's purchases that are not paid yet are cancelled: cards closed (a card the
    provider would not close is frozen and alerted), holds released. No commit."""
    rows = list(
        db.scalars(
            select(PurchaseRequest).where(
                PurchaseRequest.company_id == agent.company_id,
                PurchaseRequest.agent_id == agent.id,
                PurchaseRequest.status.in_([s.value for s in _OPEN_BEFORE_CHECKOUT]),
            )
        )
    )
    for p in rows:
        if not _release_card(db, p, "agent retired", principal):
            card = db.get(AgentCard, p.card_id) if p.card_id else None
            if card is not None:
                wallet._freeze_cards(db, [card], "agent_retired", principal)
        _cancel_open(db, p, principal, "the requesting agent was retired")
    return len(rows)


def expire_stale(db: Session, company_id: str, *, now: datetime | None = None) -> int:
    """Scheduler pass: expires approvals nobody decided in 7 days, closes unused cards past their
    TTL and expires the request, and retries issuing that is due. Returns the number of requests
    expired."""
    now = now or utcnow()
    expired = 0
    pending = list(
        db.scalars(
            select(PurchaseRequest).where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequest.status == PurchaseStatus.PENDING_APPROVAL,
            )
        )
    )
    for p in pending:
        approval = db.get(Approval, p.approval_id) if p.approval_id else None
        if approval is None or approval.status != ApprovalStatus.PENDING:
            continue
        if now - approval.created_at < APPROVAL_TTL:
            continue
        approval.status = ApprovalStatus.EXPIRED.value
        approval.decision_note = "Nobody decided within 7 days"
        approval.updated_at = now
        audit.record(
            db,
            company_id=company_id,
            principal=None,
            action="approval.expired",
            target_type="approval",
            target_id=approval.id,
            summary=f"Approval expired: {approval.title}",
            data={},
        )
        _finish_without_purchase(db, p, PurchaseStatus.EXPIRED, "Nobody decided within 7 days")
        _wake(db, p, f"Your purchase request expired undecided: {_clip(p.item, 100)}.")
        expired += 1
    waiting = list(
        db.scalars(
            select(PurchaseRequest).where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequest.status.in_(
                    [PurchaseStatus.CARD_ISSUED.value, PurchaseStatus.AWAITING_CHECKOUT.value]
                ),
                PurchaseRequest.expires_at.is_not(None),
                PurchaseRequest.expires_at <= now,
            )
        )
    )
    for p in waiting:
        if not _release_card(db, p, "unused past its time limit", None):
            alerts.raise_alert(
                db,
                company_id,
                kind="card_close_failed",
                severity=AlertSeverity.WARNING,
                title="An unused card could not be closed",
                body="The purchase expired but the provider would not close its card. Close it in "
                "the provider's dashboard.",
                related_type="purchase_request",
                related_id=p.id,
                dedupe_key=f"card_close_failed:{p.id}",
            )
            continue
        _cancel_checkout_task(db, p, None, "the purchase expired unpaid")
        _finish_without_purchase(db, p, PurchaseStatus.EXPIRED, "Not paid before the card expired")
        _wake(
            db,
            p,
            f"Your purchase expired unpaid: {_clip(p.item, 100)}. Ask again only if it is "
            "still needed.",
        )
        expired += 1
    db.commit()
    retry = list(
        db.scalars(
            select(PurchaseRequest).where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequest.status.in_(
                    [
                        PurchaseStatus.APPROVED.value,
                        PurchaseStatus.ISSUE_FAILED.value,
                        PurchaseStatus.ISSUING.value,
                    ]
                ),
            )
        )
    )
    for p in retry:
        state = _issue_state(p)
        if p.status == PurchaseStatus.ISSUE_FAILED:
            due = state.get("next_attempt_at")
            if not state.get("transient") or not due or datetime.fromisoformat(due) > now:
                continue
        try:
            issue_card(db, p.id, now=now)
        except Exception:
            db.rollback()
            logger.exception("retrying the card for purchase %s failed", p.id)
    return expired
