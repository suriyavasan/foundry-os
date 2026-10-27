"""Subscriptions: created when a recurring purchase settles, reviewed by the owning agent before
each renewal, cancelled by a human on the vendor's site (docs/specs/m15, section 4.5.3).

The daily ``check`` pass:

1. **Renewal review.** ``subscription_renewal_alert_days`` before ``next_renewal_on`` the owning
   agent (its manager if it was retired, else the Co-founder) gets a task to call
   ``company_review_subscription``. ``keep`` is noted; ``cancel``, or no answer within 48 hours,
   becomes a **human task** "Cancel <vendor> before <date>" plus an alert. Cancelling on a vendor's
   site is a human action; Foundry never does it. Freezing a card is not a cancellation (the vendor
   may keep dunning), and the UI says so.
2. **Renewal charge** within the tolerance advances ``next_renewal_on`` (``record_charge``, called
   by reconciliation).
3. **Price increase** beyond the tolerance is declined by the card's limit; reconciliation calls
   ``record_price_increase``, whose alert carries the new price. The agent must file a new request.
4. **Missed renewal** (3 days after the due date with no charge) is an info alert and marks the
   subscription ``lapsed``; a later charge reactivates it.
5. After a subscription is marked cancelled its card is closed once the paid period has ended.

Every state change writes an audit event in the same transaction. Nothing here moves money.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.integrations.cards import registry
from app.models.agent import Agent
from app.models.company import Company, CompanyMember
from app.models.enums import (
    AgentKind,
    AgentStatus,
    AlertSeverity,
    CardStatus,
    CompanyStatus,
    MemberRole,
    MemberStatus,
    Recurrence,
    SubscriptionStatus,
    TaskStatus,
)
from app.models.purchasing import AgentCard, CardAccount, PurchaseRequest, Subscription
from app.models.work import Task
from app.schemas.purchasing import SubscriptionHumanAction, SubscriptionReviewIn
from app.schemas.work import TaskCreate
from app.services import alerts, audit, events, policy, secrets_scan, tasks, wakes
from app.services.pagination import paginate

logger = logging.getLogger("company_os.subscriptions")

REVIEW_ANSWER_WINDOW = timedelta(hours=48)
MISSED_AFTER_DAYS = 3
LIVE_AGENT = (AgentStatus.ACTIVE.value, AgentStatus.PAUSED.value)
REVIEWABLE = (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.REVIEW_DUE.value)
OPEN_STATUSES = (
    SubscriptionStatus.ACTIVE.value,
    SubscriptionStatus.REVIEW_DUE.value,
    SubscriptionStatus.CANCEL_REQUESTED.value,
    SubscriptionStatus.CANCELLED.value,
    SubscriptionStatus.LAPSED.value,
)


# ---------------------------------------------------------------------- dates


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def add_period(day: date, recurrence: str) -> date:
    return add_months(day, 12 if recurrence == Recurrence.YEARLY else 1)


# ---------------------------------------------------------------------- reconciliation hooks


def create_from_settled(
    db: Session,
    purchase: PurchaseRequest,
    *,
    charged_on: date | None = None,
    charged_micros: int | None = None,
    card: AgentCard | None = None,
    now: datetime | None = None,
) -> Subscription:
    """Called by reconciliation when a recurring purchase settles. Idempotent: a purchase that
    already has a subscription returns it."""
    if purchase.subscription_id:
        existing = db.get(Subscription, purchase.subscription_id)
        if existing is not None and existing.company_id == purchase.company_id:
            return existing
    now = now or utcnow()
    started = charged_on or now.date()
    sub = Subscription(
        company_id=purchase.company_id,
        purchase_request_id=purchase.id,
        agent_id=purchase.agent_id,
        vendor=purchase.vendor,
        vendor_domain=purchase.vendor_domain,
        item=purchase.item,
        amount_micros=purchase.amount_micros,
        recurrence=purchase.recurrence,
        card_id=card.id if card is not None else purchase.card_id,
        status=SubscriptionStatus.ACTIVE.value,
        started_on=started,
        next_renewal_on=add_period(started, purchase.recurrence),
        last_charged_on=started,
        last_charge_micros=charged_micros,
    )
    db.add(sub)
    db.flush()
    purchase.subscription_id = sub.id
    if card is not None:
        card.subscription_id = sub.id
    audit.record(
        db,
        company_id=sub.company_id,
        principal=None,
        action="subscription.created",
        target_type="subscription",
        target_id=sub.id,
        summary=f"Subscription to {sub.vendor} started ({sub.recurrence})",
        data={"purchase_request_id": purchase.id, "amount_micros": sub.amount_micros},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})
    return sub


def record_charge(
    db: Session,
    sub: Subscription,
    *,
    amount_micros: int,
    charged_on: date,
    card: AgentCard | None,
    now: datetime,
) -> None:
    """A renewal charge was seen (settled, already inside the tolerance or flagged by the caller).
    Advances ``next_renewal_on`` and reactivates a lapsed or review-due subscription."""
    window = 45 if sub.recurrence == Recurrence.YEARLY else 10
    old_next = sub.next_renewal_on
    base = old_next if old_next and abs((charged_on - old_next).days) <= window else charged_on
    previous = sub.status
    sub.last_charged_on = charged_on
    sub.last_charge_micros = amount_micros
    sub.updated_at = now
    if previous == SubscriptionStatus.CANCEL_REQUESTED:
        alerts.raise_alert(
            db,
            sub.company_id,
            kind="subscription_charged_after_cancel_request",
            severity=AlertSeverity.WARNING,
            title=f"{sub.vendor} charged again while its cancellation is pending",
            body="Cancel it on the vendor's site and mark it cancelled in the Wallet.",
            related_type="subscription",
            related_id=sub.id,
            dedupe_key=f"subscription_charged_pending_cancel:{sub.id}",
            wake_founder=False,
        )
        return
    sub.next_renewal_on = add_period(base, sub.recurrence)
    if previous in (SubscriptionStatus.REVIEW_DUE, SubscriptionStatus.LAPSED):
        sub.status = SubscriptionStatus.ACTIVE.value
    alerts.resolve_by_key(db, sub.company_id, _price_key(sub.id))
    audit.record(
        db,
        company_id=sub.company_id,
        principal=None,
        action="subscription.renewed",
        target_type="subscription",
        target_id=sub.id,
        summary=f"{sub.vendor} renewed; next renewal {sub.next_renewal_on}",
        data={"amount_micros": amount_micros, "charged_on": charged_on.isoformat()},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})


def record_price_increase(
    db: Session, sub: Subscription, *, new_amount_micros: int, now: datetime
) -> None:
    """The vendor asked for more than the approved price plus tolerance; the card declined it."""
    alerts.raise_alert(
        db,
        sub.company_id,
        kind="subscription_price_increase",
        severity=AlertSeverity.WARNING,
        title=f"{sub.vendor} wants more than the approved price",
        body=f"The renewal was declined: {_money(new_amount_micros)} requested, "
        f"{_money(sub.amount_micros)} approved. The owning agent must file a new purchase "
        "request for the new price.",
        related_type="subscription",
        related_id=sub.id,
        dedupe_key=_price_key(sub.id),
    )
    owner = _owner_agent(db, sub, now)
    wakes.wake_agent(
        db,
        sub.company_id,
        owner.id if owner is not None else None,
        "subscription",
        f"{sub.vendor} asked for {_money(new_amount_micros)} but {_money(sub.amount_micros)} was "
        "approved, so the card declined it. File a new purchase request if you still need it.",
        ref_type="subscription",
        ref_id=sub.id,
        now=now,
    )
    audit.record(
        db,
        company_id=sub.company_id,
        principal=None,
        action="subscription.price_increase",
        target_type="subscription",
        target_id=sub.id,
        summary=f"{sub.vendor} renewal declined: price above the approved amount",
        data={"approved_micros": sub.amount_micros, "requested_micros": new_amount_micros},
    )


# ---------------------------------------------------------------------- reads


def list_subscriptions(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[Subscription], str | None]:
    stmt = select(Subscription).where(Subscription.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Subscription.status == status)
    return paginate(db, stmt, Subscription, limit=limit, cursor=cursor, descending=True)


def get_subscription(db: Session, ctx: CompanyContext, subscription_id: str) -> Subscription:
    return _load(db, ctx.company_id, subscription_id)


def _load(db: Session, company_id: str, subscription_id: str) -> Subscription:
    sub = db.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id, Subscription.company_id == company_id
        )
    )
    if sub is None:
        raise NotFound("Subscription not found")
    return sub


# ---------------------------------------------------------------------- decisions


def review(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionReviewIn
) -> Subscription:
    """Agent entry (``company_review_subscription``): keep, or cancel (a human cancel task). Only
    the owning agent or the Co-founder; a cancel is never executed, only requested."""
    if not ctx.principal.is_agent:
        raise Forbidden("Agents review subscriptions through the agent API")
    sub = _load(db, ctx.company_id, subscription_id)
    agent = db.get(Agent, ctx.principal.id)
    if agent is None or agent.company_id != ctx.company_id:
        raise Forbidden("Unknown agent")
    if sub.agent_id != agent.id and agent.kind != AgentKind.FOUNDER:
        raise Forbidden("Only the owning agent or the Co-founder can review this subscription")
    if sub.status not in REVIEWABLE:
        raise Conflict(f"This subscription is {sub.status}; there is nothing to review")
    now = utcnow()
    reason = (secrets_scan.scrub(data.reason) or "")[:1000]
    sub.reviewed_at = now
    sub.updated_at = now
    if data.decision == "keep":
        sub.status = SubscriptionStatus.ACTIVE.value
        audit.record(
            db,
            company_id=sub.company_id,
            principal=ctx.principal,
            action="subscription.kept",
            target_type="subscription",
            target_id=sub.id,
            summary=f"{agent.name} will keep {sub.vendor}",
            data={"reason": reason},
        )
    else:
        _request_cancel(
            db, sub, now, why=f"{agent.name} asked to cancel: {reason}", by=ctx.principal
        )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})
    db.commit()
    return sub


def mark_cancelled(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionHumanAction
) -> Subscription:
    """Human: the vendor subscription is cancelled; the card closes after the paid period."""
    require_human_admin(ctx, "mark subscriptions cancelled")
    sub = _load(db, ctx.company_id, subscription_id)
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub
    now = utcnow()
    sub.status = SubscriptionStatus.CANCELLED.value
    sub.reviewed_at = now
    sub.updated_at = now
    _finish_task(db, sub.cancel_task_id, sub.company_id, "Marked cancelled in the Wallet.", now)
    audit.record(
        db,
        company_id=sub.company_id,
        principal=ctx.principal,
        action="subscription.cancelled",
        target_type="subscription",
        target_id=sub.id,
        summary=f"{sub.vendor} subscription marked cancelled",
        data={"note": (secrets_scan.scrub(data.note) or "")[:500] if data.note else None},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})
    db.commit()
    return sub


def keep(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionHumanAction
) -> Subscription:
    require_human_admin(ctx, "keep subscriptions")
    sub = _load(db, ctx.company_id, subscription_id)
    if sub.status == SubscriptionStatus.CANCELLED:
        raise Conflict("This subscription is already cancelled")
    now = utcnow()
    sub.status = SubscriptionStatus.ACTIVE.value
    sub.reviewed_at = now
    sub.updated_at = now
    if sub.cancel_task_id:
        _cancel_task(db, sub.cancel_task_id, sub.company_id, "The owner decided to keep it.", now)
    audit.record(
        db,
        company_id=sub.company_id,
        principal=ctx.principal,
        action="subscription.kept",
        target_type="subscription",
        target_id=sub.id,
        summary=f"{sub.vendor} subscription kept by a person",
        data={"note": (secrets_scan.scrub(data.note) or "")[:500] if data.note else None},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})
    db.commit()
    return sub


# ---------------------------------------------------------------------- the daily pass


def check(
    db: Session,
    company_id: str | None = None,
    *,
    now: datetime | None = None,
    provider_factory=None,
) -> int:
    """Daily pass: renewal reviews, cancel reminders, missed-renewal alerts and closing the cards
    of cancelled subscriptions. Returns the number of subscriptions acted on."""
    now = now or utcnow()
    if company_id is not None:
        company_ids = [company_id]
    else:
        company_ids = list(
            db.scalars(
                select(Subscription.company_id)
                .distinct()
                .join(Company, Company.id == Subscription.company_id)
                .where(
                    Subscription.status.in_(OPEN_STATUSES),
                    Company.status == CompanyStatus.ACTIVE,
                )
            )
        )
    acted = 0
    for cid in company_ids:
        try:
            acted += _check_company(db, cid, now, provider_factory or registry.provider_for)
            db.commit()
        except Exception:  # one company must never stop the pass
            db.rollback()
            logger.exception("subscription check failed for company %s", cid)
    return acted


def _check_company(db: Session, company_id: str, now: datetime, factory) -> int:
    today = now.date()
    alert_days = policy.load_policy(db, company_id).subscription_renewal_alert_days
    subs = list(
        db.scalars(
            select(Subscription)
            .where(Subscription.company_id == company_id, Subscription.status.in_(OPEN_STATUSES))
            .order_by(Subscription.next_renewal_on, Subscription.id)
        )
    )
    acted = 0
    for sub in subs:
        due = sub.next_renewal_on
        if sub.status == SubscriptionStatus.CANCELLED:
            acted += 1 if _close_cancelled_card(db, sub, today, now, factory) else 0
            continue
        if due is None or sub.status == SubscriptionStatus.LAPSED:
            continue
        overdue = today > due + timedelta(days=MISSED_AFTER_DAYS)
        if sub.status == SubscriptionStatus.CANCEL_REQUESTED:
            if overdue:  # the vendor did not charge: it is cancelled in effect
                sub.status = SubscriptionStatus.CANCELLED.value
                sub.updated_at = now
                audit.record(
                    db,
                    company_id=company_id,
                    principal=None,
                    action="subscription.cancelled",
                    target_type="subscription",
                    target_id=sub.id,
                    summary=f"{sub.vendor} did not renew after the cancellation request",
                )
                acted += 1
            continue
        if overdue and not sub.card_id:
            # A subscription paid with the owner's own card (manual mode): Foundry never sees
            # its charges, so silence is not evidence. Ask the person instead of alerting "missed"
            # and assume the usual cycle so reviews continue.
            sub.next_renewal_on = add_period(due, sub.recurrence)
            sub.updated_at = now
            alerts.raise_alert(
                db,
                company_id,
                kind="subscription_confirm_renewal",
                severity=AlertSeverity.INFO,
                title=f"Did {sub.vendor} renew on {due}?",
                body="This subscription is paid with your own card, so Foundry cannot see its "
                f"charges. If you cancelled it on {sub.vendor_domain}, press 'Mark cancelled' in "
                "Wallet > Subscriptions; otherwise nothing is needed.",
                related_type="subscription",
                related_id=sub.id,
                dedupe_key=f"subscription_confirm:{sub.id}:{due}",
                wake_founder=False,
            )
            acted += 1
            continue
        if overdue:
            sub.status = SubscriptionStatus.LAPSED.value
            sub.updated_at = now
            alerts.raise_alert(
                db,
                company_id,
                kind="subscription_missed_renewal",
                severity=AlertSeverity.INFO,
                title=f"{sub.vendor} did not renew when expected",
                body=f"No charge was seen by {due}. The subscription may have lapsed.",
                related_type="subscription",
                related_id=sub.id,
                dedupe_key=f"subscription_missed:{sub.id}:{due}",
            )
            audit.record(
                db,
                company_id=company_id,
                principal=None,
                action="subscription.lapsed",
                target_type="subscription",
                target_id=sub.id,
                summary=f"{sub.vendor} renewal not seen by {due}: marked lapsed",
            )
            acted += 1
            continue
        if (
            sub.status == SubscriptionStatus.ACTIVE
            and today >= due - timedelta(days=alert_days)
            and not _reviewed_this_cycle(db, sub)
        ):
            acted += 1 if _request_review(db, sub, now) else 0
        elif sub.status == SubscriptionStatus.REVIEW_DUE and _unanswered(db, sub, now):
            _request_cancel(db, sub, now, why="no answer to the renewal review within 48 hours")
            acted += 1
    return acted


def _reviewed_this_cycle(db: Session, sub: Subscription) -> bool:
    """The review task of the current renewal exists (and was answered: the status is active)."""
    task = db.get(Task, sub.review_task_id) if sub.review_task_id else None
    return task is not None and task.idempotency_key == f"subrev:{sub.id}:{sub.next_renewal_on}"


def _request_review(db: Session, sub: Subscription, now: datetime) -> bool:
    agent = _owner_agent(db, sub, now)
    if agent is None:
        # Nobody to ask: a person decides.
        _request_cancel(db, sub, now, why="no active agent owns this subscription")
        return True
    due = sub.next_renewal_on
    data = TaskCreate(
        title=f"Review subscription: {sub.vendor} renews {due}"[:300],
        description=(
            f"{sub.vendor} ({sub.item}) renews on {due} for about {_money(sub.amount_micros)} "
            f"({sub.recurrence}). Decide whether it is still worth it, then call "
            f"company_review_subscription with subscription_id={sub.id} and decision keep or "
            "cancel, with a short reason. A cancellation is carried out by a person on the "
            "vendor's site. If you do not answer within 48 hours it goes to a person to cancel."
        ),
        # a short management decision: it stays on the company agent runtime (never a coding CLI),
        # which hands the reviewer company_review_subscription for this task
        kind="routine",
        priority="high",
        assignee_agent_id=agent.id,
        due_at=now + REVIEW_ANSWER_WINDOW,
        idempotency_key=f"subrev:{sub.id}:{due}",
    )
    task, _ = tasks.create_system_task(db, sub.company_id, data, now=now)
    sub.review_task_id = task.id
    sub.status = SubscriptionStatus.REVIEW_DUE.value
    sub.updated_at = now
    audit.record(
        db,
        company_id=sub.company_id,
        principal=None,
        action="subscription.review_requested",
        target_type="subscription",
        target_id=sub.id,
        summary=f"Asked {agent.name} to review the {sub.vendor} renewal on {due}",
        data={"task_id": task.id, "agent_id": agent.id},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})
    return True


def _unanswered(db: Session, sub: Subscription, now: datetime) -> bool:
    """The review task carries its own deadline (``due_at``, 48 hours after it was created): an
    answer by the agent after that moment's request would have left ``review_due``."""
    task = db.get(Task, sub.review_task_id) if sub.review_task_id else None
    if task is None or task.due_at is None:
        return True
    return now >= task.due_at


def _request_cancel(db: Session, sub: Subscription, now: datetime, *, why: str, by=None) -> None:
    """A person must cancel it on the vendor's site: a task for them and an alert."""
    due = sub.next_renewal_on
    sub.status = SubscriptionStatus.CANCEL_REQUESTED.value
    sub.updated_at = now
    assignee = _human_assignee(db, sub.company_id)
    task_id = None
    if assignee is not None:
        data = TaskCreate(
            title=f"Cancel {sub.vendor} before {due}"[:300] if due else f"Cancel {sub.vendor}",
            description=(
                f"Why: {why}\n\nOpen {sub.vendor_domain}, find billing or subscription settings "
                f"and cancel '{sub.item}'. Then press 'Mark cancelled' in the Wallet > "
                "Subscriptions. Foundry cannot cancel it for you, and freezing the card is not a "
                "cancellation: the vendor may keep trying to charge it."
            ),
            kind="work",
            priority="high",
            assignee_ref=assignee,
            due_at=datetime.combine(due, time(23, 59), tzinfo=UTC) if due else None,
            idempotency_key=f"subcancel:{sub.id}:{due}",
        )
        task, _ = tasks.create_system_task(db, sub.company_id, data, now=now)
        task_id = task.id
        sub.cancel_task_id = task.id
    alerts.raise_alert(
        db,
        sub.company_id,
        kind="subscription_cancel_needed",
        severity=AlertSeverity.WARNING,
        title=f"Cancel {sub.vendor} before {due}" if due else f"Cancel {sub.vendor}",
        body=f"{why}. A person has to cancel it on {sub.vendor_domain}.",
        related_type="subscription",
        related_id=sub.id,
        dedupe_key=f"subscription_cancel:{sub.id}:{due}",
        wake_founder=False,
    )
    audit.record(
        db,
        company_id=sub.company_id,
        principal=by,
        action="subscription.cancel_requested",
        target_type="subscription",
        target_id=sub.id,
        summary=f"Cancellation of {sub.vendor} requested: {why[:200]}",
        data={"task_id": task_id},
    )
    events.emit(db, sub.company_id, "subscription.updated", {"subscription_id": sub.id})


def _close_cancelled_card(
    db: Session, sub: Subscription, today: date, now: datetime, factory
) -> bool:
    """Once the paid period has ended, a cancelled subscription's card is closed."""
    if not sub.card_id or (sub.next_renewal_on and today < sub.next_renewal_on):
        return False
    card = db.get(AgentCard, sub.card_id)
    if card is None or card.company_id != sub.company_id or card.status != CardStatus.ACTIVE:
        return False
    account = db.get(CardAccount, card.card_account_id)
    if account is None:
        return False
    from app.services import card_reconcile  # local: card_reconcile imports this module

    provider = None
    try:
        provider = factory(db, account)
        return card_reconcile.close_card(db, provider, card, "subscription cancelled", now)
    except Exception:
        logger.warning("could not close the card of cancelled subscription %s", sub.id)
        return False
    finally:
        if provider is not None:
            registry.release(provider)


# ---------------------------------------------------------------------- helpers


def _owner_agent(db: Session, sub: Subscription, now: datetime | None = None) -> Agent | None:
    """The owning agent, else its manager, else the Co-founder."""
    agent = db.get(Agent, sub.agent_id) if sub.agent_id else None
    seen: set[str] = set()
    while agent is not None and agent.id not in seen:
        seen.add(agent.id)
        if agent.company_id == sub.company_id and agent.status in LIVE_AGENT:
            return agent
        agent = db.get(Agent, agent.manager_agent_id) if agent.manager_agent_id else None
    return wakes.active_founder(db, sub.company_id)


def _human_assignee(db: Session, company_id: str) -> str | None:
    """``member:<id>`` of the configured checkout person, else the first active owner."""
    configured = policy.load_policy(db, company_id).purchase_checkout_member_id
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(
        db.scalars(
            select(CompanyMember.id)
            .where(
                CompanyMember.company_id == company_id,
                CompanyMember.status == MemberStatus.ACTIVE,
                CompanyMember.role.in_([MemberRole.OWNER.value, MemberRole.ADMIN.value]),
            )
            .order_by(CompanyMember.role.desc(), CompanyMember.created_at, CompanyMember.id)
        )
    )
    for member_id in candidates:
        member = db.get(CompanyMember, member_id)
        if (
            member is not None
            and member.company_id == company_id
            and member.status == MemberStatus.ACTIVE
            and member.role != MemberRole.VIEWER
        ):
            return f"member:{member.id}"
    return None


def _finish_task(db: Session, task_id: str | None, company_id: str, summary: str, now: datetime):
    task = db.get(Task, task_id) if task_id else None
    if task is None or task.company_id != company_id:
        return
    if task.status not in (TaskStatus.READY, TaskStatus.IN_PROGRESS):
        return
    try:
        with db.begin_nested():
            if task.status == TaskStatus.READY:
                task.attempt_count += 1
                tasks.transition(
                    db, task, TaskStatus.IN_PROGRESS, principal=None, reason="done", now=now
                )
            tasks.finish_success(
                db, task, summary=summary, output_refs=None, principal=None, run_id=None, now=now
            )
    except Exception:
        logger.exception("could not complete task %s", task.id)


def _cancel_task(db: Session, task_id: str, company_id: str, reason: str, now: datetime) -> None:
    task = db.get(Task, task_id)
    if task is None or task.company_id != company_id:
        return
    if task.status in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED):
        return
    try:
        with db.begin_nested():
            tasks.transition(db, task, TaskStatus.CANCELLED, principal=None, reason=reason, now=now)
    except Exception:
        logger.exception("could not cancel task %s", task.id)


def _price_key(subscription_id: str) -> str:
    return f"subscription_price_increase:{subscription_id}"


def _money(micros: int) -> str:
    cents = abs(micros) // 10_000
    return f"{'-' if micros < 0 else ''}${cents // 100:,}.{cents % 100:02d}"
