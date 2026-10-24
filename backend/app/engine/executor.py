"""Executes one queued run through its RuntimeAdapter.

Lifecycle: claim (queued -> starting) -> running -> adapter.execute -> finalize. A monitor
thread heartbeats, extends the job lease, enforces ``max_run_seconds`` and turns
``cancel_requested`` (set by the API / pause) into the adapter's cancel token.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from app.engine.context import EngineContext
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import CompanyStatus, CostKind, RunStatus
from app.models.ledger import BudgetReservation
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.base import (
    AgentSpec,
    CancelToken,
    CompanySpec,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionResult,
    Limits,
    RuntimeAdapterError,
    RuntimeErrorKind,
    TaskSpec,
    UsageReport,
)
from app.services import (
    agent_auth,
    audit,
    containment,
    emergency,
    events,
    jobs,
    ledger,
    policy,
    runs,
    safety,
)
from app.services.redaction import redact_payload

logger = logging.getLogger("company_os.executor")

HEARTBEAT_SECONDS = 5
ACTIVITY_THROTTLE_SECONDS = 1.0


@dataclass
class _MonitorState:
    timed_out: bool = False
    cancel_reason: str | None = None
    trip: containment.Trip | None = None  # stalled / runaway / budget exhausted


def _summarize(event: ExecutionEvent) -> str:
    for key in ("summary", "message", "text", "title", "tool"):
        value = event.payload.get(key) if isinstance(event.payload, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return event.type


class DbEventSink:
    """Persists RunEvents (idempotent on seq) and mirrors them to the company outbox."""

    def __init__(
        self,
        ctx: EngineContext,
        run: TaskRun,
        secrets: list[str],
        guard: containment.RunGuard | None = None,
    ) -> None:
        self._ctx = ctx
        self._guard = guard
        self._run_id = run.id
        self._company_id = run.company_id
        self._agent_id = run.agent_id
        self._task_id = run.task_id
        self._secrets = secrets
        self._last_activity: datetime | None = None

    def emit(self, event: ExecutionEvent) -> None:
        payload = redact_payload(event.payload, self._secrets)
        summary = _summarize(ExecutionEvent(event.seq, event.type, payload, event.ts))
        now = self._ctx.clock()
        with self._ctx.session_factory() as db:
            run = db.get(TaskRun, self._run_id)
            if run is None:
                return
            created = runs.append_event(
                db, run, seq=event.seq, type_=event.type, payload=payload, occurred_at=event.ts
            )
            if created is None:
                db.rollback()
                return
            events.emit(
                db,
                self._company_id,
                "run.event",
                {
                    "run_id": self._run_id,
                    "task_id": self._task_id,
                    "agent_id": self._agent_id,
                    "seq": event.seq,
                    "type": event.type[:80],
                    "summary": summary,
                },
            )
            run.activity_summary = summary[
                :300
            ]  # always current; only the outbox event is throttled
            run.activity_at = now
            if (
                self._last_activity is None
                or (now - self._last_activity).total_seconds() >= ACTIVITY_THROTTLE_SECONDS
            ):
                self._last_activity = now
                events.emit(
                    db,
                    self._company_id,
                    "agent.activity",
                    {
                        "agent_id": self._agent_id,
                        "state": "working",
                        "task_id": self._task_id,
                        "run_id": self._run_id,
                        "summary": summary,
                    },
                )
            db.commit()
        if self._guard is not None:
            self._guard.observe(event.type, payload)
            if event.type == "usage":
                self._budget_checkpoint(payload, now)

    def _budget_checkpoint(self, payload: dict, now: datetime) -> None:
        """Mid-run budget checkpoint on a usage event: extend the reservation while the budgets
        allow, cancel the run (``budget_exhausted``) when they do not."""
        assert self._guard is not None
        cost = containment.measured_cost(payload)
        if cost is None:
            return
        if self._guard.measured_micros is None or cost >= self._guard.measured_micros:
            self._guard.measured_micros = cost
            self._guard.measured_kind = containment.measured_kind(payload)
        with self._ctx.session_factory() as db:
            ledger.acquire_budget_lock(db, self._company_id)  # first statement of the transaction
            run = db.get(TaskRun, self._run_id)
            if run is None:
                db.rollback()
                return
            limits = policy.load_policy(db, self._company_id)
            outcome = containment.checkpoint(db, run, cost, limits, now)
            if outcome == "extended":
                safety.check_budget_thresholds(db, self._company_id, now)
            db.commit()
        if outcome == "exhausted":
            self._guard.trip(
                runs.EXIT_BUDGET_EXHAUSTED,
                "The run spent its budget and the budgets do not allow more",
                status="cancelled",
            )


class RunExecutor:
    def __init__(
        self,
        ctx: EngineContext,
        worker_id: str,
        *,
        monitor_interval: float = 0.5,
        shutdown: threading.Event | None = None,
    ) -> None:
        self.ctx = ctx
        self.worker_id = worker_id
        self.monitor_interval = monitor_interval
        self.shutdown = shutdown or threading.Event()

    # ------------------------------------------------------------ public

    def execute(self, run_id: str, job_id: str | None = None) -> str:
        """Run to completion. Returns the final run status, or ``"skipped"`` if the run was
        not in ``queued`` (already handled, e.g. a re-delivered job)."""
        ctx = self.ctx
        now = ctx.clock()
        with ctx.session_factory() as db:
            claim = db.execute(
                update(TaskRun)
                .where(TaskRun.id == run_id, TaskRun.status == RunStatus.QUEUED)
                .values(
                    status=RunStatus.STARTING,
                    worker_id=self.worker_id,
                    started_at=now,
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            if claim.rowcount != 1:
                return "skipped"
            run = db.get(TaskRun, run_id)
            assert run is not None
            task = db.get(Task, run.task_id)
            agent = db.get(Agent, run.agent_id)
            company = db.get(Company, run.company_id)
            reservation = (
                db.get(BudgetReservation, run.reservation_id) if run.reservation_id else None
            )
            limits = policy.load_policy(db, run.company_id)
            db.commit()
            assert task and agent and company

            stopped = emergency.is_active(db)
            if run.cancel_requested or company.status != CompanyStatus.ACTIVE or stopped:
                reason = run.cancel_reason or (
                    emergency.CANCEL_REASON if stopped else runs.CANCEL_COMPANY_PAUSED
                )
                return self._finalize(
                    run_id,
                    runs.RunOutcome(RunStatus.CANCELLED, reason, executed=False),
                    cancel_reason=reason,
                )
            adapter = ctx.registry.get(run.runtime)
            if adapter is None or not adapter.supports_execution:
                return self._finalize(
                    run_id,
                    runs.RunOutcome(
                        RunStatus.FAILED,
                        "runtime_unavailable",
                        error={
                            "code": "runtime_unavailable",
                            "message": f"Runtime '{run.runtime}' cannot execute tasks",
                        },
                        executed=False,
                    ),
                )

            token = agent_auth.issue_token(
                ctx.keys, run, max_run_seconds=limits.max_run_seconds, now=now
            )
            workspace = Path(ctx.settings.workspaces_dir) / run.company_id / run.id
            workspace.mkdir(parents=True, exist_ok=True)
            request = ExecutionRequest(
                run_id=run.id,
                attempt_number=run.attempt_number,
                task=TaskSpec(
                    id=task.id,
                    title=task.title,
                    description=task.description,
                    acceptance_criteria=task.acceptance_criteria,
                    kind=task.kind,
                    priority=task.priority,
                    input_refs=list(task.input_refs),
                    review_note=task.review_note,
                ),
                agent=AgentSpec(
                    id=agent.id,
                    name=agent.name,
                    role_title=agent.role_title,
                    instructions=agent.instructions,
                    responsibilities=agent.responsibilities,
                    model_provider=agent.model_provider,
                    model_name=agent.model_name,
                    runtime_config=dict(agent.runtime_config),
                    tool_permissions=list(agent.tool_permissions),
                ),
                company=CompanySpec(
                    id=company.id,
                    name=company.name,
                    strategic_goal=company.strategic_goal,
                    constraints=list(company.constraints),
                ),
                limits=Limits(
                    max_run_seconds=limits.max_run_seconds,
                    budget_micros=reservation.amount_micros if reservation else None,
                ),
                run_token=token,
                agent_api_url=ctx.settings.agent_api_url,
                workspace_dir=str(workspace),
            )
            max_run_seconds = limits.max_run_seconds
            guard = containment.RunGuard.for_policy(limits)
            stall_seconds = limits.stall_seconds
            sink = DbEventSink(ctx, run, [token], guard)

            started = db.execute(
                update(TaskRun)
                .where(TaskRun.id == run_id, TaskRun.status == RunStatus.STARTING)
                .values(status=RunStatus.RUNNING, updated_at=now)
            )
            if started.rowcount == 1:
                events.emit(
                    db,
                    run.company_id,
                    "run.started",
                    {"run_id": run.id, "task_id": run.task_id, "agent_id": run.agent_id},
                )
                audit.record(
                    db,
                    company_id=run.company_id,
                    principal=None,
                    action="run.started",
                    target_type="run",
                    target_id=run.id,
                    summary=f"Run #{run.attempt_number} started on {run.runtime}",
                    data={"task_id": run.task_id, "worker_id": self.worker_id},
                )
            db.commit()
            if started.rowcount != 1:  # finalized concurrently (e.g. recovery)
                return "skipped"

        return self._run_adapter(
            run_id, job_id, adapter, request, sink, now, max_run_seconds, guard, stall_seconds
        )

    # ------------------------------------------------------------ internals

    def _run_adapter(
        self,
        run_id,
        job_id,
        adapter,
        request,
        sink,
        started_at,
        max_run_seconds,
        guard,
        stall_seconds,
    ):
        cancel = CancelToken()
        state = _MonitorState()
        stop = threading.Event()
        deadline = started_at.timestamp() + max_run_seconds
        monitor = threading.Thread(
            target=self._monitor,
            args=(run_id, job_id, cancel, state, stop, deadline, started_at, guard, stall_seconds),
            name=f"run-monitor-{run_id[:8]}",
            daemon=True,
        )
        monitor.start()
        result: ExecutionResult | None = None
        exception: BaseException | None = None
        try:
            result = adapter.execute(request, sink, cancel)
        except RuntimeAdapterError as exc:
            exception = exc
        except Exception as exc:  # the runtime is untrusted code: never let it kill the worker
            exception = exc
            logger.error("run %s: runtime raised %s", run_id, type(exc).__name__)
        finally:
            stop.set()
            monitor.join(timeout=5)

        outcome = self._outcome(result, exception, state, max_run_seconds, guard)
        return self._finalize(run_id, outcome, cancel_reason=state.cancel_reason)

    @staticmethod
    def _outcome(
        result: ExecutionResult | None,
        exception: BaseException | None,
        state: _MonitorState,
        max_run_seconds: int,
        guard: containment.RunGuard | None = None,
    ) -> runs.RunOutcome:
        usage = result.usage if result else None
        if (
            guard is not None
            and guard.measured_micros is not None
            and (usage is None or usage.cost_micros is None)
        ):
            # The runtime ended without a final cost (cancelled, killed): bill what it measured.
            usage = UsageReport(
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cost_micros=guard.measured_micros,
                cost_kind=CostKind(guard.measured_kind),
            )
        if state.trip is not None and not (result is not None and result.status == "succeeded"):
            trip = state.trip
            if trip.status == "cancelled":
                return runs.RunOutcome(RunStatus.CANCELLED, trip.code, usage=usage)
            return runs.RunOutcome(
                RunStatus.FAILED,
                trip.code,
                error={"code": trip.code, "message": trip.message},
                usage=usage,
            )
        if exception is not None:
            if isinstance(exception, RuntimeAdapterError):
                code, message = exception.kind.value, exception.message
            else:
                code, message = "runtime_exception", f"Runtime raised {type(exception).__name__}"
            # A configuration error (no credential, bad runtime_config) is raised before the runtime
            # does anything: nothing was spent, so the reservation is released, not charged.
            never_ran = (
                isinstance(exception, RuntimeAdapterError)
                and exception.kind == RuntimeErrorKind.CONFIG
                and usage is None
            )
            return runs.RunOutcome(
                RunStatus.FAILED,
                code,
                error={"code": code, "message": message},
                usage=usage,
                executed=not never_ran,
            )
        assert result is not None
        if result.status == "succeeded":
            return runs.RunOutcome(
                RunStatus.SUCCEEDED,
                "completed",
                summary=result.summary,
                usage=usage,
                artifacts=list(result.artifacts),
            )
        if state.timed_out:
            return runs.RunOutcome(
                RunStatus.TIMED_OUT,
                "timeout",
                error={"code": "timeout", "message": f"Run exceeded {max_run_seconds}s"},
                usage=usage,
            )
        if result.status == "cancelled" or state.cancel_reason:
            reason = state.cancel_reason or runs.CANCEL_BY_USER
            return runs.RunOutcome(RunStatus.CANCELLED, reason, usage=usage)
        err = result.error
        code = err.kind.value if err else "failed"
        message = err.message if err else "Runtime reported failure"
        if result.status != "failed":
            code, message = "invalid_result", f"Runtime returned unknown status {result.status!r}"
        return runs.RunOutcome(
            RunStatus.FAILED, code, error={"code": code, "message": message}, usage=usage
        )

    def _finalize(
        self, run_id: str, outcome: runs.RunOutcome, *, cancel_reason: str | None = None
    ) -> str:
        with self.ctx.session_factory() as db:
            run = runs.finalize_run(
                db, run_id, outcome, now=self.ctx.clock(), storage=self.ctx.storage
            )
            if run is None:
                return "skipped"  # someone else (recovery) finalized it first
            if cancel_reason and not run.cancel_reason:
                run.cancel_reason = cancel_reason
                db.commit()
            return outcome.status.value

    def _monitor(
        self, run_id, job_id, cancel, state, stop, deadline_epoch, started_at, guard, stall_seconds
    ) -> None:
        ctx = self.ctx
        last_heartbeat = started_at
        while not stop.wait(self.monitor_interval):
            now = ctx.clock()
            if not state.timed_out and now.timestamp() >= deadline_epoch:
                state.timed_out = True
                cancel.set()
            trip = guard.tripped
            if trip is not None and state.trip is None and not cancel.is_set():
                state.trip = trip
                if trip.status == "cancelled":
                    state.cancel_reason = trip.code
                cancel.set()
            if self.shutdown.is_set() and not cancel.is_set():
                state.cancel_reason = runs.CANCEL_SHUTDOWN
                cancel.set()
            try:
                with ctx.session_factory() as db:
                    requested, reason, activity_at = db.execute(
                        select(
                            TaskRun.cancel_requested, TaskRun.cancel_reason, TaskRun.activity_at
                        ).where(TaskRun.id == run_id)
                    ).one()
                    if requested and not cancel.is_set():
                        state.cancel_reason = reason or runs.CANCEL_BY_USER
                        cancel.set()
                    last_activity = max(activity_at or started_at, started_at)
                    if (
                        not cancel.is_set()
                        and (now - last_activity).total_seconds() > stall_seconds
                    ):
                        guard.trip(
                            "stalled",
                            f"No event or progress for {stall_seconds}s; the run was cancelled",
                        )
                    if (now - last_heartbeat).total_seconds() >= HEARTBEAT_SECONDS:
                        db.execute(
                            update(TaskRun)
                            .where(
                                TaskRun.id == run_id,
                                TaskRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
                            )
                            .values(heartbeat_at=now)
                        )
                        db.commit()
                        if job_id:
                            jobs.extend_lease(db, job_id, self.worker_id, now)
                        last_heartbeat = now
            except Exception:  # a DB hiccup must not stop cancellation/timeouts
                logger.warning("run monitor for %s: transient error", run_id, exc_info=False)
