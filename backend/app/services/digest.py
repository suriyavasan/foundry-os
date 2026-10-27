"""The deterministic standup digest (M12): a short, factual summary of the company that is appended
to the description of routines whose template says ``"digest": true`` (standup, weekly review).

No model is involved: every line is computed from the database, so the standup agent starts from
facts - which projects are how far, what is stuck and for how long, what failed, what was spent and
what the watchdog had to do - and can act on them instead of discovering them. Ids are included so
the agent can act on an item without searching. Money is reported as committed (settled plus held
reservations) and as usage by cost kind; an estimate is always labelled as one. Bounded to 3 KB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.autonomy import Alert, AutonomyIntervention
from app.models.company import Company
from app.models.enums import (
    WAITING_ON_SUBTASKS,
    AlertStatus,
    CostKind,
    ProjectStatus,
    TaskKind,
    TaskStatus,
)
from app.models.ledger import UsageRecord
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.services import ledger, policy

MAX_BYTES = 3 * 1024
PER_SECTION = 5
IN_REVIEW_AFTER = timedelta(hours=1)
WAITING_AFTER = timedelta(hours=2)
NOT_DISPATCHED_AFTER = timedelta(minutes=30)
WINDOW = timedelta(hours=24)
OPEN_PROJECT = [ProjectStatus.PLANNED.value, ProjectStatus.ACTIVE.value]
WORK_KINDS = [TaskKind.WORK.value, TaskKind.HIRING.value, TaskKind.REVIEW.value]


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.2f}"


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h" if minutes >= 120 else f"{minutes} min"


def _clip(text: str, n: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _names(db: Session, ids: set[str | None]) -> dict[str, str]:
    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    return {a.id: a.name for a in db.scalars(select(Agent).where(Agent.id.in_(wanted)))}


def _project_lines(db: Session, company_id: str) -> list[str]:
    projects = list(
        db.scalars(
            select(Project)
            .where(Project.company_id == company_id, Project.status.in_(OPEN_PROJECT))
            .order_by(Project.updated_at.desc())
            .limit(8)
        )
    )
    names = _names(db, {p.lead_agent_id for p in projects})
    lines = []
    for project in projects:
        rows = dict(
            db.execute(
                select(Task.status, func.count(Task.id))
                .where(
                    Task.company_id == company_id,
                    Task.project_id == project.id,
                    Task.kind == TaskKind.WORK,
                    Task.status != TaskStatus.CANCELLED,
                )
                .group_by(Task.status)
            ).all()
        )
        total = sum(rows.values())
        lead = names.get(project.lead_agent_id or "", "no lead")
        lines.append(
            f"- {_clip(project.name)} [{project.id}] ({project.status}, {lead}): "
            f"{rows.get(TaskStatus.DONE.value, 0)}/{total} tasks done"
        )
    return lines or ["- no open projects"]


def _stuck_lines(db: Session, company_id: str, now: datetime) -> list[str]:
    def rows(*conditions):
        return list(
            db.scalars(
                select(Task)
                .where(Task.company_id == company_id, *conditions)
                .order_by(Task.updated_at, Task.id)
                .limit(PER_SECTION)
            )
        )

    groups = [
        (
            "in review over 1 h",
            rows(Task.status == TaskStatus.IN_REVIEW, Task.updated_at <= now - IN_REVIEW_AFTER),
        ),
        ("blocked", rows(Task.status == TaskStatus.BLOCKED, Task.kind.in_(WORK_KINDS))),
        (
            "waiting on subtasks over 2 h",
            rows(
                Task.status.in_([TaskStatus.READY.value, TaskStatus.BACKLOG.value]),
                Task.blocked_reason == WAITING_ON_SUBTASKS,
                Task.updated_at <= now - WAITING_AFTER,
            ),
        ),
        (
            "ready but not dispatched for over 30 min",
            rows(
                Task.status == TaskStatus.READY,
                Task.assignee_agent_id.is_not(None),
                Task.blocked_reason.is_(None),
                Task.updated_at <= now - NOT_DISPATCHED_AFTER,
                (Task.next_attempt_at.is_(None)) | (Task.next_attempt_at <= now),
            ),
        ),
    ]
    lines = []
    for label, tasks in groups:
        for task in tasks:
            lines.append(
                f"- {label}: '{_clip(task.title)}' [task {task.id}] "
                f"({_age(now - task.updated_at)} in this state)"
            )
    return lines or ["- nothing is stuck"]


def _failure_line(db: Session, company_id: str, now: datetime) -> str:
    since = now - WINDOW
    failed_tasks = list(
        db.scalars(
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.status == TaskStatus.FAILED,
                Task.completed_at >= since,
            )
            .order_by(Task.completed_at.desc())
            .limit(PER_SECTION)
        )
    )
    failed_runs = int(
        db.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.company_id == company_id,
                TaskRun.status.in_(["failed", "timed_out"]),
                TaskRun.created_at >= since,
            )
        )
        or 0
    )
    if not failed_tasks and not failed_runs:
        return "- none"
    titles = "; ".join(f"'{_clip(t.title, 40)}' [task {t.id}]" for t in failed_tasks)
    return f"- {failed_runs} failed run(s); tasks failed for good: {titles or 'none'}"


def _spend_line(db: Session, company_id: str, now: datetime) -> str:
    company = db.get(Company, company_id)
    tz = company.timezone if company else "UTC"
    limits = policy.load_policy(db, company_id)
    periods = ledger.periods_for(now, tz)
    committed = ledger.committed_micros(db, company_id, period_month=periods.month)
    year, month = (int(x) for x in periods.month.split("-"))
    start = datetime(year, month, 1, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    by_kind = dict(
        db.execute(
            select(UsageRecord.cost_kind, func.coalesce(func.sum(UsageRecord.cost_micros), 0))
            .where(UsageRecord.company_id == company_id, UsageRecord.occurred_at >= start)
            .group_by(UsageRecord.cost_kind)
        ).all()
    )
    budget = (
        f" of a {_usd(limits.monthly_budget_micros)} monthly budget"
        if limits.monthly_budget_micros is not None
        else " (no monthly budget set)"
    )
    return (
        f"- this month: {_usd(committed)} committed (settled plus held reservations){budget}; "
        f"usage verified {_usd(int(by_kind.get(CostKind.VERIFIED.value, 0)))}, "
        f"estimated {_usd(int(by_kind.get(CostKind.ESTIMATED.value, 0)))} (estimates, not charges)"
    )


def _watchdog_lines(db: Session, company_id: str, now: datetime) -> list[str]:
    rows = list(
        db.execute(
            select(AutonomyIntervention.kind, func.sum(AutonomyIntervention.count))
            .where(
                AutonomyIntervention.company_id == company_id,
                AutonomyIntervention.last_at >= now - WINDOW,
                AutonomyIntervention.kind.notin_(["heartbeat_fingerprint"]),
            )
            .group_by(AutonomyIntervention.kind)
            .order_by(AutonomyIntervention.kind)
        ).all()
    )
    stalled = int(
        db.scalar(
            select(func.count(Alert.id)).where(
                Alert.company_id == company_id,
                Alert.kind == "autonomy_stalled",
                Alert.status == AlertStatus.OPEN,
            )
        )
        or 0
    )
    parts = ", ".join(f"{kind} x{int(n)}" for kind, n in rows) or "none"
    return [
        f"- watchdog steps in the last 24 h: {parts}; open 'autonomy stalled' alerts: {stalled}"
    ]


def _fit(lines: list[str]) -> str:
    """Join the lines, dropping whole lines from the end until the text fits in 3 KB."""
    text = "\n".join(lines)
    if len(text.encode()) <= MAX_BYTES:
        return text
    marker = "- (digest truncated)"
    while lines and len("\n".join([*lines, marker]).encode()) > MAX_BYTES:
        lines = lines[:-1]
    return "\n".join([*lines, marker])


def build(db: Session, company_id: str, now: datetime) -> str:
    """The digest text (markdown, at most 3 KB). Read-only."""
    lines = [
        f"## Platform digest ({now:%Y-%m-%d %H:%M} UTC; computed from the database, no model)",
        "Projects:",
        *_project_lines(db, company_id),
        "Stuck items (act on these first):",
        *_stuck_lines(db, company_id, now),
        "Failures in the last 24 h:",
        _failure_line(db, company_id, now),
        "Spend:",
        _spend_line(db, company_id, now),
        "Autonomy:",
        *_watchdog_lines(db, company_id, now),
    ]
    return _fit(lines)
