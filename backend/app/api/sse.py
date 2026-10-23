"""Server-sent events over the company_events outbox (decision D10).

The generator is factored out of the route so tests can drive it without a socket: it polls
``company_events`` for ``id > cursor`` through short DB sessions, so it works across API and
worker processes and is resumable with ``Last-Event-ID``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.core.time import format_ts
from app.services import events

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
BATCH = 200


def format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, separators=(",", ":"), default=str)
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


def _fetch(
    factory: sessionmaker[Session], company_id: str, after_id: int, limit: int
) -> list[dict[str, Any]]:
    with factory() as db:
        return [
            {
                "id": e.id,
                "type": e.type,
                "company_id": e.company_id,
                "payload": e.payload,
                "created_at": format_ts(e.created_at),
            }
            for e in events.fetch_after(db, company_id, after_id, limit=limit)
        ]


def resolve_cursor(after: int | None, last_event_id: str | None, latest: int) -> int:
    """``?after=`` wins, then the ``Last-Event-ID`` header; with neither, start at "now"."""
    if after is not None:
        return after
    if last_event_id and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return latest


def latest_id(factory: sessionmaker[Session], company_id: str) -> int:
    with factory() as db:
        return events.latest_event_id(db, company_id)


async def event_stream(
    factory: sessionmaker[Session],
    company_id: str,
    after_id: int,
    *,
    poll_interval: float = POLL_SECONDS,
    heartbeat_interval: float = HEARTBEAT_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    batch: int = BATCH,
) -> AsyncIterator[str]:
    """Yield SSE frames for events with ``id > after_id``, then keep tailing."""
    yield "retry: 3000\n\n"
    cursor = after_id
    last_sent = monotonic()
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        rows = await run_in_threadpool(_fetch, factory, company_id, cursor, batch)
        for row in rows:
            yield format_sse(row)
            cursor = row["id"]
        if rows:
            last_sent = monotonic()
            if len(rows) >= batch:
                continue  # more waiting: don't sleep
        elif monotonic() - last_sent >= heartbeat_interval:
            yield ": keepalive\n\n"
            last_sent = monotonic()
        await sleep(poll_interval)
