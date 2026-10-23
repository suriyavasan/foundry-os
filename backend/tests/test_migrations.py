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


# ------------------------------------------------------------------ 0003: skills & HR

M4_TABLES = {"skill_blobs", "skills", "agent_skills", "role_templates"}


def test_0003_upgrade_downgrade_roundtrip(tmp_path):
    url = f"sqlite:///{tmp_path / 'm4.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    assert tables(url) >= M4_TABLES
    engine = create_engine(url)
    insp = inspect(engine)
    assert {"role_template", "role_family"} <= {c["name"] for c in insp.get_columns("agents")}
    assert {"auto_activate_hires", "hire_default_monthly_budget_micros"} <= {
        c["name"] for c in insp.get_columns("company_policies")
    }
    assert "blocked_reason" in {c["name"] for c in insp.get_columns("tasks")}
    assert {"subject_type", "subject_id"} <= {c["name"] for c in insp.get_columns("approvals")}
    skill_uniques = [set(u["column_names"]) for u in insp.get_unique_constraints("skills")]
    assert {"company_id", "slug"} in skill_uniques
    assert {"agent_id", "skill_id"} in [
        set(u["column_names"]) for u in insp.get_unique_constraints("agent_skills")
    ]
    engine.dispose()
    command.downgrade(cfg, "0002")
    assert not (M4_TABLES & tables(url)) and tables(url) >= M2_TABLES
    engine = create_engine(url)
    assert "role_family" not in {c["name"] for c in inspect(engine).get_columns("agents")}
    engine.dispose()
    command.upgrade(cfg, "head")
    assert tables(url) >= M4_TABLES


def _legacy_company(conn, name: str, *, with_people_department: bool = False) -> dict[str, str]:
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    ids = {"company": str(uuid.uuid4()), "founder": str(uuid.uuid4()), "worker": str(uuid.uuid4())}
    conn.execute(
        text(
            "INSERT INTO companies (id, name, slug, constraints, timezone, status, created_at, updated_at) "
            "VALUES (:id, :n, :s, '[]', 'UTC', 'active', :t, :t)"
        ),
        {"id": ids["company"], "n": name, "s": name.lower(), "t": now},
    )
    for key, kind, agent_name in (
        ("founder", "founder", "Atlas"),
        ("worker", "specialist", "Nova"),
    ):
        conn.execute(
            text(
                "INSERT INTO agents (id, company_id, name, role_title, kind, status, reports_to_human, "
                "runtime, runtime_config, tool_permissions, model_provider, model_name, status_changed_at, "
                "created_at, updated_at) VALUES (:id, :c, :n, 'Role', :k, 'active', 0, 'scripted', '{}', "
                "'[]', 'ollama', 'qwen2.5:7b', :t, :t, :t)"
            ),
            {"id": ids[key], "c": ids["company"], "n": agent_name, "k": kind, "t": now},
        )
    if with_people_department:
        conn.execute(
            text(
                "INSERT INTO departments (id, company_id, name, created_at, updated_at) "
                "VALUES (:id, :c, 'People', :t, :t)"
            ),
            {"id": str(uuid.uuid4()), "c": ids["company"], "t": now},
        )
    return ids


def test_0003_backfills_an_hr_agent_people_department_and_seed_job(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy_m4.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    engine = create_engine(url)
    with engine.begin() as conn:
        plain = _legacy_company(conn, "Plain")
        has_people = _legacy_company(conn, "HasPeople", with_people_department=True)
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        for ids in (plain, has_people):
            rows = conn.execute(
                text(
                    "SELECT id, name, kind, status, role_template, role_family, manager_agent_id, "
                    "department_id, runtime, model_provider, model_name, tool_permissions, instructions "
                    "FROM agents WHERE company_id = :c ORDER BY name"
                ),
                {"c": ids["company"]},
            ).all()
            by_name = {r[1]: r for r in rows}
            assert set(by_name) == {"Atlas", "Harper", "Nova"}
            # role_family inferred for existing agents
            assert (by_name["Atlas"][4], by_name["Atlas"][5]) == ("founder-ceo", "founder")
            assert (by_name["Nova"][4], by_name["Nova"][5]) == (None, "generic")
            hr = by_name["Harper"]
            assert (hr[2], hr[3], hr[4], hr[5]) == ("specialist", "active", "hr-partner", "hr")
            assert hr[6] == ids["founder"]
            assert (hr[8], hr[9], hr[10]) == ("scripted", "ollama", "qwen2.5:7b")  # copied
            assert "company" in hr[11] and hr[12]
            people = conn.execute(
                text(
                    "SELECT id, lead_agent_id FROM departments WHERE company_id = :c AND name = 'People'"
                ),
                {"c": ids["company"]},
            ).all()
            assert len(people) == 1 and people[0][0] == hr[7] and people[0][1] == hr[0]
            jobs = conn.execute(
                text("SELECT kind, status, dedupe_key, payload FROM jobs WHERE company_id = :c"),
                {"c": ids["company"]},
            ).all()
            assert [(j[0], j[1], j[2]) for j in jobs] == [
                ("seed_skill_library", "queued", f"seed_skill_library:{ids['company']}")
            ]
            audit = conn.execute(
                text(
                    "SELECT action FROM audit_events WHERE company_id = :c AND action = 'agent.created'"
                ),
                {"c": ids["company"]},
            ).all()
            assert len(audit) == 1
        policy = conn.execute(
            text(
                "SELECT auto_activate_hires, hire_default_monthly_budget_micros FROM company_policies"
            )
        ).all()
        assert all(bool(p[0]) and p[1] is None for p in policy)
    engine.dispose()

    # a second pass (downgrade + upgrade) does not hire a second HR agent
    command.downgrade(cfg, "0002")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM agents WHERE company_id = :c AND name = 'Harper'"),
            {"c": plain["company"]},
        ).scalar()
    engine.dispose()
    assert n == 1
