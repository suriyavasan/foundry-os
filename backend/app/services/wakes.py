"""Wake mechanics: how events turn into planning runs of the AI Co-founder and of leads.

Events (a note, a new goal, an escalation, a hire, ...) never create work directly. They add a
reason to the agent's single pending **wake request**; the scheduler tick later turns due
requests into one ``planning`` task:

* **debounced** - a request is not due before ``not_before`` (60 s after the first reason for a
  Founder's note, a few seconds for system events), so a burst becomes one run;
* **coalesced** - at most one open wake task per agent: a due request merges into a wake task
  that has not started yet, or waits until the running one is finished;
* **rate limited** - at most ``policy.max_planning_runs_per_hour`` planning runs per agent and
  hour (wake tasks and planning schedules both count);
* **safe** - a paused company never wakes anyone; a retired/rejected agent drops its request;
* **idempotent** - the wake task's idempotency key is ``wake:<request id>``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.autonomy import Alert, AutonomyIntervention, FounderNote, WakeRequest
from app.models.company import Company
from app.models.enums import (
    TERMINAL_TASK_STATUSES,
    AgentKind,
    AgentStatus,
    AlertStatus,
    ApprovalStatus,
    CompanyStatus,
    GoalStatus,
    NoteStatus,
    TaskKind,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.work import Goal, Project, Task
from app.schemas.work import TaskCreate
from app.services import agent_roles, audit, events, policy

logger = logging.getLogger("company_os.wakes")

WAKE_KEY_PREFIX = "wake:"
NOTE_DEBOUNCE_SECONDS = 60
# M10: input typed into the Command Center (and the Founder's reactions to reflections) is answered
# quickly: a short debounce, and the wake task is dispatched ahead of ordinary work.
COMMAND_DEBOUNCE_SECONDS = 5
EVENT_DELAY_SECONDS = 5
MAX_REASONS = 15
MAX_REASON_CHARS = 300
RATE_WINDOW = timedelta(hours=1)
OPEN_STATUSES = [s for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]
PRE_START = (TaskStatus.BACKLOG, TaskStatus.READY)

TRIGGER_LABELS = {
    "note": "Founder's note",
    "goal": "goal change",
    "project": "project change",
    "escalation": "escalation",
    "hire": "new hire",
    "approval": "approval decided",
    "alert": "alert",
    "assignment": "project assigned",
    "project_done": "project work finished",
    "manual": "manual wake",
    "schedule": "schedule",
    "email": "new e-mail",
    "command": "Command Center input",
    "reflection": "reaction to a reflection",
    # M12
    "initiative": "initiative (idle company)",
    "stall": "stalled work",
    "goal_review": "goal review",
    "retry": "retried wake",
    "budget": "budget block",
    "blocked": "blocked task",
}


# ---------------------------------------------------------------- requests


def active_founder(db: Session, company_id: str) -> Agent | None:
    """The AI Co-founder to wake. A paused founder still collects its wake request (it is
    created when the founder resumes); only a retired one has none."""
    return db.scalar(
        select(Agent).where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED]),
        )
    )


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 3] + "..."


def request_wake(
    db: Session,
    company_id: str,
    agent_id: str,
    trigger: str,
    text: str,
    *,
    ref_type: str | None = None,
    ref_id: str | None = None,
    delay_seconds: int = EVENT_DELAY_SECONDS,
    now: datetime | None = None,
) -> WakeRequest | None:
    """Add a reason to ``agent_id``'s pending wake request (creating it). No commit."""
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
    if agent is None or agent.status in (AgentStatus.RETIRED, AgentStatus.REJECTED):
        return None
    now = now or utcnow()
    reason = {"trigger": trigger, "text": _clip(text), "ref_type": ref_type, "ref_id": ref_id}
    not_before = now + timedelta(seconds=delay_seconds)
    db.flush()
    request = db.scalar(
        select(WakeRequest)
        .where(WakeRequest.company_id == company_id, WakeRequest.agent_id == agent_id)
        .order_by(WakeRequest.created_at)
        .limit(1)
    )
    if request is None:
        request = WakeRequest(
            company_id=company_id,
            agent_id=agent_id,
            trigger=trigger,
            reasons=[reason],
            not_before=not_before,
            created_at=now,
        )
        db.add(request)
    else:
        reasons = list(request.reasons or [])
        if not any(
            r.get("trigger") == trigger and r.get("text") == reason["text"] for r in reasons
        ):
            reasons.append(reason)
        request.reasons = reasons[-MAX_REASONS:]
        # The earliest due time wins: a quick event must not wait behind a long debounce.
        request.not_before = min(request.not_before, not_before)
    db.flush()
    return request


def wake_founder(
    db: Session,
    company_id: str,
    trigger: str,
    text: str,
    *,
    ref_type: str | None = None,
    ref_id: str | None = None,
    delay_seconds: int = EVENT_DELAY_SECONDS,
    actor_agent_id: str | None = None,
    now: datetime | None = None,
) -> WakeRequest | None:
    """Wake the AI Co-founder - unless the founder itself caused the event (no self-wake loops)."""
    founder = active_founder(db, company_id)
    if founder is None or founder.id == actor_agent_id:
        return None
    return request_wake(
        db,
        company_id,
        founder.id,
        trigger,
        text,
        ref_type=ref_type,
        ref_id=ref_id,
        delay_seconds=delay_seconds,
        now=now,
    )


def wake_agent(
    db: Session,
    company_id: str,
    agent_id: str | None,
    trigger: str,
    text: str,
    *,
    ref_type: str | None = None,
    ref_id: str | None = None,
    actor_agent_id: str | None = None,
    delay_seconds: int = EVENT_DELAY_SECONDS,
    now: datetime | None = None,
) -> WakeRequest | None:
    if not agent_id or agent_id == actor_agent_id:
        return None
    return request_wake(
        db,
        company_id,
        agent_id,
        trigger,
        text,
        ref_type=ref_type,
        ref_id=ref_id,
        delay_seconds=delay_seconds,
        now=now,
    )


# ---------------------------------------------------------------- queries


def open_wake_task(db: Session, company_id: str, agent_id: str) -> Task | None:
    return db.scalar(
        select(Task)
        .where(
            Task.company_id == company_id,
            Task.assignee_agent_id == agent_id,
            Task.kind == TaskKind.PLANNING,
            Task.idempotency_key.like(f"{WAKE_KEY_PREFIX}%"),
            Task.status.in_(OPEN_STATUSES),
        )
        .order_by(Task.created_at)
        .limit(1)
    )


def has_pending(db: Session, company_id: str, agent_id: str) -> bool:
    return bool(
        db.scalar(
            select(func.count(WakeRequest.id)).where(
                WakeRequest.company_id == company_id, WakeRequest.agent_id == agent_id
            )
        )
    )


def planning_runs_last_hour(db: Session, company_id: str, agent_id: str, now: datetime) -> int:
    return int(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.company_id == company_id,
                Task.assignee_agent_id == agent_id,
                Task.kind == TaskKind.PLANNING,
                Task.created_at >= now - RATE_WINDOW,
            )
        )
        or 0
    )


def planning_allowed(db: Session, company_id: str, agent_id: str, now: datetime) -> bool:
    limits = policy.load_policy(db, company_id)
    return (
        planning_runs_last_hour(db, company_id, agent_id, now) < limits.max_planning_runs_per_hour
    )


FAILED_ATTENTION_WINDOW = timedelta(days=3)
MAX_ATTENTION_ITEMS = 200
HEARTBEAT_FINGERPRINT_KIND = "heartbeat_fingerprint"
WORK_KIND_VALUES = [TaskKind.WORK.value, TaskKind.HIRING.value, TaskKind.REVIEW.value]


def attention_items(db: Session, company_id: str, now: datetime | None = None) -> list[str]:
    """M12: what an idle planning routine (heartbeat) should look at, as sorted ``kind:id`` strings.
    Work that is merely running, and active goals that have a project, are not in it: they need
    nobody. New notes, failed (and not yet analysed) or blocked tasks, pending approvals, open
    alerts, goals without projects and projects without a lead are."""
    now = now or utcnow()
    items: list[str] = []

    def add(kind: str, ids) -> None:
        items.extend(f"{kind}:{i}" for i in ids)

    add(
        "note",
        db.scalars(
            select(FounderNote.id).where(
                FounderNote.company_id == company_id, FounderNote.status == NoteStatus.NEW
            )
        ),
    )
    failed = list(
        db.scalars(
            select(Task.id)
            .where(
                Task.company_id == company_id,
                Task.status == TaskStatus.FAILED,
                Task.kind.in_([TaskKind.WORK.value, TaskKind.HIRING.value]),
                Task.completed_at >= now - FAILED_ATTENTION_WINDOW,
            )
            .order_by(Task.completed_at.desc())
            .limit(50)
        )
    )
    for task_id in failed:  # a failure whose analysis task was finished has been handled
        handled = db.scalar(
            select(Task.id)
            .where(
                Task.company_id == company_id,
                Task.idempotency_key.like(f"failure:{task_id}:%"),
                Task.status == TaskStatus.DONE,
            )
            .limit(1)
        )
        if handled is None:
            items.append(f"failed:{task_id}")
    add(
        "blocked",
        db.scalars(
            select(Task.id).where(
                Task.company_id == company_id,
                Task.status == TaskStatus.BLOCKED,
                Task.kind.in_(WORK_KIND_VALUES),
            )
        ),
    )
    add(
        "approval",
        db.scalars(
            select(Approval.id).where(
                Approval.company_id == company_id, Approval.status == ApprovalStatus.PENDING
            )
        ),
    )
    add(
        "alert",
        db.scalars(
            select(Alert.id).where(Alert.company_id == company_id, Alert.status == AlertStatus.OPEN)
        ),
    )
    with_project = select(Project.goal_id).where(
        Project.company_id == company_id,
        Project.goal_id.is_not(None),
        Project.status != "cancelled",
    )
    add(
        "goal",
        db.scalars(
            select(Goal.id).where(
                Goal.company_id == company_id,
                Goal.status.in_([GoalStatus.PROPOSED, GoalStatus.ACTIVE]),
                Goal.id.notin_(with_project),
            )
        ),
    )
    add(
        "project",
        db.scalars(
            select(Project.id).where(
                Project.company_id == company_id,
                Project.status.in_(["planned", "active"]),
                Project.lead_agent_id.is_(None),
            )
        ),
    )
    return sorted(items)[:MAX_ATTENTION_ITEMS]


def needs_attention(db: Session, company_id: str, now: datetime | None = None) -> bool:
    """Is there anything an idle planning schedule (heartbeat, standup, ...) should look at?"""
    return bool(attention_items(db, company_id, now))


def has_open_work(db: Session, company_id: str) -> bool:
    return bool(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.company_id == company_id,
                Task.status.in_(OPEN_STATUSES),
                Task.kind.in_(WORK_KIND_VALUES),
            )
        )
    )


def attention_fingerprint(items: list[str]) -> str:
    return hashlib.sha256("\n".join(items).encode()).hexdigest()[:16]


def _fingerprint_row(db: Session, company_id: str) -> AutonomyIntervention | None:
    return db.scalar(
        select(AutonomyIntervention).where(
            AutonomyIntervention.company_id == company_id,
            AutonomyIntervention.kind == HEARTBEAT_FINGERPRINT_KIND,
            AutonomyIntervention.subject_id == company_id,
        )
    )


def heartbeat_skip_reason(db: Session, company_id: str, now: datetime) -> str | None:
    """Why the heartbeat creates no planning run now: nothing needs attention, or the attention
    items are exactly those of the last heartbeat (it already looked at them). No commit."""
    items = attention_items(db, company_id, now)
    row = _fingerprint_row(db, company_id)
    if not items:
        if row is not None:  # a recurring identical set must fire again
            db.delete(row)
        return "nothing to review"
    if row is not None and (row.data or {}).get("fingerprint") == attention_fingerprint(items):
        return "nothing new since the last heartbeat"
    return None


def record_heartbeat(db: Session, company_id: str, now: datetime) -> None:
    """The heartbeat fired: remember what it was asked to look at. No commit."""
    fingerprint = attention_fingerprint(attention_items(db, company_id, now))
    row = _fingerprint_row(db, company_id)
    if row is None:
        row = AutonomyIntervention(
            company_id=company_id,
            kind=HEARTBEAT_FINGERPRINT_KIND,
            subject_type="company",
            subject_id=company_id,
            count=0,
            last_at=now,
            data={"fingerprint": fingerprint},
            created_at=now,
        )
        db.add(row)
    else:
        row.count += 1
        row.last_at = now
        row.data = {"fingerprint": fingerprint}


# ---------------------------------------------------------------- materialization


def _compose(role: str, reasons: list[dict]) -> tuple[str, str]:
    triggers = list(dict.fromkeys(r.get("trigger", "manual") for r in reasons))
    label = ", ".join(TRIGGER_LABELS.get(t, t) for t in triggers[:3])
    if role == agent_roles.FOUNDER:
        title = f"Planning run: {label}"
        tail = (
            "Start with company_get_state. Handle every item above and anything else that needs "
            "you (unprocessed Founder's notes first). Finish with company_complete_task: a 2-3 "
            "sentence summary of what you changed - the Founder reads it."
        )
    else:
        title = f"Management run: {label}"
        tail = (
            "Start with company_get_state (it shows your projects and tasks). Handle every item "
            "above, then finish with company_complete_task and a short summary of what you did."
        )
    lines = ["Why you were woken:", *(f"- {r.get('text', '')}" for r in reasons), "", tail]
    return title[:300], "\n".join(lines)


def _materialize_one(db: Session, request: WakeRequest, now: datetime) -> str | None:
    """Turn a due request into a task, merge it into a waiting wake task, or defer/drop it.
    Returns the id of a task created or merged into; None when nothing happened. No commit."""
    from app.services import tasks  # local: tasks -> wakes (readiness hooks)

    company = db.get(Company, request.company_id)
    if company is None:
        db.delete(request)
        return None
    if company.status != CompanyStatus.ACTIVE:
        return None  # a paused company never wakes; the request waits for the resume
    agent = db.get(Agent, request.agent_id)
    if agent is None or agent.status in (AgentStatus.RETIRED, AgentStatus.REJECTED):
        db.delete(request)
        return None
    if agent.status != AgentStatus.ACTIVE:
        return None  # paused / proposed: wait
    from app.services import ai_setup  # local: ai_setup -> tasks -> wakes

    if not ai_setup.is_ready(db, request.company_id):
        return None  # no working AI model yet: the request waits for the AI setup (M10)
    reasons = list(request.reasons or [])
    existing = open_wake_task(db, request.company_id, agent.id)
    if existing is not None:
        if existing.status in PRE_START:
            # Coalesce: the waiting wake task learns the new reasons; no extra run.
            extra = [
                r for r in reasons if _clip(r.get("text", "")) not in (existing.description or "")
            ]
            if extra:
                existing.description = (
                    (existing.description or "")
                    + "\n"
                    + "\n".join(f"- (also) {r.get('text', '')}" for r in extra)
                )
                existing.updated_at = now
                events.emit(
                    db, request.company_id, "task.updated", tasks.task_event_payload(existing)
                )
            db.delete(request)
            return existing.id
        return None  # running: a follow-up wake is created once it is finished
    if not planning_allowed(db, request.company_id, agent.id, now):
        return None
    role = agent_roles.role_of(db, agent)
    title, description = _compose(role, reasons)
    triggers_now = {r.get("trigger") for r in reasons}
    if triggers_now & {"command", "reflection"}:
        priority = "urgent"  # a person is waiting for the answer
    elif triggers_now & {"note", "escalation", "alert"}:
        priority = "high"
    else:
        priority = "normal"
    data = TaskCreate(
        title=title,
        description=description,
        kind=TaskKind.PLANNING.value,
        priority=priority,
        assignee_agent_id=agent.id,
        idempotency_key=f"{WAKE_KEY_PREFIX}{request.id}",
    )
    task, _ = tasks.create_system_task(db, request.company_id, data, now=now)
    audit.record(
        db,
        company_id=request.company_id,
        principal=None,
        action="wake.created",
        target_type="task",
        target_id=task.id,
        summary=f"Woke {agent.name}: {title}",
        data={
            "agent_id": agent.id,
            "triggers": list(dict.fromkeys(r.get("trigger") for r in reasons)),
            "reasons": len(reasons),
        },
    )
    if agent.kind == AgentKind.FOUNDER:
        events.emit(
            db,
            request.company_id,
            "cofounder.woke",
            {
                "task_id": task.id,
                "agent_id": agent.id,
                "triggers": list(dict.fromkeys(r.get("trigger") for r in reasons)),
            },
        )
    db.delete(request)
    return task.id


def materialize_due(factory: sessionmaker[Session], now: datetime) -> list[str]:
    """Scheduler step: create the wake tasks that are due. Returns the task ids touched."""
    with factory() as db:
        ids = list(
            db.scalars(
                select(WakeRequest.id)
                .where(WakeRequest.not_before <= now)
                .order_by(WakeRequest.not_before, WakeRequest.id)
            )
        )
    touched: list[str] = []
    for request_id in ids:
        with factory() as db:
            request = db.get(WakeRequest, request_id)
            if request is None:
                continue
            try:
                task_id = _materialize_one(db, request, now)
                db.commit()
            except Exception:  # one bad request must not stall the others
                db.rollback()
                logger.exception("could not materialize wake request %s", request_id)
                continue
            if task_id:
                touched.append(task_id)
    return touched


# ---------------------------------------------------------------- owner wake


def owner_wake(db: Session, ctx: CompanyContext) -> tuple[str, str | None]:
    """``POST /cofounder/wake``: wake the AI Co-founder now. Returns (status, task id):
    ``queued`` when a new planning task was created, ``coalesced`` when one is already open."""
    from app.core.errors import Conflict
    from app.core.principal import require_human_admin

    require_human_admin(ctx, "wake the AI Co-founder")
    founder = active_founder(db, ctx.company_id)
    if founder is None:
        raise Conflict("The company has no active AI Co-founder to wake")
    company = db.get(Company, ctx.company_id)
    if company is None or company.status != CompanyStatus.ACTIVE:
        raise Conflict("The company is paused; resume it before waking the AI Co-founder")
    now = utcnow()
    before = open_wake_task(db, ctx.company_id, founder.id)
    request = request_wake(
        db,
        ctx.company_id,
        founder.id,
        "manual",
        "The Founder asked you to review the company now.",
        delay_seconds=0,
        now=now,
    )
    assert request is not None
    task_id = _materialize_one(db, request, now)
    after = open_wake_task(db, ctx.company_id, founder.id)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="cofounder.wake_requested",
        target_type="agent",
        target_id=founder.id,
        summary="The Founder woke the AI Co-founder",
        data={"task_id": task_id or (after.id if after else None)},
    )
    db.commit()
    if task_id and before is None:
        return "queued", task_id
    return "coalesced", (after.id if after is not None else task_id)


# ---------------------------------------------------------------- stale goals

GOAL_NUDGE_AFTER = timedelta(minutes=3)
GOAL_NUDGE_SPACING = timedelta(minutes=20)
MAX_GOAL_NUDGES = 3


def nudge_goals_without_projects(db: Session, company_id: str, *, now: datetime) -> int:
    """A live goal with no project after a few minutes: wake the AI Co-founder to give it one (three
    nudges per goal at most, 20 minutes apart). Models sometimes record a goal and forget the rest.
    No commit."""
    from app.models.audit import AuditEvent
    from app.models.work import Project

    company = db.get(Company, company_id)
    founder = active_founder(db, company_id)
    if company is None or company.status != CompanyStatus.ACTIVE or founder is None:
        return 0
    goals = list(
        db.scalars(
            select(Goal).where(
                Goal.company_id == company_id,
                Goal.status.in_([GoalStatus.PROPOSED, GoalStatus.ACTIVE]),
                Goal.created_at <= now - GOAL_NUDGE_AFTER,
            )
        )
    )
    nudged = 0
    for goal in goals:
        has_project = db.scalar(
            select(func.count(Project.id)).where(
                Project.company_id == company_id,
                Project.goal_id == goal.id,
                Project.status.notin_(["cancelled"]),
            )
        )
        if has_project:
            continue
        events_ = list(
            db.scalars(
                select(AuditEvent.created_at).where(
                    AuditEvent.company_id == company_id,
                    AuditEvent.action == "goal.nudged",
                    AuditEvent.target_id == goal.id,
                )
            )
        )
        if len(events_) >= MAX_GOAL_NUDGES or (events_ and max(events_) > now - GOAL_NUDGE_SPACING):
            continue
        if open_wake_task(db, company_id, founder.id) is not None or has_pending(
            db, company_id, founder.id
        ):
            continue
        request_wake(
            db,
            company_id,
            founder.id,
            "goal",
            f"Goal '{goal.title}' [id {goal.id}] has no project yet: create one with "
            "company_manage_project (goal_id) and give the work to a lead.",
            ref_type="goal",
            ref_id=goal.id,
            now=now,
        )
        audit.record(
            db,
            company_id=company_id,
            principal=None,
            action="goal.nudged",
            target_type="goal",
            target_id=goal.id,
            summary=f"Reminded the AI Co-founder that goal '{goal.title}' has no project",
            data={},
        )
        nudged += 1
    return nudged
