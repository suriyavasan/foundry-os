"""Keyset pagination. Cursor = opaque base64url of ``[created_at ISO, id]``."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from app.core.errors import BadRequest

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = json.dumps([created_at.astimezone(UTC).isoformat(), row_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_raw, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        created_at = datetime.fromisoformat(created_raw)
        if created_at.tzinfo is None or not isinstance(row_id, str):
            raise ValueError("bad cursor")
        return created_at, row_id
    except Exception as exc:
        raise BadRequest("Invalid cursor") from exc


def paginate(
    db: Session,
    stmt: Select[Any],
    model: Any,
    *,
    limit: int,
    cursor: str | None,
    descending: bool,
) -> tuple[list[Any], str | None]:
    """Order ``stmt`` by (created_at, id), apply the cursor, return (rows, next_cursor)."""
    created, ident = model.created_at, model.id
    if cursor:
        c_created, c_id = decode_cursor(cursor)
        cmp = (lambda a, b: a < b) if descending else (lambda a, b: a > b)
        stmt = stmt.where(
            or_(cmp(created, c_created), and_(created == c_created, cmp(ident, c_id)))
        )
    order = (created.desc(), ident.desc()) if descending else (created.asc(), ident.asc())
    rows = list(db.scalars(stmt.order_by(*order).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return rows, next_cursor
