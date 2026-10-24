"""Automatic safety nets that need no human: the circuit breaker and budget alerts.

* **Circuit breaker** - ``policy.circuit_breaker_failures`` failed or timed-out runs within ten
  minutes auto-pause the company (the same kill switch as the Founder's pause: runs are cancelled,
  dispatch and schedules stop) and raise a critical alert. A human resumes it.
* **Budget thresholds** - alerts at 80 % and 100 % of the daily and monthly company budget.
  Spend is the ledger's committed amount (settled + held), never a guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.company import Company
from app.models.enums import AlertSeverity, CompanyStatus, RunStatus
from app.models.run import TaskRun
from app.services import alerts, audit, ledger, policy

BREAKER_WINDOW = timedelta(minutes=10)
BREAKER_ALERT_KEY = "circuit_breaker"


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
    failures = db.scalar(
        select(func.count(TaskRun.id)).where(
            TaskRun.company_id == company_id,
            TaskRun.status.in_([RunStatus.FAILED, RunStatus.TIMED_OUT]),
            TaskRun.finished_at >= now - BREAKER_WINDOW,
        )
    )
    if (failures or 0) < threshold:
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
        data={"failures": int(failures or 0), "threshold": threshold},
    )
    alerts.raise_alert(
        db,
        company_id,
        kind="circuit_breaker",
        severity=AlertSeverity.CRITICAL,
        title="The company was paused automatically",
        body=f"{failures} runs failed within {int(BREAKER_WINDOW.total_seconds() // 60)} minutes "
        f"(limit {threshold}). Look at the failed runs, fix the cause, then resume the company.",
        related_type="company",
        related_id=company_id,
        dedupe_key=BREAKER_ALERT_KEY,
        wake_founder=False,
    )
    return True


def resolve_breaker_alert(db: Session, company_id: str, principal: Principal | None) -> None:
    alerts.resolve_by_key(db, company_id, BREAKER_ALERT_KEY, principal)


def check_budget_thresholds(db: Session, company_id: str, now: datetime) -> None:
    company = db.get(Company, company_id)
    if company is None:
        return
    limits = policy.load_policy(db, company_id)
    periods = ledger.periods_for(now, company.timezone)
    checks = (
        ("monthly", limits.monthly_budget_micros, {"period_month": periods.month}, periods.month),
        ("daily", limits.daily_budget_micros, {"period_day": periods.day}, periods.day.isoformat()),
    )
    for label, budget, scope, period_key in checks:
        if not budget:
            continue
        used = ledger.committed_micros(db, company_id, **scope)
        for pct, severity in ((100, AlertSeverity.CRITICAL), (80, AlertSeverity.WARNING)):
            if used * 100 >= budget * pct:
                alerts.raise_alert(
                    db,
                    company_id,
                    kind="budget_threshold",
                    severity=severity,
                    title=f"{label.capitalize()} budget {pct}% used",
                    body=f"Spent or reserved {used} of {budget} micro-USD this "
                    f"{'day' if label == 'daily' else 'month'}. Local models cost $0; cloud "
                    "costs may be estimates.",
                    related_type="company",
                    related_id=company_id,
                    dedupe_key=f"budget:{label}:{pct}:{period_key}",
                )
                break
