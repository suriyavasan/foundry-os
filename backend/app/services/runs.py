"""Task runs: creation at dispatch, cancellation, events, and finalization.

``finalize_run`` is the single place a run reaches a terminal state. It is called by the
worker (normal completion), by recovery (lost worker) and by cancellation of queued runs, and
it settles the budget reservation, records usage, moves the task along its state machine,
audits and emits events -- all in one transaction that starts with the budget lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import InvalidTransition, NotFound
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import (
    ACTIVE_RUN_STATUSES,
    CostKind,
    RunStatus,
    TaskStatus,
)
from app.models.ledger import BudgetReservation
from app.models.run import RunEvent, TaskRun
from app.models.work import Task
from app.runtimes.base import ArtifactRef, UsageReport
from app.services import audit, events, ledger, tasks
from app.services.pagination import paginate

CANCEL_BY_USER = "cancelled_by_user"
CANCEL_COMPANY_PAUSED = "company_paused"
CANCEL_TASK_CANCELLED = "task_cancelled"
CANCEL_TASK_BLOCKED = "task_blocked"
CANCEL_SHUTDOWN = "worker_shutdown"


@dataclass
class RunOutcome:
    status: RunStatus  # succeeded | failed | cancelled | timed_out
    exit_reason: str
    summary: str | None = None
    error: dict[str, Any] | None = None
    usage: UsageReport | None = None
    executed: bool = True  # False -> the runtime never ran: release the reservation
    artifacts: list[ArtifactRef] = field(default_factory=list)


# ---------------------------------------------------------------- queries


def get_run(db: Session, ctx: CompanyContext, run_id: str) -> TaskRun:
    run = db.scalar(
        select(TaskRun).where(TaskRun.id == run_id, TaskRun.company_id == ctx.company_id)
    )
    if run is None:
        raise NotFound("Run not found")
    return run


def list_runs_for_task(
    db: Session, ctx: CompanyContext, task_id: str, *, limit: int, cursor: str | None
):
    tasks.get_task(db, ctx, task_id)
    stmt = select(TaskRun).where(TaskRun.company_id == ctx.company_id, TaskRun.task_id == task_id)
    return paginate(db, stmt, TaskRun, limit=limit, cursor=cursor, descending=True)


def list_run_events(
    db: Session, ctx: CompanyContext, run_id: str, *, after_seq: int, limit: int
) -> tuple[list[RunEvent], str | None]:
    run = get_run(db, ctx, run_id)
    rows = list(
        db.scalars(
            select(RunEvent)
            .where(
                RunEvent.company_id == ctx.company_id,
                RunEvent.run_id == run.id,
                RunEvent.seq > after_seq,
            )
            .order_by(RunEvent.seq)
            .limit(limit + 1)
        )
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].seq)
    return rows, next_cursor


# ---------------------------------------------------------------- creation (dispatcher)


def create_queued_run(
    db: Session,
    task: Task,
    agent: Agent,
    reservation: BudgetReservation,
    *,
    now: datetime,
) -> TaskRun:
    """Queue a run for ``task`` (no commit). Bumps ``attempt_count`` and starts the task."""
    prior = db.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_id == task.id))
    attempt_number = int(prior or 0) + 1
    task.attempt_count += 1
    run = TaskRun(
        company_id=task.company_id,
        task_id=task.id,
        agent_id=agent.id,
        attempt_number=attempt_number,
        status=RunStatus.QUEUED,
        runtime=agent.runtime,
        idempotency_key=f"{task.id}:{attempt_number}:{task.revision_count}",
        queued_at=now,
        reservation_id=reservation.id,
        cost_kind=CostKind.UNKNOWN,
    )
    db.add(run)
    db.flush()
    reservation.run_id = run.id
    tasks.transition(
        db,
        task,
        TaskStatus.IN_PROGRESS,
        principal=None,
        now=now,
        extra={"run_id": run.id, "attempt": attempt_number},
    )
    audit.record(
        db,
        company_id=task.company_id,
        principal=None,
        action="run.queued",
        target_type="run",
        target_id=run.id,
        summary=f"Queued run #{attempt_number} of '{task.title}' for {agent.name}",
        data={"task_id": task.id, "agent_id": agent.id, "runtime": agent.runtime},
    )
    events.emit(
        db,
        task.company_id,
        "run.queued",
        {"run_id": run.id, "task_id": task.id, "agent_id": agent.id, "attempt": attempt_number},
    )
    events.emit(
        db,
        task.company_id,
        "agent.activity",
        {
            "agent_id": agent.id,
            "state": "working",
            "task_id": task.id,
            "run_id": run.id,
            "summary": f"Queued: {task.title}",
        },
    )
    return run


# ---------------------------------------------------------------- cancellation


def _active_runs(db: Session, company_id: str, *, task_id: str | None = None) -> list[TaskRun]:
    stmt = select(TaskRun).where(
        TaskRun.company_id == company_id, TaskRun.status.in_(ACTIVE_RUN_STATUSES)
    )
    if task_id:
        stmt = stmt.where(TaskRun.task_id == task_id)
    return list(db.scalars(stmt))


def _cancel_one(
    db: Session,
    run: TaskRun,
    reason: str,
    principal: Principal | None,
    *,
    now: datetime | None = None,
) -> None:
    """Queued runs are cancelled on the spot; started ones get a cooperative cancel request
    that the worker's monitor picks up."""
    now = now or utcnow()
    if run.status == RunStatus.QUEUED:
        ledger.acquire_budget_lock(db, run.company_id)
        _finalize_locked(
            db,
            run,
            RunOutcome(status=RunStatus.CANCELLED, exit_reason=reason, executed=False),
            now=now,
            principal=principal,
            cancel_reason=reason,
        )
        return
    if not run.cancel_requested:
        run.cancel_requested = True
        run.cancel_reason = reason
        run.updated_at = now
        audit.record(
            db,
            company_id=run.company_id,
            principal=principal,
            action="run.cancel_requested",
            target_type="run",
            target_id=run.id,
            summary=f"Cancellation requested for run ({reason})",
            data={"task_id": run.task_id, "reason": reason},
        )
        events.emit(
            db,
            run.company_id,
            "run.cancel_requested",
            {"run_id": run.id, "task_id": run.task_id, "reason": reason},
        )


def request_cancel_for_task(
    db: Session, task: Task, reason: str, principal: Principal | None
) -> None:
    for run in _active_runs(db, task.company_id, task_id=task.id):
        _cancel_one(db, run, reason, principal)


def request_cancel_all(
    db: Session, company_id: str, reason: str, principal: Principal | None
) -> int:
    runs = _active_runs(db, company_id)
    for run in runs:
        _cancel_one(db, run, reason, principal)
    return len(runs)


def cancel_run(db: Session, ctx: CompanyContext, run_id: str, note: str | None = None) -> TaskRun:
    require_human_admin(ctx, "cancel runs")
    run = get_run(db, ctx, run_id)
    if RunStatus(run.status) not in ACTIVE_RUN_STATUSES:
        raise InvalidTransition(
            f"Cannot cancel a run that is {run.status}", details={"from": run.status}
        )
    _cancel_one(db, run, CANCEL_BY_USER, ctx.principal)
    db.commit()
    return run


# ---------------------------------------------------------------- events


def append_event(
    db: Session,
    run: TaskRun,
    *,
    seq: int,
    type_: str,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> RunEvent | None:
    """Idempotent on (run_id, seq): a replay returns None and writes nothing."""
    exists = db.scalar(select(RunEvent.id).where(RunEvent.run_id == run.id, RunEvent.seq == seq))
    if exists:
        return None
    event = RunEvent(
        company_id=run.company_id,
        run_id=run.id,
        seq=seq,
        type=type_[:80],
        payload=payload,
        occurred_at=occurred_at,
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    return event


# ---------------------------------------------------------------- finalization


def finalize_run(
    db: Session,
    run_id: str,
    outcome: RunOutcome,
    *,
    now: datetime,
    principal: Principal | None = None,
    storage: Any | None = None,
) -> TaskRun | None:
    """Move a run to its terminal state and settle everything it touched, atomically.
    Returns None if another actor already finalized it (e.g. recovery beat the worker)."""
    run = db.get(TaskRun, run_id)
    if run is None:
        return None
    ledger.acquire_budget_lock(db, run.company_id)  # first statement of the transaction
    done = _finalize_locked(db, run, outcome, now=now, principal=principal, storage=storage)
    if not done:
        db.rollback()
        return None
    db.commit()
    return run


def _finalize_locked(
    db: Session,
    run: TaskRun,
    outcome: RunOutcome,
    *,
    now: datetime,
    principal: Principal | None,
    storage: Any | None = None,
    cancel_reason: str | None = None,
) -> bool:
    """Body of finalize_run; the caller already holds the budget lock. No commit."""
    claim = db.execute(
        update(TaskRun)
        .where(
            TaskRun.id == run.id,
            TaskRun.company_id == run.company_id,
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .values(status=outcome.status.value, finished_at=now, updated_at=now)
    )
    if claim.rowcount != 1:
        return False
    db.refresh(run)
    company_id = run.company_id
    task = db.get(Task, run.task_id)
    agent = db.get(Agent, run.agent_id)
    reservation = db.get(BudgetReservation, run.reservation_id) if run.reservation_id else None
    if cancel_reason:
        run.cancel_reason = cancel_reason
        run.cancel_requested = True

    run.exit_reason = outcome.exit_reason
    run.error = outcome.error
    usage = outcome.usage
    if usage is not None:
        run.input_tokens = usage.input_tokens
        run.output_tokens = usage.output_tokens

    # ---- money
    if outcome.executed and reservation is not None:
        if (
            usage is not None
            and usage.cost_micros is not None
            and usage.cost_kind != CostKind.UNKNOWN
        ):
            cost, kind = max(0, int(usage.cost_micros)), CostKind(usage.cost_kind)
        else:
            # Unknown cost: charge the reservation as an *estimate*, never as a verified charge.
            cost, kind = reservation.amount_micros, CostKind.ESTIMATED
        ledger.settle(db, reservation, cost)
        run.cost_micros, run.cost_kind = cost, kind.value
        ledger.record_usage(
            db,
            company_id=company_id,
            agent_id=run.agent_id,
            dedupe_key=f"run:{run.id}",
            project_id=task.project_id if task else None,
            task_id=run.task_id,
            run_id=run.id,
            runtime=run.runtime,
            provider=agent.model_provider if agent else None,
            model=agent.model_name if agent else None,
            input_tokens=(usage.input_tokens if usage else 0) or 0,
            output_tokens=(usage.output_tokens if usage else 0) or 0,
            cost_micros=cost,
            cost_kind=kind,
            occurred_at=now,
        )
    else:
        if reservation is not None:
            ledger.release(db, reservation)
        run.cost_micros, run.cost_kind = 0, CostKind.UNKNOWN.value

    # ---- artifacts reported by the runtime
    artifact_ids: list[str] = []
    if outcome.artifacts and storage is not None and task is not None and agent is not None:
        from app.services import artifacts as artifact_service

        for ref in outcome.artifacts:
            artifact = artifact_service.create_from_ref(
                db, storage, run=run, task=task, agent=agent, ref=ref
            )
            artifact_ids.append(artifact.id)

    # ---- task state machine
    if task is not None and task.status == TaskStatus.IN_PROGRESS:
        _apply_to_task(db, task, run, outcome, artifact_ids, principal, now)

    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="run.finished",
        target_type="run",
        target_id=run.id,
        summary=f"Run finished: {outcome.status.value} ({outcome.exit_reason})",
        data={
            "task_id": run.task_id,
            "status": outcome.status.value,
            "exit_reason": outcome.exit_reason,
            "cost_micros": run.cost_micros,
            "cost_kind": run.cost_kind,
        },
    )
    events.emit(
        db,
        company_id,
        "run.finished",
        {
            "run_id": run.id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "status": outcome.status.value,
            "exit_reason": outcome.exit_reason,
        },
    )
    events.emit(
        db,
        company_id,
        "agent.activity",
        {
            "agent_id": run.agent_id,
            "state": "idle",
            "task_id": None,
            "run_id": None,
            "summary": None,
        },
    )
    return True


def _apply_to_task(
    db: Session,
    task: Task,
    run: TaskRun,
    outcome: RunOutcome,
    artifact_ids: list[str],
    principal: Principal | None,
    now: datetime,
) -> None:
    if outcome.status == RunStatus.SUCCEEDED:
        tasks.finish_success(
            db,
            task,
            summary=outcome.summary,
            output_refs=artifact_ids,
            principal=principal,
            run_id=run.id,
            now=now,
        )
        return
    if outcome.status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
        err = outcome.error or {}
        tasks.finish_failure(
            db,
            task,
            code=str(err.get("code") or outcome.exit_reason),
            message=str(err.get("message") or outcome.exit_reason),
            run_id=run.id,
            principal=principal,
            now=now,
        )
        return
    # cancelled
    reason = run.cancel_reason or outcome.exit_reason
    if reason == CANCEL_BY_USER:
        tasks.transition(
            db,
            task,
            TaskStatus.BLOCKED,
            principal=principal,
            now=now,
            error={"code": "run_cancelled", "message": "Run cancelled by a user", "run_id": run.id},
            reason="run cancelled",
        )
    else:  # company paused, worker shutdown, ...: not the task's fault -> refund the attempt
        task.attempt_count = max(0, task.attempt_count - 1)
        tasks.transition(
            db,
            task,
            TaskStatus.READY,
            principal=principal,
            now=now,
            reason=f"run cancelled: {reason}",
        )
        tasks.recompute_readiness(
            db, task.company_id, task_ids=[task.id], principal=principal, now=now
        )
