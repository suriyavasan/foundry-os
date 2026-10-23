from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def format_ts(value: datetime) -> str:
    """ISO-8601 UTC with a trailing Z, e.g. 2026-10-23T12:00:00Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
