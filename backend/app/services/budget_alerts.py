"""Budget threshold alerts (docs/specs/m6 section 6).

For every budget -- the company's daily and monthly budget and each agent's daily and monthly
budget -- an alert fires when committed spend (settled + held reservations: what the ledger
enforces) crosses a configured threshold (``policy.budget_alert_thresholds``, default 50/80/100 %).
It is idempotent per (scope, period, threshold): a ``budget_alert_marks`` row remembers each crossed
threshold, so resolving the alert does not make the same threshold fire again in the same period,
and one big jump over several thresholds raises one alert (the highest) and marks them all.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import AlertSeverity
from app.models.guardrails import BudgetAlertMark
from app.services import alerts, ledger, policy


def _severity(threshold: int) -> str:
    if threshold >= 100:
        return AlertSeverity.CRITICAL
    return AlertSeverity.WARNING if threshold >= 80 else AlertSeverity.INFO


def _new_marks(
    db: Session, company_id: str, scope: str, scope_id: str, period: str, crossed: list[int]
) -> list[int]:
    """Mark the crossed thresholds; returns the ones that were not marked before."""
    have = set(
        db.scalars(
            select(BudgetAlertMark.threshold).where(
                BudgetAlertMark.company_id == company_id,
                BudgetAlertMark.scope == scope,
                BudgetAlertMark.scope_id == scope_id,
                BudgetAlertMark.period_key == period,
            )
        )
    )
    fresh: list[int] = []
    for threshold in crossed:
        if threshold in have:
            continue
        try:
            with db.begin_nested():
                db.add(
                    BudgetAlertMark(
                        company_id=company_id,
                        scope=scope,
                        scope_id=scope_id,
                        period_key=period,
                        threshold=threshold,
                    )
                )
        except IntegrityError:  # a concurrent writer marked it first
            continue
        fresh.append(threshold)
    return fresh


def _check_scope(
    db: Session,
    company_id: str,
    *,
    scope: str,
    scope_id: str,
    period: str,
    label: str,
    budget: int | None,
    used: int,
    thresholds: list[int],
    who: str,
    related_type: str,
    related_id: str,
) -> None:
    if not budget:
        return
    crossed = sorted(t for t in thresholds if used * 100 >= budget * t)
    fresh = _new_marks(db, company_id, scope, scope_id, period, crossed)
    if not fresh:
        return
    top = max(fresh)
    alerts.raise_alert(
        db,
        company_id,
        kind="budget_threshold",
        severity=_severity(top),
        title=f"{who}{label.capitalize()} budget {top}% used",
        body=f"Spent or reserved {used} of {budget} micro-USD this "
        f"{'day' if label == 'daily' else 'month'}. Local models cost $0; cloud costs may be "
        "estimates.",
        related_type=related_type,
        related_id=related_id,
        dedupe_key=f"budget:{scope}:{scope_id}:{top}:{period}",
    )


def check(db: Session, company_id: str, now: datetime) -> None:
    """Raise threshold alerts for every budget of the company. No commit."""
    company = db.get(Company, company_id)
    if company is None:
        return
    limits = policy.load_policy(db, company_id)
    thresholds = sorted({int(t) for t in (limits.budget_alert_thresholds or []) if int(t) > 0})
    if not thresholds:
        return
    periods = ledger.periods_for(now, company.timezone)
    day_key, month_key = periods.day.isoformat(), periods.month
    for label, budget, kwargs, period in (
        ("monthly", limits.monthly_budget_micros, {"period_month": periods.month}, month_key),
        ("daily", limits.daily_budget_micros, {"period_day": periods.day}, day_key),
    ):
        _check_scope(
            db,
            company_id,
            scope=f"company_{label}",
            scope_id="",
            period=period,
            label=label,
            budget=budget,
            used=ledger.committed_micros(db, company_id, **kwargs),
            thresholds=thresholds,
            who="",
            related_type="company",
            related_id=company_id,
        )
    agents = db.scalars(
        select(Agent).where(
            Agent.company_id == company_id,
            (Agent.monthly_budget_micros.is_not(None)) | (Agent.daily_budget_micros.is_not(None)),
        )
    )
    for agent in agents:
        for label, budget, kwargs, period in (
            ("monthly", agent.monthly_budget_micros, {"period_month": periods.month}, month_key),
            ("daily", agent.daily_budget_micros, {"period_day": periods.day}, day_key),
        ):
            _check_scope(
                db,
                company_id,
                scope=f"agent_{label}",
                scope_id=agent.id,
                period=period,
                label=label,
                budget=budget,
                used=ledger.committed_micros(db, company_id, agent_id=agent.id, **kwargs),
                thresholds=thresholds,
                who=f"{agent.name}'s ",
                related_type="agent",
                related_id=agent.id,
            )
