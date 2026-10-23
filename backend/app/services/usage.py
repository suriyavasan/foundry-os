"""Usage aggregates (minimal in M2; M6 expands). Source of truth: ``usage_records``."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BadRequest
from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.company import Company
from app.models.ledger import UsageRecord
from app.models.work import Project
from app.schemas.usage import CostByKind, UsageRow


def _parse_bound(value: str, tz: ZoneInfo, *, end: bool) -> datetime:
    """Accepts ``YYYY-MM-DD`` (company-timezone day; ``to`` is inclusive of that day) or a
    full ISO-8601 instant (UTC if no offset)."""
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            if end:
                day += timedelta(days=1)
            return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequest(f"Invalid date/time: {value!r}") from exc
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _empty(key, label) -> UsageRow:
    return UsageRow(
        key=key,
        label=label,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        cost_micros=0,
        by_cost_kind=CostByKind(),
        records=0,
    )


def _add(row: UsageRow, rec_in, rec_out, rec_cached, cost, kind, count) -> None:
    row.input_tokens += int(rec_in or 0)
    row.output_tokens += int(rec_out or 0)
    row.cached_tokens += int(rec_cached or 0)
    row.cost_micros += int(cost or 0)
    row.records += int(count or 0)
    setattr(
        row.by_cost_kind,
        f"{kind}_micros",
        getattr(row.by_cost_kind, f"{kind}_micros") + int(cost or 0),
    )


def usage_report(
    db: Session,
    ctx: CompanyContext,
    *,
    from_: str | None,
    to: str | None,
    group_by: str,
    now: datetime | None = None,
) -> dict:
    company = db.get(Company, ctx.company_id)
    tz = ZoneInfo(company.timezone if company else "UTC")
    now = now or utcnow()
    local_now = now.astimezone(tz)
    start = (
        _parse_bound(from_, tz, end=False)
        if from_
        else datetime(local_now.year, local_now.month, 1, tzinfo=tz).astimezone(UTC)
    )
    end = _parse_bound(to, tz, end=True) if to else now + timedelta(seconds=1)
    if end <= start:
        raise BadRequest("'to' must be after 'from'")

    base = [
        UsageRecord.company_id == ctx.company_id,
        UsageRecord.occurred_at >= start,
        UsageRecord.occurred_at < end,
    ]
    totals = _empty(None, "Total")
    rows: dict[str | None, UsageRow] = {}

    if group_by == "day":
        for rec in db.scalars(select(UsageRecord).where(*base)):
            day = rec.occurred_at.astimezone(tz).date().isoformat()
            row = rows.setdefault(day, _empty(day, day))
            for target in (row, totals):
                _add(
                    target,
                    rec.input_tokens,
                    rec.output_tokens,
                    rec.cached_tokens,
                    rec.cost_micros,
                    rec.cost_kind,
                    1,
                )
        ordered = [rows[k] for k in sorted(rows)]
    else:
        column = {
            "agent": UsageRecord.agent_id,
            "project": UsageRecord.project_id,
            "model": UsageRecord.model,
        }[group_by]
        stmt = (
            select(
                column,
                UsageRecord.cost_kind,
                func.sum(UsageRecord.input_tokens),
                func.sum(UsageRecord.output_tokens),
                func.sum(UsageRecord.cached_tokens),
                func.sum(UsageRecord.cost_micros),
                func.count(UsageRecord.id),
            )
            .where(*base)
            .group_by(column, UsageRecord.cost_kind)
        )
        labels: dict[str, str] = {}
        for key, kind, t_in, t_out, t_cached, cost, count in db.execute(stmt).all():
            row = rows.setdefault(key, _empty(key, None))
            for target in (row, totals):
                _add(target, t_in, t_out, t_cached, cost, kind, count)
        keys = [k for k in rows if k]
        if group_by == "agent" and keys:
            labels = dict(
                db.execute(
                    select(Agent.id, Agent.name).where(
                        Agent.company_id == ctx.company_id, Agent.id.in_(keys)
                    )
                ).all()
            )
        elif group_by == "project" and keys:
            labels = dict(
                db.execute(
                    select(Project.id, Project.name).where(
                        Project.company_id == ctx.company_id, Project.id.in_(keys)
                    )
                ).all()
            )
        for key, row in rows.items():
            row.label = labels.get(key) if key else None
            if group_by == "model" and key:
                row.label = key
        ordered = sorted(rows.values(), key=lambda r: (-r.cost_micros, str(r.key)))

    return {
        "group_by": group_by,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
        "items": ordered,
        "totals": totals,
    }
