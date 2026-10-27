"""Automatic safety nets that need no human: the circuit breaker and budget alerts.

* **Circuit breaker** - ``policy.circuit_breaker_failures`` failed or timed-out runs within ten
  minutes auto-pause the company (the same kill switch as the Founder's pause: runs are cancelled,
  dispatch and schedules stop) and raise a critical alert. Only *permanent* failures count: a
  429 or an overloaded provider (``retry_policy``) says nothing about the company. A human resumes
  it, or - ``maybe_auto_resume`` - it resumes itself after ``circuit_breaker_cooldown_seconds``,
  at most twice in 24 hours; a company a human paused is never resumed automatically.
* **Budget thresholds** - alerts at the configured percentages (default 50/80/100) of every daily
  and monthly budget, idempotent per scope/period/threshold (``budget_alerts``). Spend is the
  ledger's committed amount (settled + held), never a guess.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.audit import AuditEvent
from app.models.company import Company
from app.models.enums import ActorType, AlertSeverity, CompanyStatus, RunStatus
from app.models.run import TaskRun
from app.services import alerts, audit, policy, retry_policy

BREAKER_WINDOW = timedelta(minutes=10)
BREAKER_ALERT_KEY = "circuit_breaker"
AUTO_RESUME_WINDOW = timedelta(hours=24)
MAX_AUTO_RESUMES = 2  # per AUTO_RESUME_WINDOW; after that only a human resumes the company


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def check_circuit_breaker(db: Session, company_id: str, now: datetime) -> bool:
    """Call after a run failed. Pauses the company when the failure rate trips the breaker.
    Returns True when it tripped. No commit."""
    limits = policy.load_policy(db, company_id)
    threshold = limits.circuit_breaker_failures
    if threshold <= 0:
        return False
    company = db.get(Company, company_id)
    if company is None or company.status != CompanyStatus.ACTIVE:
        return False
    db.flush()
    recent = db.execute(
        select(TaskRun.error, TaskRun.exit_reason).where(
            TaskRun.company_id == company_id,
            TaskRun.status.in_([RunStatus.FAILED, RunStatus.TIMED_OUT]),
            TaskRun.finished_at >= now - BREAKER_WINDOW,
        )
    ).all()
    # Transient failures (429, overloaded, lost worker ...) are the provider's or the machine's
    # problem, not evidence that the company's work is broken.
    failures = sum(
        1 for error, reason in recent if not retry_policy.is_transient_run(error, reason)
    )
    if failures < threshold:
        return False
    from app.services import control

    company.status = CompanyStatus.PAUSED
    company.updated_at = now
    control.apply_status_effects(db, None, company)
    audit.record(
        db,
        company_id=company_id,
        principal=None,
        action="company.auto_paused",
        target_type="company",
        target_id=company_id,
        summary=f"Circuit breaker: {failures} failed runs in "
        f"{int(BREAKER_WINDOW.total_seconds() // 60)} "
        "minutes; the company was paused",
        data={
            "failures": failures,
            "threshold": threshold,
            "paused_at": now.isoformat(),
            "cooldown_seconds": limits.circuit_breaker_cooldown_seconds,
        },
    )
    alerts.raise_alert(
        db,
        company_id,
        kind="circuit_breaker",
        severity=AlertSeverity.CRITICAL,
        title="The company was paused automatically",
        body=f"{failures} runs failed within {int(BREAKER_WINDOW.total_seconds() // 60)} minutes "
        f"(limit {threshold}). Look at the failed runs, fix the cause, then resume the company."
        + (
            f" It resumes itself after {limits.circuit_breaker_cooldown_seconds // 60} minutes "
            f"(at most {MAX_AUTO_RESUMES} times in 24 hours)."
            if limits.circuit_breaker_cooldown_seconds > 0
            else ""
        ),
        related_type="company",
        related_id=company_id,
        dedupe_key=BREAKER_ALERT_KEY,
        wake_founder=False,
    )
    return True


def maybe_auto_resume(db: Session, company_id: str, now: datetime) -> bool:
    """Resume a company the circuit breaker paused, once ``circuit_breaker_cooldown_seconds`` have
    passed. Never resumes a company a human paused (or while the emergency stop is on), at most
    ``MAX_AUTO_RESUMES`` times per 24 hours, and not at all when the cooldown is 0. Returns True
    when it resumed. No commit."""
    from app.services import control, emergency

    company = db.get(Company, company_id)
    if company is None or company.status != CompanyStatus.PAUSED:
        return False
    limits = policy.load_policy(db, company_id)
    if limits.circuit_breaker_cooldown_seconds <= 0 or emergency.is_active(db):
        return False
    # Who paused it last? The breaker pauses as the system; a human pause is a human actor. Only
    # the system's pause (backed by a breaker audit event) is ours to undo.
    last = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.company_id == company_id,
            AuditEvent.action.in_(["company.paused", "company.resumed"]),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    if last is None or last.action != "company.paused" or last.actor_type != ActorType.SYSTEM:
        return False
    tripped = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.company_id == company_id, AuditEvent.action == "company.auto_paused")
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    if tripped is None:
        return False
    paused_at = _parse_time((tripped.data or {}).get("paused_at")) or tripped.created_at
    if now < paused_at + timedelta(seconds=limits.circuit_breaker_cooldown_seconds):
        return False
    resumes = [
        _parse_time((e.data or {}).get("resumed_at")) or e.created_at
        for e in db.scalars(
            select(AuditEvent).where(
                AuditEvent.company_id == company_id, AuditEvent.action == "company.auto_resumed"
            )
        )
    ]
    recent = [t for t in resumes if t > now - AUTO_RESUME_WINDOW]
    if len(recent) >= MAX_AUTO_RESUMES:
        return False  # tripped again and again: a human must look
    company.status = CompanyStatus.ACTIVE
    company.updated_at = now
    control.apply_status_effects(db, None, company)
    audit.record(
        db,
        company_id=company_id,
        principal=None,
        action="company.auto_resumed",
        target_type="company",
        target_id=company_id,
        summary=f"The circuit breaker cooled down ({limits.circuit_breaker_cooldown_seconds}s); "
        f"the company resumed itself (resume {len(recent) + 1} of {MAX_AUTO_RESUMES} in 24 h)",
        data={
            "resumed_at": now.isoformat(),
            "paused_at": paused_at.isoformat(),
            "auto_resumes_24h": len(recent) + 1,
        },
    )
    return True


def resolve_breaker_alert(db: Session, company_id: str, principal: Principal | None) -> None:
    alerts.resolve_by_key(db, company_id, BREAKER_ALERT_KEY, principal)


def check_budget_thresholds(db: Session, company_id: str, now: datetime) -> None:
    """Threshold alerts for every budget (company and agents); see ``budget_alerts``."""
    from app.services import budget_alerts

    budget_alerts.check(db, company_id, now)
