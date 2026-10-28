"""The daily cap on web-search queries inside the radar routine (owner decision 3: at most 4).

``web_search`` goes through DuckDuckGo with no API agreement, and the radar routine is the one task
that would run it every day. The runtime asks ``POST /guardrails/check-tool`` before each
search; for a run whose task was fired by the ``market-radar`` schedule this module enforces
``SEARCH_QUERIES_PER_DAY`` per company in code (``rate_limit_hits``), on top of the general
``web_fetches_per_hour`` limits. Other tasks are not affected.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import RateLimited
from app.core.time import utcnow
from app.guardrails import ratelimit
from app.models.schedule import Schedule
from app.services.agent_auth import AgentRunContext

SEARCH_KEY = "radar_search_queries_per_day"
SEARCH_QUERIES_PER_DAY = 4
SEARCH_TOOLS = ("web_search",)
RADAR_DAILY_KIND = "daily"

# Registered here (not in ratelimit.py) so the limit lives next to the rule it serves.
ratelimit.LIMITS.setdefault(
    SEARCH_KEY,
    ratelimit.Limit(
        SEARCH_KEY,
        ratelimit.DAY,
        per_agent=SEARCH_QUERIES_PER_DAY,
        per_company=SEARCH_QUERIES_PER_DAY,
    ),
)


def is_radar_daily_run(db: Session, rc: AgentRunContext) -> bool:
    """The run's task was fired by a ``market-radar`` schedule."""
    key = rc.task.idempotency_key or ""
    if not key.startswith("schedule:"):
        return False
    parts = key.split(":")
    if len(parts) < 3:
        return False
    schedule = db.get(Schedule, parts[1])
    if schedule is None or schedule.company_id != rc.ctx.company_id:
        return False
    return (schedule.task_template or {}).get("radar_kind") == RADAR_DAILY_KIND


def enforce(db: Session, rc: AgentRunContext, tool: str, now: datetime | None = None) -> bool:
    """Raise 429 when the company's radar queries for today are used up. Returns whether the cap
    applies to this call (so the caller records it once the check passed)."""
    if tool not in SEARCH_TOOLS or not is_radar_daily_run(db, rc):
        return False
    now = now or utcnow()
    window = ratelimit.window_for(db, rc.ctx.company_id, SEARCH_KEY, now, None)
    if window.used >= window.limit:
        raise RateLimited(
            f"The radar may run {SEARCH_QUERIES_PER_DAY} web searches a day; today's are used up. "
            "Work with the items you already have.",
            details={
                "key": SEARCH_KEY,
                "scope": "company",
                "limit": window.limit,
                "used": window.used,
                "window_seconds": window.window_seconds,
                "retry_after_seconds": 3600,
            },
        )
    return True


def record(db: Session, rc: AgentRunContext, now: datetime | None = None) -> None:
    ratelimit.record(db, rc.ctx.company_id, rc.agent.id, (SEARCH_KEY,), now or utcnow())
    db.commit()
