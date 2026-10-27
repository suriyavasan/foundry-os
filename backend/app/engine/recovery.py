"""Crash recovery, run on scheduler/worker start and then every 30 s."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from app.engine.context import EngineContext
from app.models.company import Company
from app.models.enums import CompanyStatus, JobStatus, RunStatus
from app.models.run import Job, TaskRun
from app.services import jobs, runs, safety, tasks

STALE_HEARTBEAT_SECONDS = 30
RECOVERY_INTERVAL_SECONDS = 30


@dataclass
class RecoveryReport:
    stale_run_ids: list[str] = field(default_factory=list)
    dead_job_run_ids: list[str] = field(default_factory=list)
    requeued_jobs: int = 0
    budget_released_tasks: int = 0
    auto_resumed_company_ids: list[str] = field(default_factory=list)


def recover(ctx: EngineContext, now: datetime | None = None) -> RecoveryReport:
    now = now or ctx.clock()
    report = RecoveryReport()

    with ctx.session_factory() as db:
        report.requeued_jobs = jobs.requeue_expired(db, now)

    cutoff = now - timedelta(seconds=STALE_HEARTBEAT_SECONDS)
    with ctx.session_factory() as db:
        stale = list(
            db.scalars(
                select(TaskRun.id).where(
                    TaskRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
                    TaskRun.heartbeat_at < cutoff,
                )
            )
        )
        dead = list(
            db.scalars(
                select(TaskRun.id)
                .join(Job, Job.dedupe_key == TaskRun.id)
                .where(TaskRun.status == RunStatus.QUEUED, Job.status == JobStatus.DEAD)
            )
        )
    for run_id in stale:
        with ctx.session_factory() as db:
            outcome = runs.RunOutcome(
                status=RunStatus.FAILED,
                exit_reason="worker_lost",
                error={
                    "code": "worker_lost",
                    "message": "The worker stopped heartbeating; the run was abandoned",
                },
                executed=False,  # actual spend unknown: reservation is released, not charged
            )
            if runs.finalize_run(db, run_id, outcome, now=now, storage=ctx.storage):
                report.stale_run_ids.append(run_id)
    for run_id in dead:
        with ctx.session_factory() as db:
            outcome = runs.RunOutcome(
                status=RunStatus.FAILED,
                exit_reason="job_dead",
                error={"code": "job_dead", "message": "The execution job could not be completed"},
                executed=False,
            )
            if runs.finalize_run(db, run_id, outcome, now=now, storage=ctx.storage):
                report.dead_job_run_ids.append(run_id)

    # Tasks blocked by a daily/monthly budget that has since rolled over.
    with ctx.session_factory() as db:
        for company_id in db.scalars(select(Company.id)):
            report.budget_released_tasks += tasks.release_budget_blocked(
                db, company_id, None, due_before=now
            )
        db.commit()

    # Companies the circuit breaker paused come back after their cooldown (never a human's pause).
    with ctx.session_factory() as db:
        paused = list(db.scalars(select(Company.id).where(Company.status == CompanyStatus.PAUSED)))
    for company_id in paused:
        with ctx.session_factory() as db:
            if safety.maybe_auto_resume(db, company_id, now):
                db.commit()
                report.auto_resumed_company_ids.append(company_id)
            else:
                db.rollback()
    return report
