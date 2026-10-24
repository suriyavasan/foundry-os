"""Sliding-window rate limits (docs/specs/m6 section 2), DB-backed and portable.

Every counted action is one ``rate_limit_hits`` row; a window is "rows newer than now - window".
A row exactly ``window`` old is OUTSIDE the window (strict ``>``): at the edge the action that is
``window`` seconds after the first one is allowed again. Limits exist per agent and per company;
the first one exceeded refuses the action (429 ``rate_limited``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.guardrails import registry
from app.models.guardrails import RateLimitHit

HOUR = 3600
DAY = 86_400


@dataclass(frozen=True)
class Limit:
    key: str
    window_seconds: int
    per_agent: int
    per_company: int


# Conservative defaults. They are code, not policy: raising them is a release decision, and an
# owner who wants a hard stop for one action uses a guardrail rule.
LIMITS: dict[str, Limit] = {
    lim.key: lim
    for lim in (
        Limit(registry.API_CALLS, 60, per_agent=120, per_company=600),
        Limit(registry.TASKS_CREATED, HOUR, per_agent=30, per_company=150),
        Limit(registry.EXTERNAL_COMMS, DAY, per_agent=10, per_company=40),
        Limit(registry.HIRES, DAY, per_agent=10, per_company=20),
        Limit(registry.SKILL_INSTALLS, DAY, per_agent=10, per_company=30),
        Limit(registry.WEB_FETCHES, HOUR, per_agent=120, per_company=600),
    )
}
RETENTION = timedelta(days=2)


@dataclass(frozen=True)
class Window:
    key: str
    agent_id: str | None
    limit: int
    used: int
    window_seconds: int
    resets_at: datetime | None


def _usage(
    db: Session, company_id: str, key: str, window: int, now: datetime, agent_id: str | None
) -> tuple[int, datetime | None]:
    cutoff = now - timedelta(seconds=window)
    stmt = select(func.count(RateLimitHit.id), func.min(RateLimitHit.occurred_at)).where(
        RateLimitHit.company_id == company_id,
        RateLimitHit.key == key,
        RateLimitHit.occurred_at > cutoff,
    )
    if agent_id is not None:
        stmt = stmt.where(RateLimitHit.agent_id == agent_id)
    count, oldest = db.execute(stmt).one()
    return int(count or 0), oldest


def window_for(
    db: Session, company_id: str, key: str, now: datetime, agent_id: str | None
) -> Window:
    lim = LIMITS[key]
    used, oldest = _usage(db, company_id, key, lim.window_seconds, now, agent_id)
    return Window(
        key=key,
        agent_id=agent_id,
        limit=lim.per_agent if agent_id else lim.per_company,
        used=used,
        window_seconds=lim.window_seconds,
        resets_at=oldest + timedelta(seconds=lim.window_seconds) if oldest else None,
    )


def exceeded(
    db: Session, company_id: str, agent_id: str, keys: tuple[str, ...], now: datetime
) -> Window | None:
    """The first window already full for this agent or its company, else None."""
    for key in keys:
        if key not in LIMITS:
            continue
        for scope_agent in (agent_id, None):
            window = window_for(db, company_id, key, now, scope_agent)
            if window.used >= window.limit:
                return window
    return None


def record(
    db: Session, company_id: str, agent_id: str | None, keys: tuple[str, ...], now: datetime
) -> None:
    for key in keys:
        if key in LIMITS:
            db.add(RateLimitHit(company_id=company_id, agent_id=agent_id, key=key, occurred_at=now))


def status(
    db: Session, company_id: str, now: datetime, agent_id: str | None = None
) -> list[Window]:
    """Company windows, plus the windows of ``agent_id`` — or, without one, of every agent that
    has activity in some window."""
    out = [window_for(db, company_id, key, now, None) for key in LIMITS]
    if agent_id is not None:
        out += [window_for(db, company_id, key, now, agent_id) for key in LIMITS]
        return out
    for key, lim in LIMITS.items():
        cutoff = now - timedelta(seconds=lim.window_seconds)
        ids = db.scalars(
            select(RateLimitHit.agent_id)
            .where(
                RateLimitHit.company_id == company_id,
                RateLimitHit.key == key,
                RateLimitHit.occurred_at > cutoff,
                RateLimitHit.agent_id.is_not(None),
            )
            .distinct()
        )
        out += [window_for(db, company_id, key, now, a) for a in sorted(i for i in ids if i)]
    return out


def prune(db: Session, now: datetime) -> int:
    result = db.execute(delete(RateLimitHit).where(RateLimitHit.occurred_at < now - RETENTION))
    return int(result.rowcount or 0)
