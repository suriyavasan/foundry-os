"""Schedules (heartbeats and routines). Firing is idempotent per (schedule, slot):

* a claim ``UPDATE ... WHERE next_run_at = :seen`` makes concurrent schedulers race safely;
* ``schedule_fires`` has a unique (schedule_id, scheduled_for), the durable backstop;
* the created task carries ``schedule:{id}:{slot}`` as its idempotency key.

After downtime only the **latest** missed slot fires (no backfill storm).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.company import Company
from app.models.enums import (
    TERMINAL_TASK_STATUSES,
    CompanyStatus,
    ScheduleKind,
    TaskKind,
)
from app.models.schedule import Schedule, ScheduleFire
from app.models.work import Task
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.schemas.work import TaskCreate
from app.services import audit, events, tasks
from app.services.common import get_agent_or_404
from app.services.pagination import paginate


def compute_next(
    *, interval_seconds: int | None, cron: str | None, timezone: str, after: datetime
) -> datetime:
    """First slot strictly after ``after`` (UTC)."""
    if interval_seconds:
        return after + timedelta(seconds=interval_seconds)
    assert cron is not None
    local = after.astimezone(ZoneInfo(timezone))
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


def latest_slot(schedule: Schedule, now: datetime) -> datetime:
    """The most recent slot <= now, given ``schedule.next_run_at <= now``."""
    assert schedule.next_run_at is not None
    if schedule.interval_seconds:
        steps = int((now - schedule.next_run_at).total_seconds() // schedule.interval_seconds)
        return schedule.next_run_at + timedelta(seconds=steps * schedule.interval_seconds)
    local_now = (now + timedelta(seconds=1)).astimezone(ZoneInfo(schedule.timezone))
    slot = croniter(schedule.cron, local_now).get_prev(datetime).astimezone(UTC)
    return max(slot, schedule.next_run_at)


# ---------------------------------------------------------------- API-facing


def list_schedules(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    enabled: bool | None = None,
    kind: str | None = None,
    agent_id: str | None = None,
):
    stmt = select(Schedule).where(Schedule.company_id == ctx.company_id)
    if enabled is not None:
        stmt = stmt.where(Schedule.enabled == enabled)
    if kind:
        stmt = stmt.where(Schedule.kind == kind)
    if agent_id:
        stmt = stmt.where(Schedule.agent_id == agent_id)
    return paginate(db, stmt, Schedule, limit=limit, cursor=cursor, descending=False)


def get_schedule(db: Session, ctx: CompanyContext, schedule_id: str) -> Schedule:
    from app.core.errors import NotFound

    schedule = db.scalar(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.company_id == ctx.company_id)
    )
    if schedule is None:
        raise NotFound("Schedule not found")
    return schedule


def create_schedule(db: Session, ctx: CompanyContext, data: ScheduleCreate) -> Schedule:
    require_human_admin(ctx, "create schedules")
    get_agent_or_404(db, ctx.company_id, data.agent_id)
    company = db.get(Company, ctx.company_id)
    timezone = data.timezone or (company.timezone if company else "UTC")
    now = utcnow()
    schedule = Schedule(
        company_id=ctx.company_id,
        name=data.name,
        kind=data.kind,
        agent_id=data.agent_id,
        interval_seconds=data.interval_seconds,
        cron=data.cron,
        timezone=timezone,
        task_template=data.task_template.model_dump(exclude_none=True),
        enabled=data.enabled,
        next_run_at=compute_next(
            interval_seconds=data.interval_seconds, cron=data.cron, timezone=timezone, after=now
        )
        if data.enabled
        else None,
    )
    db.add(schedule)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="schedule.created",
        target_type="schedule",
        target_id=schedule.id,
        summary=f"Created {schedule.kind} schedule '{schedule.name}'",
        data={"agent_id": schedule.agent_id, "enabled": schedule.enabled},
    )
    events.emit(db, ctx.company_id, "schedule.created", {"schedule_id": schedule.id})
    db.commit()
    return schedule


def update_schedule(
    db: Session, ctx: CompanyContext, schedule_id: str, data: ScheduleUpdate
) -> Schedule:
    require_human_admin(ctx, "update schedules")
    schedule = get_schedule(db, ctx, schedule_id)
    fields = data.model_fields_set
    if "agent_id" in fields:
        get_agent_or_404(db, ctx.company_id, data.agent_id)  # type: ignore[arg-type]
    changed: list[str] = []
    timing_changed = False
    simple = {
        "name": data.name,
        "agent_id": data.agent_id,
        "timezone": data.timezone,
        "enabled": data.enabled,
    }
    for field, value in simple.items():
        if field in fields and getattr(schedule, field) != value:
            setattr(schedule, field, value)
            changed.append(field)
            timing_changed |= field in ("timezone", "enabled")
    if "task_template" in fields:
        template = data.task_template.model_dump(exclude_none=True)  # type: ignore[union-attr]
        if template != schedule.task_template:
            schedule.task_template = template
            changed.append("task_template")
    if "interval_seconds" in fields or "cron" in fields:
        interval = data.interval_seconds if "interval_seconds" in fields else None
        cron = data.cron if "cron" in fields else None
        if (interval is None) == (cron is None):
            raise ValidationFailed(
                "provide exactly one of interval_seconds or cron when changing the timing",
                details=[
                    {
                        "loc": ["body", "interval_seconds"],
                        "msg": "exactly one of interval_seconds or cron",
                    }
                ],
            )
        schedule.interval_seconds, schedule.cron = interval, cron
        changed.append("interval_seconds" if interval else "cron")
        timing_changed = True
    if changed:
        now = utcnow()
        schedule.updated_at = now
        if timing_changed:
            schedule.next_run_at = (
                compute_next(
                    interval_seconds=schedule.interval_seconds,
                    cron=schedule.cron,
                    timezone=schedule.timezone,
                    after=now,
                )
                if schedule.enabled
                else None
            )
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="schedule.updated",
            target_type="schedule",
            target_id=schedule.id,
            summary=f"Updated schedule '{schedule.name}': {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(db, ctx.company_id, "schedule.updated", {"schedule_id": schedule.id})
        db.commit()
    return schedule


# ---------------------------------------------------------------- firing (scheduler)


def _has_open_task(db: Session, schedule: Schedule) -> bool:
    return bool(
        db.scalar(
            select(Task.id)
            .join(ScheduleFire, ScheduleFire.task_id == Task.id)
            .where(
                ScheduleFire.schedule_id == schedule.id,
                Task.status.notin_([s.value for s in TERMINAL_TASK_STATUSES]),
            )
            .limit(1)
        )
    )


def fire_due(factory: sessionmaker[Session], now: datetime) -> list[str]:
    """Fire every enabled schedule whose ``next_run_at`` has passed. Returns the ids of the
    tasks created. Safe to run from several scheduler instances at once."""
    with factory() as db:
        due = list(
            db.execute(
                select(Schedule.id, Schedule.company_id)
                .join(Company, Company.id == Schedule.company_id)
                .where(
                    Schedule.enabled.is_(True),
                    Schedule.next_run_at.is_not(None),
                    Schedule.next_run_at <= now,
                    Company.status == CompanyStatus.ACTIVE,
                )
                .order_by(Schedule.next_run_at)
            ).all()
        )
    created: list[str] = []
    for schedule_id, _company_id in due:
        task_id = _fire_one(factory, schedule_id, now)
        if task_id:
            created.append(task_id)
    return created


def _fire_one(factory: sessionmaker[Session], schedule_id: str, now: datetime) -> str | None:
    with factory() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or not schedule.enabled or schedule.next_run_at is None:
            return None
        seen_next = schedule.next_run_at
        if seen_next > now:
            return None
        slot = latest_slot(schedule, now)
        new_next = compute_next(
            interval_seconds=schedule.interval_seconds,
            cron=schedule.cron,
            timezone=schedule.timezone,
            after=now,
        )
        claim = db.execute(
            update(Schedule)
            .where(
                Schedule.id == schedule.id,
                Schedule.enabled.is_(True),
                Schedule.next_run_at == seen_next,
            )
            .values(next_run_at=new_next, last_fired_at=now, updated_at=now)
        )
        if claim.rowcount != 1:  # another scheduler instance got this slot
            db.rollback()
            return None
        db.add(
            ScheduleFire(
                company_id=schedule.company_id, schedule_id=schedule.id, scheduled_for=slot
            )
        )
        try:
            db.flush()
        except IntegrityError:  # slot already fired (e.g. next_run_at was reset)
            db.rollback()
            return None
        fire = db.scalar(
            select(ScheduleFire).where(
                ScheduleFire.schedule_id == schedule.id, ScheduleFire.scheduled_for == slot
            )
        )
        db.refresh(schedule)
        task_id: str | None = None
        if not (schedule.kind == ScheduleKind.HEARTBEAT and _has_open_task(db, schedule)):
            template = schedule.task_template or {}
            default_kind = (
                TaskKind.PLANNING if schedule.kind == ScheduleKind.HEARTBEAT else TaskKind.ROUTINE
            )
            data = TaskCreate(
                title=template.get("title") or schedule.name,
                description=template.get("description"),
                acceptance_criteria=template.get("acceptance_criteria"),
                kind=template.get("kind") or default_kind.value,
                priority=template.get("priority", "normal"),
                assignee_agent_id=schedule.agent_id,
                idempotency_key=f"schedule:{schedule.id}:{slot.strftime('%Y%m%dT%H%M%SZ')}",
            )
            task, _ = tasks.create_system_task(db, schedule.company_id, data, now=now)
            task_id = task.id
            if fire is not None:
                fire.task_id = task.id
        audit.record(
            db,
            company_id=schedule.company_id,
            principal=None,
            action="schedule.fired",
            target_type="schedule",
            target_id=schedule.id,
            summary=f"Fired schedule '{schedule.name}'"
            + ("" if task_id else " (skipped: previous heartbeat still open)"),
            data={"slot": slot.isoformat().replace("+00:00", "Z"), "task_id": task_id},
        )
        events.emit(
            db,
            schedule.company_id,
            "schedule.fired",
            {"schedule_id": schedule.id, "task_id": task_id},
        )
        db.commit()
        return task_id
