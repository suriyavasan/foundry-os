"""Redaction for data that leaves a runtime (RunEvent payloads): secret-looking keys become
``"[redacted]"``, known secret values are masked, and size is bounded."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.services.agents import looks_like_secret_key

REDACTED = "[redacted]"
MAX_STRING = 10_000
MAX_ITEMS = 200
MAX_DEPTH = 8
MAX_PAYLOAD_BYTES = 64_000


def _clean(value: Any, secrets: tuple[str, ...], depth: int) -> Any:
    if depth > MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (key, child) in enumerate(value.items()):
            if i >= MAX_ITEMS:
                out["[truncated]"] = f"{len(value) - MAX_ITEMS} more keys"
                break
            key = str(key)
            out[key] = REDACTED if looks_like_secret_key(key) else _clean(child, secrets, depth + 1)
        return out
    if isinstance(value, list | tuple):
        items = [_clean(v, secrets, depth + 1) for v in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"[{len(value) - MAX_ITEMS} more items truncated]")
        return items
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, REDACTED)
        if len(value) > MAX_STRING:
            value = value[:MAX_STRING] + "...[truncated]"
        return value
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:MAX_STRING]


def redact_payload(payload: Any, secrets: Iterable[str] = ()) -> dict[str, Any]:
    cleaned = _clean(
        payload if isinstance(payload, dict) else {"value": payload}, tuple(secrets), 0
    )
    encoded = json.dumps(cleaned, default=str)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        return {"truncated": True, "preview": encoded[:2000]}
    return cleaned
