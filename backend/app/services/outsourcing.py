"""M22 outsourcing: freelance jobs HR drafts, a person approves, posts and records.

Trust model (docs/security.md): Fiverr has no public buyer API and Upwork's API needs an approved
partner key, and both marketplaces' terms forbid unauthorized automation and scraping, so Foundry
**never posts, messages, hires or pays on a marketplace by itself** and makes no network call here.
HR prepares the job (``draft``); a person approves it (an ``outsource_job`` approval that is ALWAYS
decided by a human, whatever the policy); a person then does the outward-facing steps on the
marketplace's own site and records what happened (``posted`` / ``hired`` / ``delivered`` /
``paid``). Money is integer micro-USD. A recorded payment becomes a ``verified`` external
expense: the person paid, Foundry moved nothing.

Every transition is validated against ``_ALLOWED`` and audited in the same transaction.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, InvalidTransition, LimitExceeded, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.guardrails import dlp
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company
from app.models.enums import ApprovalStatus, Approver, ExpenseSource
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.outsourcing import (
    OPEN_OUTSOURCE_STATUSES,
    OutsourceJob,
    OutsourceStatus,
)
from app.models.work import Task
from app.schemas.human_work import OutsourceDraft
from app.services import (
    approvals,
    audit,
    events,
    hr_access,
    ledger,
    policy,
    secrets_scan,
    wakes,
)
from app.services.pagination import paginate

logger = logging.getLogger("company_os.outsourcing")

MAX_OPEN_JOBS = 5
S = OutsourceStatus
MARKETPLACE_LABEL = {"fiverr": "Fiverr", "upwork": "Upwork", "other": "Other marketplace"}
# Which transitions exist at all (the endpoint-specific checks come on top).
_ALLOWED: dict[OutsourceStatus, frozenset[OutsourceStatus]] = {
    S.DRAFT: frozenset({S.PENDING_APPROVAL, S.CANCELLED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.DECLINED, S.CANCELLED}),
    S.APPROVED: frozenset({S.POSTED, S.HIRED, S.CANCELLED}),
    S.POSTED: frozenset({S.HIRED, S.CANCELLED}),
    S.HIRED: frozenset({S.HIRED, S.DELIVERED, S.CLOSED, S.CANCELLED}),
    S.DELIVERED: frozenset({S.CLOSED, S.CANCELLED}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
    S.DECLINED: frozenset(),
}
# The marketplace's own domains (the listing URL of a fiverr/upwork job must be on one of them).
_DOMAINS = {"fiverr": ("fiverr.com",), "upwork": ("upwork.com",)}


# ---------------------------------------------------------------- helpers


def usd_to_micros(amount: float | Decimal | str) -> int:
    return int((Decimal(str(amount)) * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))


def get_job(db: Session, company_id: str, job_id: str) -> OutsourceJob:
    job = db.scalar(
        select(OutsourceJob).where(OutsourceJob.id == job_id, OutsourceJob.company_id == company_id)
    )
    if job is None:
        raise NotFound("Outsource job not found")
    return job


def _reject_unsafe(db: Session, company_id: str, text: str, what: str) -> None:
    """The brief goes to strangers on the internet: no secrets, no personal data (e-mail addresses,
    phone numbers), no passages of memory marked internal-only, no card numbers."""
    findings = dlp.scan_text(text, internal=dlp.internal_snippets(db, company_id))
    kinds = sorted({f.kind for f in findings})
    if secrets_scan.contains_card_number(text):
        kinds = sorted({*kinds, "card_number"})
    if kinds:
        raise ValidationFailed(
            f"The {what} must not contain secrets or personal data ({', '.join(kinds)}). It is "
            "posted publicly on a marketplace: remove them or say where the freelancer will get "
            "them once hired.",
            details=[{"loc": ["body", what], "msg": "unsafe content", "kinds": kinds}],
        )


def _transition(job: OutsourceJob, to: OutsourceStatus, verb: str) -> OutsourceStatus:
    current = OutsourceStatus(job.status)
    if to not in _ALLOWED[current]:
        raise InvalidTransition(
            f"Cannot {verb}: the job is {current.value}", details={"from": current.value, "to": to}
        )
    job.status = to.value
    job.updated_at = utcnow()
    return current


def _event(job: OutsourceJob) -> dict[str, Any]:
    return {"job_id": job.id, "status": job.status, "task_id": job.task_id}


def _audit(
    db: Session,
    principal: Principal | None,
    job: OutsourceJob,
    action: str,
    summary: str,
    **data: Any,
) -> None:
    audit.record(
        db,
        company_id=job.company_id,
        principal=principal,
        action=f"outsource.{action}",
        target_type="outsource_job",
        target_id=job.id,
        summary=f"{summary}: {job.title[:100]}",
        data={"status": job.status, **data},
    )
    events.emit(db, job.company_id, "outsource.updated", _event(job))


def open_url(job: OutsourceJob) -> str | None:
    """A public page of the marketplace to start from (no fetching, no scraping: a link the person
    opens in their own browser)."""
    q = quote(job.title[:120], safe="")
    if job.marketplace == "fiverr":
        return f"https://www.fiverr.com/search/gigs?query={q}"
    if job.marketplace == "upwork":
        return f"https://www.upwork.com/nx/search/talent/?q={q}"
    return None


def validate_listing_url(marketplace: str, url: str, what: str = "listing_url") -> str:
    """https only, no credentials in the URL, and for Fiverr / Upwork a page on the marketplace's
    own domain (so a pasted look-alike or phishing link is refused)."""
    text = url.strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        host, parts = "", None  # type: ignore[assignment]
    problem: str | None = None
    if parts is None or parts.scheme != "https" or not host:
        problem = "must be an https:// link"
    elif parts.username or parts.password:
        problem = "must not contain a username or password"
    elif marketplace in _DOMAINS and not any(
        host == d or host.endswith("." + d) for d in _DOMAINS[marketplace]
    ):
        problem = f"must be a page on {_DOMAINS[marketplace][0]}"
    if problem:
        raise ValidationFailed(
            f"The link {problem}", details=[{"loc": ["body", what], "msg": problem}]
        )
    return text


def _committed_to_jobs(db: Session, company_id: str, *, exclude_job_id: str | None = None) -> int:
    """What other open jobs may still cost: budgets of jobs that are pending, approved, posted,
    hired or delivered and not paid yet (the agreed price when hired)."""
    total = 0
    for job in db.scalars(
        select(OutsourceJob).where(
            OutsourceJob.company_id == company_id,
            OutsourceJob.status.in_([s.value for s in OPEN_OUTSOURCE_STATUSES]),
            OutsourceJob.paid_micros.is_(None),
        )
    ):
        if job.id != exclude_job_id:
            total += (
                job.agreed_price_micros
                if job.agreed_price_micros is not None
                else job.budget_micros
            )
    return total


def check_budget(
    db: Session, company_id: str, budget_micros: int, *, exclude_job_id: str | None = None
) -> None:
    """The per-job ceiling (policy, owner/admin only) and, when the company has a monthly budget,
    what is left of it after usage and what other open jobs may still cost."""
    limits = policy.load_policy(db, company_id)
    if budget_micros > limits.outsource_max_job_micros:
        raise LimitExceeded(
            "The budget is above the per-job ceiling of "
            f"${limits.outsource_max_job_micros / 1e6:,.2f} (an owner or admin can change it under "
            "People ops > Freelancers)",
            details={
                "limit": "outsource_max_job_micros",
                "max_micros": limits.outsource_max_job_micros,
                "requested_micros": budget_micros,
            },
        )
    if limits.monthly_budget_micros is not None:
        company = db.get(Company, company_id)
        month = ledger.periods_for(utcnow(), company.timezone if company else "UTC").month
        spent = ledger.committed_micros(db, company_id, period_month=month)
        remaining = (
            limits.monthly_budget_micros
            - spent
            - _committed_to_jobs(db, company_id, exclude_job_id=exclude_job_id)
        )
        if budget_micros > remaining:
            raise LimitExceeded(
                "The budget does not fit in what is left of this month's company budget",
                details={
                    "limit": "company_monthly",
                    "remaining_micros": max(0, remaining),
                    "requested_micros": budget_micros,
                },
            )


# ---------------------------------------------------------------- HR drafts


def draft(db: Session, ctx: CompanyContext, data: OutsourceDraft) -> tuple[OutsourceJob, Approval]:
    """HR prepares a freelance job and its (always human) approval. Nothing leaves Foundry.
    Commits."""
    hr = hr_access.require_hr(db, ctx, "draft outsourced jobs")
    for text, what in (
        (data.title, "title"),
        (data.brief, "brief"),
        (" ".join(data.skills), "skills"),
    ):
        if text:
            _reject_unsafe(db, ctx.company_id, text, what)
    budget = usd_to_micros(data.budget_usd)
    if budget <= 0:
        raise ValidationFailed("The budget must be more than zero")
    open_jobs = db.scalar(
        select(func.count(OutsourceJob.id)).where(
            OutsourceJob.company_id == ctx.company_id,
            OutsourceJob.status.in_([s.value for s in OPEN_OUTSOURCE_STATUSES]),
        )
    )
    if (open_jobs or 0) >= MAX_OPEN_JOBS:
        raise LimitExceeded(
            f"There are already {MAX_OPEN_JOBS} open outsourced jobs; wait for one to finish",
            details={"limit": "open_outsource_jobs", "max": MAX_OPEN_JOBS},
        )
    check_budget(db, ctx.company_id, budget)
    task_id = None
    if data.task_id:
        task_id = db.scalar(
            select(Task.id).where(Task.id == data.task_id, Task.company_id == ctx.company_id)
        )
        if task_id is None:
            raise NotFound("Task not found")
    job = OutsourceJob(
        company_id=ctx.company_id,
        created_by_agent_id=hr.id,
        task_id=task_id,
        title=data.title,
        brief=data.brief,
        skills=list(data.skills),
        marketplace=data.marketplace,
        budget_micros=budget,
        status=S.DRAFT.value,
    )
    db.add(job)
    db.flush()
    approval = approvals.create_approval_record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        kind="outsource_job",
        title=f"Outsource on {MARKETPLACE_LABEL[data.marketplace]}: {data.title[:200]}",
        reason=data.brief[:2000],
        amount_micros=budget,
        vendor=MARKETPLACE_LABEL[data.marketplace],
        risk="medium",
        evidence={"job_id": job.id, "marketplace": data.marketplace, "skills": list(data.skills)},
        scope={"op": "post", "job_id": job.id, "budget_micros": budget},
        task_id=task_id,
        subject=("outsource_job", job.id),
        approver=Approver.HUMAN,
    )
    job.approval_id = approval.id
    _transition(job, S.PENDING_APPROVAL, "send the job for approval")
    _audit(
        db,
        ctx.principal,
        job,
        "drafted",
        f"{hr.name} drafted a freelance job on {MARKETPLACE_LABEL[data.marketplace]} "
        f"(budget ${budget / 1e6:,.2f})",
        budget_micros=budget,
        marketplace=data.marketplace,
        approval_id=approval.id,
    )
    db.commit()
    return job, approval


def apply_decision(db: Session, ctx: CompanyContext, approval: Approval, decision: str) -> None:
    """``approvals.decide`` / the deadline sweep hand the decision of an ``outsource_job`` approval
    here (no commit). Approve -> approved (or the raised budget); anything else -> declined, the
    job's budget unchanged. HR is woken by the approval machinery (it asked for the approval)."""
    job = db.scalar(
        select(OutsourceJob).where(
            OutsourceJob.id == approval.subject_id,
            OutsourceJob.company_id == approval.company_id,
        )
    )
    if job is None:
        return
    raising = (approval.scope or {}).get("op") == "raise_budget"
    note = (approval.decision_note or "").strip()
    if decision == "approve":
        if raising:
            new_budget = int(approval.scope.get("new_budget_micros") or 0)
            if new_budget > job.budget_micros:
                job.budget_micros = new_budget
            job.updated_at = utcnow()
            _audit(
                db,
                ctx.principal,
                job,
                "budget_raised",
                "Raised the budget",
                budget_micros=job.budget_micros,
            )
        elif job.status == S.PENDING_APPROVAL:
            _transition(job, S.APPROVED, "approve the job")
            _audit(db, ctx.principal, job, "approved", "A person approved the freelance job")
        return
    if raising:
        _audit(db, ctx.principal, job, "budget_raise_declined", "A person declined a budget raise")
        return
    if job.status == S.PENDING_APPROVAL:
        _transition(job, S.DECLINED, "decline the job")
        if note:
            job.notes = note[:2000]
        _audit(
            db,
            ctx.principal,
            job,
            "declined",
            "A person declined the freelance job"
            if ctx.principal is not None
            else "The freelance job expired",
            decision=decision,
        )


def raise_budget(
    db: Session, ctx: CompanyContext, job_id: str, new_budget_usd: float, reason: str | None
) -> tuple[OutsourceJob, Approval]:
    """Owner/admin: ask for a higher budget. A NEW approval (for the difference) decides it; until a
    person approves it the job keeps its budget. Commits."""
    require_human_admin(ctx, "raise an outsourcing budget")
    job = get_job(db, ctx.company_id, job_id)
    if job.status not in (S.APPROVED, S.POSTED, S.HIRED):
        raise InvalidTransition(
            f"Cannot raise the budget: the job is {job.status}", details={"from": job.status}
        )
    new_budget = usd_to_micros(new_budget_usd)
    if new_budget <= job.budget_micros:
        raise ValidationFailed("The new budget must be above the current one")
    check_budget(db, ctx.company_id, new_budget, exclude_job_id=job.id)
    for waiting in db.scalars(
        select(Approval).where(
            Approval.company_id == ctx.company_id,
            Approval.subject_type == "outsource_job",
            Approval.subject_id == job.id,
            Approval.status == ApprovalStatus.PENDING,
        )
    ):
        if (waiting.scope or {}).get("op") == "raise_budget":
            raise Conflict("A budget raise for this job is already waiting for approval")
    diff = new_budget - job.budget_micros
    approval = approvals.create_approval_record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        kind="outsource_job",
        title=f"Raise the budget of '{job.title[:150]}' by ${diff / 1e6:,.2f}",
        reason=(reason or "")[:2000] or None,
        amount_micros=diff,
        vendor=MARKETPLACE_LABEL.get(job.marketplace),
        risk="medium",
        evidence={"job_id": job.id, "from_micros": job.budget_micros, "to_micros": new_budget},
        scope={"op": "raise_budget", "job_id": job.id, "new_budget_micros": new_budget},
        task_id=job.task_id,
        subject=("outsource_job", job.id),
        approver=Approver.HUMAN,
        requested_by_agent_id=None,
    )
    _audit(
        db,
        ctx.principal,
        job,
        "budget_raise_requested",
        f"Asked to raise the budget to ${new_budget / 1e6:,.2f}",
        new_budget_micros=new_budget,
        approval_id=approval.id,
    )
    db.commit()
    return job, approval


# ---------------------------------------------------------------- the person's steps


def posted(db: Session, ctx: CompanyContext, job_id: str, listing_url: str) -> OutsourceJob:
    """The person posted the job on the marketplace's own site and records where. Commits."""
    require_human_admin(ctx, "record a posted job")
    job = get_job(db, ctx.company_id, job_id)
    url = validate_listing_url(job.marketplace, listing_url)
    _transition(job, S.POSTED, "record the post")
    job.listing_url = url
    _audit(db, ctx.principal, job, "posted", "Recorded that the job was posted", listing_url=url)
    db.commit()
    return job


def hired(
    db: Session,
    ctx: CompanyContext,
    job_id: str,
    *,
    freelancer_name: str,
    freelancer_profile_url: str | None,
    agreed_price_usd: float,
) -> OutsourceJob:
    """The person hired a freelancer on the marketplace and records who and the agreed price, which
    may not exceed the approved budget (raise the budget first: a new approval). May be re-recorded
    until something was paid. Commits."""
    require_human_admin(ctx, "record a hire")
    job = get_job(db, ctx.company_id, job_id)
    if job.paid_micros is not None:
        raise Conflict("A payment is already recorded; the hire can no longer be changed")
    agreed = usd_to_micros(agreed_price_usd)
    if agreed > job.budget_micros:
        raise ValidationFailed(
            f"The agreed price (${agreed / 1e6:,.2f}) is above the approved budget "
            f"(${job.budget_micros / 1e6:,.2f}). Use 'Raise budget' to ask for a new approval "
            "first.",
            details=[
                {
                    "loc": ["body", "agreed_price_usd"],
                    "msg": "above the budget",
                    "raise_budget": True,
                }
            ],
        )
    profile = (
        validate_listing_url(job.marketplace, freelancer_profile_url, "freelancer_profile_url")
        if freelancer_profile_url and freelancer_profile_url.strip()
        else None
    )
    name = freelancer_name.strip()
    _reject_unsafe_name(name)
    _transition(job, S.HIRED, "record the hire")
    job.freelancer_name = name
    job.freelancer_profile_url = profile
    job.agreed_price_micros = agreed
    _audit(
        db,
        ctx.principal,
        job,
        "hired",
        f"Recorded the hire of {name} (agreed ${agreed / 1e6:,.2f})",
        agreed_price_micros=agreed,
    )
    db.commit()
    return job


def _reject_unsafe_name(name: str) -> None:
    if any(f.kind in ("secret", "own_token") for f in dlp.scan_text(name)):
        raise ValidationFailed("The name looks like a secret")


def delivered(
    db: Session, ctx: CompanyContext, job_id: str, *, artifact_ids: list[str], notes: str | None
) -> OutsourceJob:
    """The freelancer delivered. The person attached the files as artifacts; the linked task (if
    any) gets them as outputs and its assignee agent is woken. Commits."""
    require_human_admin(ctx, "record a delivery")
    job = get_job(db, ctx.company_id, job_id)
    refs: list[str] = []
    for artifact_id in dict.fromkeys(artifact_ids):
        found = db.scalar(
            select(Artifact.id).where(
                Artifact.id == artifact_id, Artifact.company_id == ctx.company_id
            )
        )
        if found is None:
            raise NotFound("Artifact not found")
        refs.append(found)
    _transition(job, S.DELIVERED, "record the delivery")
    job.delivered_artifact_ids = refs
    if notes:
        job.notes = notes.strip()[:2000]
    if job.task_id:
        task = db.scalar(
            select(Task).where(Task.id == job.task_id, Task.company_id == ctx.company_id)
        )
        if task is not None:
            task.output_refs = list(dict.fromkeys([*task.output_refs, *refs]))
            task.updated_at = utcnow()
            events.emit(
                db, ctx.company_id, "task.updated", {"task_id": task.id, "status": task.status}
            )
            if task.assignee_agent_id:
                wakes.wake_agent(
                    db,
                    ctx.company_id,
                    task.assignee_agent_id,
                    "outsourcing",
                    f"The freelancer delivered '{job.title[:100]}': {len(refs)} file(s) are now "
                    f"outputs of task '{task.title[:100]}'. Review them and carry on.",
                    ref_type="outsource_job",
                    ref_id=job.id,
                )
    if job.created_by_agent_id:
        wakes.wake_agent(
            db,
            ctx.company_id,
            job.created_by_agent_id,
            "outsourcing",
            f"The freelance job '{job.title[:100]}' was delivered.",
            ref_type="outsource_job",
            ref_id=job.id,
        )
    _audit(db, ctx.principal, job, "delivered", "Recorded the delivery", artifact_ids=refs)
    db.commit()
    return job


def paid(db: Session, ctx: CompanyContext, job_id: str, amount_usd: float) -> OutsourceJob:
    """The person paid the freelancer on the marketplace; record it as a ``verified`` expense
    (integer micro-USD) against the company and the task's project. Never above the agreed price.
    Foundry moves no money. Commits."""
    require_human_admin(ctx, "record a payment")
    job = get_job(db, ctx.company_id, job_id)
    if job.status not in (S.HIRED, S.DELIVERED):
        raise InvalidTransition(
            f"Cannot record a payment: the job is {job.status}", details={"from": job.status}
        )
    if job.agreed_price_micros is None:
        raise Conflict("Record the hire (with the agreed price) first")
    if job.paid_micros is not None:
        raise Conflict("A payment is already recorded for this job")
    amount = usd_to_micros(amount_usd)
    if amount <= 0:
        raise ValidationFailed("The amount must be more than zero")
    if amount > job.agreed_price_micros:
        raise ValidationFailed(
            f"The payment (${amount / 1e6:,.2f}) is above the agreed price "
            f"(${job.agreed_price_micros / 1e6:,.2f}). Re-record the hire with the new agreed "
            "price (raise the budget first if needed): that takes a new approval.",
            details=[{"loc": ["body", "amount_usd"], "msg": "above the agreed price"}],
        )
    project_id = None
    if job.task_id:
        project_id = db.scalar(
            select(Task.project_id).where(Task.id == job.task_id, Task.company_id == ctx.company_id)
        )
    company = db.get(Company, ctx.company_id)
    tz = ZoneInfo(company.timezone if company and company.timezone else "UTC")
    expense = ExternalExpense(
        company_id=ctx.company_id,
        vendor=f"{MARKETPLACE_LABEL[job.marketplace]}: {job.freelancer_name or 'freelancer'}"[:200],
        description=f"Outsourced job: {job.title}"[:500],
        amount_micros=amount,
        recurrence="one_time",
        project_id=project_id,
        incurred_on=utcnow().astimezone(tz).date(),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
        cost_kind="verified",
        source=ExpenseSource.OUTSOURCING.value,
    )
    db.add(expense)
    db.flush()
    job.paid_micros = amount
    job.ledger_entry_id = expense.id
    job.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="expense.created",
        target_type="expense",
        target_id=expense.id,
        summary=f"Recorded a verified expense: {expense.vendor}",
        data={"amount_micros": amount, "source": "outsourcing", "outsource_job_id": job.id},
    )
    events.emit(
        db,
        ctx.company_id,
        "expense.created",
        {"expense_id": expense.id, "vendor": expense.vendor, "amount_micros": amount},
    )
    _audit(
        db,
        ctx.principal,
        job,
        "paid",
        f"Recorded a payment of ${amount / 1e6:,.2f} (paid by a person, not by Foundry)",
        paid_micros=amount,
        cost_kind="verified",
        ledger_entry_id=expense.id,
    )
    db.commit()
    return job


def close(db: Session, ctx: CompanyContext, job_id: str) -> OutsourceJob:
    require_human_admin(ctx, "close an outsourced job")
    job = get_job(db, ctx.company_id, job_id)
    _transition(job, S.CLOSED, "close the job")
    _audit(db, ctx.principal, job, "closed", "Closed the freelance job")
    db.commit()
    return job


def cancel(db: Session, ctx: CompanyContext, job_id: str, reason: str | None) -> OutsourceJob:
    """Stop an open job. A pending approval is withdrawn (rejected by the person cancelling)."""
    require_human_admin(ctx, "cancel an outsourced job")
    job = get_job(db, ctx.company_id, job_id)
    if job.paid_micros is not None:
        raise Conflict("A payment is recorded: close the job instead of cancelling it")
    was = _transition(job, S.CANCELLED, "cancel the job")
    if reason and reason.strip():
        job.notes = reason.strip()[:2000]
    if was == S.PENDING_APPROVAL and job.approval_id:
        approval = db.scalar(
            select(Approval).where(
                Approval.id == job.approval_id, Approval.company_id == ctx.company_id
            )
        )
        if approval is not None and approval.status == ApprovalStatus.PENDING:
            now = utcnow()
            approval.status = ApprovalStatus.REJECTED
            approval.decided_by_type = ctx.principal.type.value
            approval.decided_by_id = ctx.principal.id
            approval.decided_at = now
            approval.decision_note = "The job was cancelled"
            approval.updated_at = now
            events.emit(
                db,
                ctx.company_id,
                "approval.decided",
                {
                    "approval_id": approval.id,
                    "status": "rejected",
                    "kind": approval.kind,
                    "decided_by_type": ctx.principal.type.value,
                },
            )
    _audit(
        db,
        ctx.principal,
        job,
        "cancelled",
        "Cancelled the freelance job",
        reason=(reason or "")[:300],
    )
    db.commit()
    return job


# ---------------------------------------------------------------- reading


def list_jobs(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    limit: int,
    cursor: str | None,
):
    if not ctx.principal.is_human:
        from app.core.errors import Forbidden

        raise Forbidden("Outsourcing is for people; HR drafts through its own tool")
    stmt = select(OutsourceJob).where(OutsourceJob.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(OutsourceJob.status == status)
    return paginate(db, stmt, OutsourceJob, limit=limit, cursor=cursor, descending=True)


def serialize(db: Session, jobs: list[OutsourceJob]) -> list[dict[str, Any]]:
    agent_ids = {j.created_by_agent_id for j in jobs if j.created_by_agent_id}
    names = (
        {i: n for i, n in db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))}
        if agent_ids
        else {}
    )
    task_ids = {j.task_id for j in jobs if j.task_id}
    titles = (
        {i: t for i, t in db.execute(select(Task.id, Task.title).where(Task.id.in_(task_ids)))}
        if task_ids
        else {}
    )
    approval_ids = {j.approval_id for j in jobs if j.approval_id}
    statuses = (
        {
            i: s
            for i, s in db.execute(
                select(Approval.id, Approval.status).where(Approval.id.in_(approval_ids))
            )
        }
        if approval_ids
        else {}
    )
    out = []
    for j in jobs:
        out.append(
            {
                "id": j.id,
                "company_id": j.company_id,
                "created_by_agent_id": j.created_by_agent_id,
                "created_by_agent_name": names.get(j.created_by_agent_id or ""),
                "task_id": j.task_id,
                "task_title": titles.get(j.task_id or ""),
                "title": j.title,
                "brief": j.brief,
                "skills": list(j.skills),
                "marketplace": j.marketplace,
                "budget_micros": j.budget_micros,
                "status": j.status,
                "approval_id": j.approval_id,
                "approval_status": statuses.get(j.approval_id or ""),
                "listing_url": j.listing_url,
                "freelancer_name": j.freelancer_name,
                "freelancer_profile_url": j.freelancer_profile_url,
                "agreed_price_micros": j.agreed_price_micros,
                "paid_micros": j.paid_micros,
                "cost_kind": "verified" if j.paid_micros is not None else None,
                "ledger_entry_id": j.ledger_entry_id,
                "delivered_artifact_ids": list(j.delivered_artifact_ids),
                "notes": j.notes,
                "open_url": open_url(j),
                "created_at": j.created_at,
                "updated_at": j.updated_at,
            }
        )
    return out
