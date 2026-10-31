"""M18 Part B schema (migration 0020): the work-graph columns. Existing companies get both policy
switches on; runs get the nullable graph columns; the downgrade removes them again."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_0020_turns_the_work_graph_on_for_existing_companies_and_downgrades(tmp_path):
    url = f"sqlite:///{tmp_path / 'wg.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0019")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        _insert_legacy_rows(conn, ids["company"], ids["worker"])
    assert "graph_thread_id" not in _columns(engine, "task_runs")

    command.upgrade(cfg, "0020")
    assert {"graph_thread_id", "proposed_result"} <= _columns(engine, "task_runs")
    assert {"self_check_enabled", "work_graph_enabled"} <= _columns(engine, "company_policies")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT self_check_enabled, work_graph_enabled FROM company_policies "
                "WHERE company_id = :c"
            ),
            {"c": ids["company"]},
        ).one()
    assert tuple(row) == (1, 1)

    command.downgrade(cfg, "0019")
    assert "graph_thread_id" not in _columns(engine, "task_runs")
    assert "work_graph_enabled" not in _columns(engine, "company_policies")
