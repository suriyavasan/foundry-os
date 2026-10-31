"""M22 schema (migration 0022): tasks.requires_human & co, outsource_jobs, the policy ceiling.
Existing rows keep working with the documented defaults; the downgrade removes everything again."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

TASK_COLUMNS = {"requires_human", "human_category", "human_instructions", "requested_by_agent_id"}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_0022_adds_the_columns_and_the_table_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm22.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0021")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        task_id = _insert_legacy_rows(conn, ids["company"], ids["worker"])
    assert "outsource_jobs" not in set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0022")
    assert "outsource_jobs" in set(inspect(engine).get_table_names())
    assert _columns(engine, "tasks") >= TASK_COLUMNS
    assert "outsource_max_job_micros" in _columns(engine, "company_policies")
    assert "ix_tasks_company_requires_human" in {
        i["name"] for i in inspect(engine).get_indexes("tasks")
    }
    with engine.begin() as conn:
        # an existing task is not human work; an existing policy gets the $200 per-job ceiling
        row = conn.execute(
            text(
                "SELECT requires_human, human_category, human_instructions, requested_by_agent_id "
                "FROM tasks WHERE id = :i"
            ),
            {"i": task_id},
        ).one()
        assert tuple(row) == (0, None, None, None)
        ceiling = conn.execute(
            text("SELECT outsource_max_job_micros FROM company_policies WHERE company_id = :c"),
            {"c": ids["company"]},
        ).scalar()
        assert ceiling == 200_000_000
        jid = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO outsource_jobs (id, company_id, title, brief, skills, marketplace, "
                "budget_micros, status, delivered_artifact_ids, created_at, updated_at) VALUES "
                "(:i, :c, 'Logo', 'Draw it', '[]', 'fiverr', 50000000, 'draft', '[]', :t, :t)"
            ),
            {"i": jid, "c": ids["company"], "t": _now()},
        )
        conn.execute(
            text(
                "UPDATE tasks SET requires_human = 1, human_category = 'call', "
                "requested_by_agent_id = :a WHERE id = :i"
            ),
            {"a": ids["worker"], "i": task_id},
        )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM outsource_jobs"))
        conn.execute(text("UPDATE tasks SET requested_by_agent_id = NULL"))
    command.downgrade(cfg, "0021")
    assert "outsource_jobs" not in set(inspect(engine).get_table_names())
    assert not TASK_COLUMNS & _columns(engine, "tasks")
    assert "outsource_max_job_micros" not in _columns(engine, "company_policies")
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM tasks WHERE id = :i"), {"i": task_id}).scalar()
            == 1
        )
    command.upgrade(cfg, "0022")  # and up again
    assert "outsource_jobs" in set(inspect(engine).get_table_names())
    engine.dispose()
