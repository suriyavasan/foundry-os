from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker


def build_engine(url: str, *, enforce_foreign_keys: bool = True) -> Engine:
    """Create an engine. SQLite gets WAL + foreign key enforcement + a busy timeout."""
    is_sqlite = make_url(url).get_backend_name() == "sqlite"
    kwargs: dict = {}
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, **kwargs)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute(f"PRAGMA foreign_keys={'ON' if enforce_foreign_keys else 'OFF'}")
            cur.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """``SessionLocal`` equivalent, bound to a specific engine (one per app instance)."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
