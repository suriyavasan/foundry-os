"""Installation-level CLI settings (auth mode, key reference, last test, install state), kept in the
existing ``system_state`` key/value table: one row per CLI. Never holds a secret (a credential id
only)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.guardrails import SystemState


def _key(cli_id: str) -> str:
    return f"cli:{cli_id}"[:40]


def load(db: Session, cli_id: str) -> dict[str, Any]:
    row = db.get(SystemState, _key(cli_id))
    return dict(row.value or {}) if row else {}


def save(db: Session, cli_id: str, **patch: Any) -> dict[str, Any]:
    row = db.get(SystemState, _key(cli_id))
    value = dict(row.value or {}) if row else {}
    value.update(patch)
    if row is None:
        db.add(SystemState(key=_key(cli_id), value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
    return value
