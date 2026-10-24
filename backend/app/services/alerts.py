"""Alerts: conditions the Founder should see (budget thresholds, circuit breaker, failures).

``raise_alert`` is idempotent per ``dedupe_key`` while an alert with that key is open, so a
repeating condition shows once. It never commits: the caller's transaction owns it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.models.autonomy import Alert
from app.models.enums import AlertSeverity, AlertStatus
from app.services import audit, events
from app.services.pagination import paginate


def raise_alert(
    db: Session,
    company_id: str,
    *,
    kind: str,
    severity: str = AlertSeverity.WARNING,
    title: str,
    body: str | None = None,
    related_type: str | None = None,
    related_id: str | None = None,
    dedupe_key: str | None = None,
    wake_founder: bool = True,
) -> Alert | None:
    """Open an alert unless one with ``dedupe_key`` is already open. No commit."""
    if dedupe_key:
        db.flush()
        open_one = db.scalar(
            select(Alert.id).where(
                Alert.company_id == company_id,
                Alert.dedupe_key == dedupe_key,
                Alert.status == AlertStatus.OPEN,
            )
        )
        if open_one is not None:
            return None
    alert = Alert(
        company_id=company_id,
        severity=str(severity),
        kind=kind[:48],
        title=title[:300],
        body=body,
        related_type=related_type,
        related_id=related_id,
        dedupe_key=dedupe_key,
    )
    db.add(alert)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=None,
        action="alert.raised",
        target_type="alert",
        target_id=alert.id,
        summary=f"Alert ({alert.severity}): {alert.title}",
        data={"kind": alert.kind, "related_type": related_type, "related_id": related_id},
    )
    events.emit(
        db,
        company_id,
        "alert.raised",
        {
            "alert_id": alert.id,
            "severity": alert.severity,
            "kind": alert.kind,
            "title": alert.title,
            "related_type": related_type,
            "related_id": related_id,
        },
    )
    if wake_founder and alert.severity != AlertSeverity.INFO:
        from app.services import wakes

        wakes.wake_founder(
            db,
            company_id,
            "alert",
            f"Alert ({alert.severity}): {alert.title}",
            ref_type="alert",
            ref_id=alert.id,
        )
    return alert


def resolve_by_key(
    db: Session, company_id: str, dedupe_key: str, principal: Principal | None = None
) -> int:
    """Resolve the open alert(s) with this key (a condition that cleared). No commit."""
    rows = list(
        db.scalars(
            select(Alert).where(
                Alert.company_id == company_id,
                Alert.dedupe_key == dedupe_key,
                Alert.status == AlertStatus.OPEN,
            )
        )
    )
    for alert in rows:
        _resolve(db, alert, principal)
    return len(rows)


def _resolve(db: Session, alert: Alert, principal: Principal | None, now: datetime | None = None):
    now = now or utcnow()
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = now
    alert.resolved_by_type = principal.type.value if principal else "system"
    alert.resolved_by_id = principal.id if principal else None
    alert.updated_at = now
    audit.record(
        db,
        company_id=alert.company_id,
        principal=principal,
        action="alert.resolved",
        target_type="alert",
        target_id=alert.id,
        summary=f"Resolved alert: {alert.title}",
        data={"kind": alert.kind},
    )
    events.emit(
        db,
        alert.company_id,
        "alert.resolved",
        {"alert_id": alert.id, "kind": alert.kind, "severity": alert.severity},
    )


# ---------------------------------------------------------------- owner API


def _readable(ctx: CompanyContext) -> None:
    from app.core.errors import Forbidden

    if ctx.principal.is_agent:
        raise Forbidden("Agents read alerts through GET /state")


def list_alerts(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    severity: str | None = None,
):
    _readable(ctx)
    stmt = select(Alert).where(Alert.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    return paginate(db, stmt, Alert, limit=limit, cursor=cursor, descending=True)


def get_alert(db: Session, ctx: CompanyContext, alert_id: str) -> Alert:
    _readable(ctx)
    alert = db.scalar(select(Alert).where(Alert.id == alert_id, Alert.company_id == ctx.company_id))
    if alert is None:
        raise NotFound("Alert not found")
    return alert


def resolve_alert(db: Session, ctx: CompanyContext, alert_id: str) -> Alert:
    """Humans only (viewers read). Resolving an already resolved alert is a no-op."""
    require_human_admin(ctx, "resolve alerts")
    alert = get_alert(db, ctx, alert_id)
    if alert.status == AlertStatus.OPEN:
        _resolve(db, alert, ctx.principal)
        db.commit()
    return alert


def open_alerts(db: Session, company_id: str, limit: int = 20) -> list[Alert]:
    return list(
        db.scalars(
            select(Alert)
            .where(Alert.company_id == company_id, Alert.status == AlertStatus.OPEN)
            .order_by(Alert.created_at.desc(), Alert.id)
            .limit(limit)
        )
    )
