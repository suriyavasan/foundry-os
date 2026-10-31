"""M19 schema (migration 0019): the chat tables, their constraints, and a clean downgrade."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.conftest import alembic_config
from tests.test_migrations import _legacy_company

M19_TABLES = {"chat_channels", "chat_links", "chat_link_codes", "chat_messages"}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def test_0019_adds_the_chat_tables_with_their_constraints_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm19.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0018")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        user_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, is_local_owner, totp_enabled, "
                "recovery_code_hashes, created_at, updated_at) VALUES (:i, 'a@b.c', 'A', 0, 0, '[]', :t, :t)"
            ),
            {"i": user_id, "t": _now()},
        )
    assert not M19_TABLES & set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0019")
    insp = inspect(engine)
    assert set(insp.get_table_names()) >= M19_TABLES
    cols = {c["name"] for c in insp.get_columns("chat_messages")}
    assert {
        "direction",
        "external_message_id",
        "external_user_id",
        "ref_type",
        "ref_id",
        "status",
        "attempts",
        "next_attempt_at",
    } <= cols
    assert {c["name"] for c in insp.get_columns("chat_channels")} >= {
        "credential_id",
        "config",
        "poll_cursor",
        "lease_owner",
        "lease_expires_at",
        "last_error",
    }

    company = ids["company"]
    channel = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_channels (id, company_id, kind, name, status, config, created_at, updated_at) "
                "VALUES (:i, :c, 'telegram', 'bot', 'active', '{}', :t, :t)"
            ),
            {"i": channel, "c": company, "t": _now()},
        )

    def add_channel(kind="telegram"):
        conn.execute(
            text(
                "INSERT INTO chat_channels (id, company_id, kind, name, status, config, created_at, updated_at) "
                "VALUES (:i, :c, :k, 'dup', 'active', '{}', :t, :t)"
            ),
            {"i": str(uuid.uuid4()), "c": company, "k": kind, "t": _now()},
        )

    # one channel per kind and company
    with pytest.raises(IntegrityError), engine.begin() as conn:
        add_channel("telegram")

    def add_link(conn, external="42", revoked=None):
        conn.execute(
            text(
                "INSERT INTO chat_links (id, company_id, channel_id, user_id, external_user_id, external_chat_id, "
                "notify, linked_at, revoked_at) VALUES (:i, :c, :ch, :u, :e, :e, '{}', :t, :r)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": company,
                "ch": channel,
                "u": user_id,
                "e": external,
                "t": _now(),
                "r": revoked,
            },
        )

    with engine.begin() as conn:
        add_link(conn)
    # a live chat identity is unique per channel ...
    with pytest.raises(IntegrityError), engine.begin() as conn:
        add_link(conn)
    # ... a revoked one does not block a new link, and other identities are free
    with engine.begin() as conn:
        add_link(conn, "42", revoked=_now())
        add_link(conn, "43")

    def add_message(conn, ext, direction="in"):
        conn.execute(
            text(
                "INSERT INTO chat_messages (id, company_id, channel_id, direction, external_message_id, status, "
                "attempts, created_at) VALUES (:i, :c, :ch, :d, :e, 'received', 0, :t)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": company,
                "ch": channel,
                "d": direction,
                "e": ext,
                "t": _now(),
            },
        )

    with engine.begin() as conn:
        add_message(conn, "m1")
        add_message(conn, None, "out")  # outbox rows have no platform id yet
        add_message(conn, None, "out")
        add_message(conn, "m1", "out")  # same id, other direction
    with pytest.raises(IntegrityError), engine.begin() as conn:
        add_message(conn, "m1")  # inbound dedupe

    command.downgrade(cfg, "0018")
    assert not M19_TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0019")
    assert set(inspect(engine).get_table_names()) >= M19_TABLES
