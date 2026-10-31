"""M21 schema (migration 0021): the meeting tables, tasks.meeting_id and the policy limits. Existing
rows keep working and get the documented defaults; the downgrade removes everything again."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

TABLES = {"meetings", "meeting_participants", "meeting_messages"}
POLICY_COLUMNS = {
    "meetings_enabled",
    "max_meetings_per_agent_per_day",
    "max_meetings_per_company_per_day",
}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_0021_adds_the_tables_and_columns_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm21.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0020")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        task_id = _insert_legacy_rows(conn, ids["company"], ids["worker"])
    assert not TABLES & set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0021")
    assert set(inspect(engine).get_table_names()) >= TABLES
    assert "meeting_id" in _columns(engine, "tasks")
    assert _columns(engine, "company_policies") >= POLICY_COLUMNS
    fks = inspect(engine).get_foreign_keys("tasks")
    assert any(
        fk["referred_table"] == "meetings" and fk["constrained_columns"] == ["meeting_id"]
        for fk in fks
    )
    assert "ix_tasks_meeting_id" in {i["name"] for i in inspect(engine).get_indexes("tasks")}
    with engine.begin() as conn:
        # existing policy rows: meetings on, 3 per agent, 12 per company
        policy = conn.execute(
            text(
                "SELECT meetings_enabled, max_meetings_per_agent_per_day, "
                "max_meetings_per_company_per_day FROM company_policies WHERE company_id = :c"
            ),
            {"c": ids["company"]},
        ).one()
        assert tuple(policy) == (1, 3, 12)
        # an existing task has no meeting; a meeting with its rows can be stored and linked
        assert (
            conn.execute(
                text("SELECT meeting_id FROM tasks WHERE id = :i"), {"i": task_id}
            ).scalar()
            is None
        )
        mid = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO meetings (id, company_id, organizer_agent_id, title, agenda, "
                "expected_outcome, status, start_at, outcome, created_at, updated_at) VALUES "
                "(:i, :c, :a, 'T', 'A', 'O', 'scheduled', :t, '{}', :t, :t)"
            ),
            {"i": mid, "c": ids["company"], "a": ids["worker"], "t": _now()},
        )
        row = conn.execute(
            text("SELECT round, max_rounds FROM meetings WHERE id = :i"), {"i": mid}
        ).one()
        assert tuple(row) == (0, 2)
        conn.execute(
            text(
                "INSERT INTO meeting_participants (meeting_id, agent_id, company_id, role) "
                "VALUES (:m, :a, :c, 'organizer')"
            ),
            {"m": mid, "a": ids["worker"], "c": ids["company"]},
        )
        assert tuple(
            conn.execute(
                text(
                    "SELECT position, joined, passes FROM meeting_participants WHERE meeting_id = :m"
                ),
                {"m": mid},
            ).one()
        ) == (0, 0, 0)
        conn.execute(
            text("UPDATE tasks SET meeting_id = :m WHERE id = :i"), {"m": mid, "i": task_id}
        )

    # the legacy task survived the table rebuild
    with engine.begin() as conn:
        conn.execute(text("UPDATE tasks SET meeting_id = NULL"))
    command.downgrade(cfg, "0020")
    assert not TABLES & set(inspect(engine).get_table_names())
    assert "meeting_id" not in _columns(engine, "tasks")
    assert not POLICY_COLUMNS & _columns(engine, "company_policies")
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM tasks WHERE id = :i"), {"i": task_id}).scalar()
            == 1
        )
    command.upgrade(cfg, "0021")  # and up again
    assert set(inspect(engine).get_table_names()) >= TABLES
    engine.dispose()
