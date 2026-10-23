"""Durable job queue. Leasing is portable: pick candidates, then a conditional
``UPDATE ... WHERE id = :id AND status = 'queued'`` and accept only if rowcount == 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utcnow
from app.models.enums import JobStatus
from app.models.run import Job

BACKOFF_BASE_SECONDS = 5
BACKOFF_CAP_SECONDS = 300
JOB_RETENTION_DAYS = 7


@dataclass(frozen=True)
class LeasedJob:
    id: str
    kind: str
    payload: dict[str, Any]
    company_id: str | None
    attempts: int
    max_attempts: int


def enqueue(
    db: Session,
    *,
    kind: str,
    payload: dict[str, Any],
    company_id: str | None = None,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job:
    """Add a job to the caller's transaction. A duplicate ``dedupe_key`` returns the original."""
    if dedupe_key:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    job = Job(
        company_id=company_id,
        kind=kind,
        payload=payload,
        status=JobStatus.QUEUED,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
        dedupe_key=dedupe_key,
    )
    db.add(job)
    db.flush()
    return job


def lease_next(
    factory: sessionmaker[Session],
    worker_id: str,
    now: datetime,
    *,
    lease_seconds: int = 60,
    kinds: list[str] | None = None,
) -> LeasedJob | None:
    with factory() as db:
        stmt = select(Job.id).where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
        if kinds:
            stmt = stmt.where(Job.kind.in_(kinds))
        candidates = list(db.scalars(stmt.order_by(Job.run_after, Job.created_at).limit(10)))
        for job_id in candidates:
            claimed = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                .values(
                    status=JobStatus.LEASED,
                    leased_by=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            )
            db.commit()
            if claimed.rowcount == 1:
                job = db.get(Job, job_id)
                assert job is not None
                return LeasedJob(
                    id=job.id,
                    kind=job.kind,
                    payload=dict(job.payload),
                    company_id=job.company_id,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                )
    return None


def extend_lease(
    db: Session, job_id: str, worker_id: str, now: datetime, lease_seconds: int = 60
) -> bool:
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.leased_by == worker_id, Job.status == JobStatus.LEASED)
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
    )
    db.commit()
    return result.rowcount == 1


def complete(db: Session, job_id: str, worker_id: str, now: datetime) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.leased_by == worker_id)
        .values(status=JobStatus.DONE, lease_expires_at=None, updated_at=now)
    )
    db.commit()


def fail(db: Session, job: LeasedJob, worker_id: str, error: str, now: datetime) -> str:
    """Record a failure: exponential backoff, ``dead`` once attempts are exhausted."""
    if job.attempts >= job.max_attempts:
        status, run_after = JobStatus.DEAD, None
    else:
        delay = min(BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1), BACKOFF_CAP_SECONDS)
        status, run_after = JobStatus.QUEUED, now + timedelta(seconds=delay)
    values: dict[str, Any] = {
        "status": status,
        "last_error": error[:2000],
        "lease_expires_at": None,
        "leased_by": None,
        "updated_at": now,
    }
    if run_after is not None:
        values["run_after"] = run_after
    db.execute(update(Job).where(Job.id == job.id, Job.leased_by == worker_id).values(**values))
    db.commit()
    return status.value


def requeue_expired(db: Session, now: datetime) -> int:
    """Leases whose holder vanished go back to ``queued`` (or ``dead`` when exhausted)."""
    expired = list(
        db.execute(
            select(Job.id, Job.attempts, Job.max_attempts).where(
                Job.status == JobStatus.LEASED, Job.lease_expires_at < now
            )
        ).all()
    )
    count = 0
    for job_id, attempts, max_attempts in expired:
        status = JobStatus.DEAD if attempts >= max_attempts else JobStatus.QUEUED
        result = db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.LEASED,
                Job.lease_expires_at < now,
            )
            .values(
                status=status,
                leased_by=None,
                lease_expires_at=None,
                last_error="lease expired",
                updated_at=now,
            )
        )
        count += result.rowcount or 0
    db.commit()
    return count


def prune_finished(db: Session, now: datetime) -> int:
    cutoff = now - timedelta(days=JOB_RETENTION_DAYS)
    result = db.execute(
        delete(Job).where(Job.status.in_([JobStatus.DONE, JobStatus.DEAD]), Job.updated_at < cutoff)
    )
    return int(result.rowcount or 0)
