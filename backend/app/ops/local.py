"""Helpers for commands that touch the data directory / database directly (no HTTP)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.cli.client import EXIT_FORBIDDEN, CliError
from app.core.config import Settings
from app.core.owner_token import MIN_TOKEN_LENGTH, OWNER_TOKEN_FILENAME
from app.db.session import build_engine, build_session_factory


def require_owner_token_access() -> Settings:
    """Proof of being on the server machine: the owner token file must exist and be readable by this
    user. (It is mode 0600 inside the data dir, which agent processes cannot reach.)"""
    settings = Settings()
    path = settings.resolved_data_dir / OWNER_TOKEN_FILENAME
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise CliError(
            f"This command only works on the server machine: it needs to read {path}.",
            exit_code=EXIT_FORBIDDEN,
            code="owner_token_unreadable",
        ) from exc
    if len(token) < MIN_TOKEN_LENGTH:
        raise CliError(
            f"{path} does not hold a valid owner token.",
            exit_code=EXIT_FORBIDDEN,
            code="owner_token_invalid",
        )
    return settings


@contextlib.contextmanager
def open_local_session(settings: Settings) -> Iterator[Session]:
    engine = build_engine(settings.resolved_database_url)
    try:
        factory = build_session_factory(engine)
        with factory() as db:
            yield db
    finally:
        engine.dispose()
