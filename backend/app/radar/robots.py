"""robots.txt (RFC 9309) for the radar fetcher.

* The file is fetched through the radar's own guarded client (SSRF guard, size cap) and cached for
  24 hours per host in ``radar_host_state``; nothing is fetched twice a day.
* ``4xx`` (no file) means allowed; ``5xx``, a timeout or an unreachable host means **disallowed
  for 24 hours** (we do not crawl what we cannot ask about).
* ``Crawl-delay`` is honoured, capped at 60 seconds.
* Matching uses ``urllib.robotparser`` on the cached body, for the product token
  ``FoundryOS-Radar``; the more specific group wins and ``*`` is the fallback, as in the RFC.

Python's parser treats a 401/403 robots.txt as "disallow all"; RFC 9309 says any 4xx means the file
is unavailable (allowed). We follow the RFC, because we never hand a status to the parser: it only
ever sees a body.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.robotparser import RobotFileParser

from app.radar import (
    MAX_CRAWL_DELAY_SECONDS,
    MAX_ROBOTS_BYTES,
    ROBOTS_FAILURE_TTL_SECONDS,
    ROBOTS_TTL_SECONDS,
    USER_AGENT_TOKEN,
)

OK = "ok"
MISSING = "missing"
UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class RobotsRecord:
    """What is cached per host."""

    status: str  # ok | missing | unreachable
    body: str | None
    fetched_at: datetime


@dataclass(frozen=True)
class RobotsVerdict:
    allowed: bool
    reason: str  # allowed | disallowed | missing | unreachable
    crawl_delay_seconds: int | None = None


def is_fresh(record: RobotsRecord | None, now: datetime) -> bool:
    if record is None:
        return False
    ttl = ROBOTS_FAILURE_TTL_SECONDS if record.status == UNREACHABLE else ROBOTS_TTL_SECONDS
    return now - record.fetched_at < timedelta(seconds=ttl)


def record_from_response(
    status_code: int | None, body: bytes | None, now: datetime
) -> RobotsRecord:
    """The cache entry for a robots.txt response: ``status_code`` None = no response at all."""
    if status_code is None or status_code >= 500 or status_code == 429:
        return RobotsRecord(UNREACHABLE, None, now)
    if 400 <= status_code < 500:
        return RobotsRecord(MISSING, None, now)
    if status_code >= 300:  # an unfollowed redirect: treat like unreachable, never like "allow"
        return RobotsRecord(UNREACHABLE, None, now)
    text = (body or b"")[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
    return RobotsRecord(OK, text, now)


def evaluate(record: RobotsRecord, url_path_and_query: str) -> RobotsVerdict:
    """May we fetch ``url_path_and_query`` (``/path?query``) on the host of ``record``?"""
    if record.status == UNREACHABLE:
        return RobotsVerdict(False, UNREACHABLE)
    if record.status == MISSING or not record.body:
        return RobotsVerdict(True, MISSING if record.status == MISSING else "allowed")
    parser = RobotFileParser()
    parser.parse(record.body.splitlines())
    path = url_path_and_query if url_path_and_query.startswith("/") else "/" + url_path_and_query
    # ``can_fetch`` wants a URL; only its path and query matter for a body we already hold.
    allowed = parser.can_fetch(USER_AGENT_TOKEN, "https://robots.invalid" + path)
    delay = parser.crawl_delay(USER_AGENT_TOKEN)
    crawl_delay: int | None = None
    if delay is not None:
        try:
            crawl_delay = max(0, min(int(float(delay)), MAX_CRAWL_DELAY_SECONDS))
        except (TypeError, ValueError):
            crawl_delay = None
    return RobotsVerdict(allowed, "allowed" if allowed else "disallowed", crawl_delay)
