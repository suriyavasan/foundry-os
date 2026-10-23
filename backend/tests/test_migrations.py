"""Alembic: upgrade/downgrade on a fresh DB, and the M2 policy backfill for existing companies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config

M2_TABLES = {
    "company_policies",
    "goals",
    "projects",
    "tasks",
    "task_dependencies",
    "task_runs",
    "run_events",
    "artifacts",
    "artifact_versions",
    "budget_reservations",
    "usage_records",
    "approvals",
    "decisions",
    "schedules",
    "schedule_fires",
    "jobs",
    "company_events",
}


def tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_on_a_fresh_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    assert tables(url) >= M2_TABLES
    command.downgrade(cfg, "0001")
    after = tables(url)
    assert not (M2_TABLES & after)
    assert {"companies", "agents", "audit_events", "users"} <= after  # M1 schema intact
    command.upgrade(cfg, "head")
    assert tables(url) >= M2_TABLES
    command.downgrade(cfg, "base")
    assert tables(url) <= {"alembic_version"}


def test_upgrade_backfills_a_policy_for_every_existing_company(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")
    engine = create_engine(url)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    ids = [str(uuid.uuid4()) for _ in range(3)]
    with engine.begin() as conn:
        for i, cid in enumerate(ids):
            conn.execute(
                text(
                    "INSERT INTO companies (id, name, slug, constraints, timezone, status, created_at, updated_at) "
                    "VALUES (:id, :n, :s, '[]', 'UTC', 'active', :t, :t)"
                ),
                {"id": cid, "n": f"Legacy {i}", "s": f"legacy-{i}", "t": now},
            )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT company_id, autonomy_mode, human_approval_required_for, max_concurrent_runs, "
                "max_delegation_depth, max_run_seconds, default_run_reservation_micros, "
                "budget_lock_version, daily_budget_micros FROM company_policies"
            )
        ).all()
    engine.dispose()
    assert sorted(r[0] for r in rows) == sorted(ids)
    for row in rows:
        assert row[1] == "autonomous"
        assert "external_purchase" in row[2] and "budget_increase" in row[2]
        assert (row[3], row[4], row[5], row[6], row[7], row[8]) == (2, 3, 900, 50_000, 0, None)


def test_stepwise_upgrade_creates_the_expected_indexes(tmp_path):
    cfg = alembic_config(f"sqlite:///{tmp_path / 'x.db'}")
    command.upgrade(cfg, "0001")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    idx = {i["name"] for i in inspect(engine).get_indexes("tasks")}
    engine.dispose()
    assert {"ix_tasks_company_status", "ix_tasks_assignee_agent_id"} <= idx


@pytest.mark.parametrize(
    ("table", "unique_cols"),
    [
        ("tasks", {"company_id", "idempotency_key"}),
        ("task_runs", {"idempotency_key"}),
        ("run_events", {"run_id", "seq"}),
        ("schedule_fires", {"schedule_id", "scheduled_for"}),
        ("artifact_versions", {"artifact_id", "version"}),
        ("usage_records", {"company_id", "dedupe_key"}),
        ("jobs", {"dedupe_key"}),
        ("task_dependencies", {"task_id", "depends_on_task_id"}),
        ("company_policies", {"company_id"}),
    ],
)
def test_idempotency_unique_constraints_exist_in_the_schema(db_url, table, unique_cols):
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        uniques = [set(u["column_names"]) for u in insp.get_unique_constraints(table)]
        uniques += [set(i["column_names"]) for i in insp.get_indexes(table) if i.get("unique")]
    finally:
        engine.dispose()
    assert unique_cols in uniques


def test_every_tenant_owned_table_has_an_indexed_company_id(db_url):
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        for table in M2_TABLES - {"jobs"}:  # system jobs may have a null company_id
            cols = {c["name"] for c in insp.get_columns(table)}
            assert "company_id" in cols, table
            indexed = [i["column_names"][0] for i in insp.get_indexes(table)] + [
                u["column_names"][0] for u in insp.get_unique_constraints(table)
            ]
            assert "company_id" in indexed, table
    finally:
        engine.dispose()
