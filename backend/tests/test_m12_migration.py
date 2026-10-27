"""M12 schema (migration 0011) and the new owner-only policy fields."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.core.principal import Principal, PrincipalType
from tests.conftest import alembic_config, human
from tests.helpers import api, new_agent
from tests.test_migrations import _legacy_company

POLICY_COLUMNS = {
    "work_model_provider_id",
    "work_model_name",
    "cli_role_families",
    "cli_run_reservation_micros",
    "management_reserved_slots",
    "max_parallel_runs_per_agent",
    "max_work_tasks_per_project",
    "max_subtasks_per_task",
    "circuit_breaker_cooldown_seconds",
    "initiative_enabled",
    "initiative_interval_seconds",
    "project_auto_complete",
}
DEFAULTS = {
    "work_model_provider_id": None,
    "work_model_name": None,
    "cli_role_families": None,
    "cli_run_reservation_micros": 1_000_000,
    "management_reserved_slots": 1,
    "max_parallel_runs_per_agent": 1,
    "max_work_tasks_per_project": 15,
    "max_subtasks_per_task": 5,
    "circuit_breaker_cooldown_seconds": 1800,
    "initiative_enabled": True,
    "initiative_interval_seconds": 21_600,
    "project_auto_complete": True,
}


def _insert_legacy_rows(conn, company_id: str, worker_id: str) -> str:
    """A policy, a task and a run as they exist before 0011; returns the task id."""
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    conn.execute(
        text(
            "INSERT INTO company_policies (id, company_id, autonomy_mode, human_approval_required_for, "
            "max_concurrent_runs, max_delegation_depth, max_tasks_per_planning_cycle, max_attempts_per_task, "
            "max_revisions, max_run_seconds, max_headcount, heartbeat_interval_seconds, "
            "default_run_reservation_micros, allowed_model_providers, auto_activate_hires, "
            "max_agent_monthly_budget_micros, max_planning_runs_per_hour, max_open_tasks, "
            "circuit_breaker_failures, sandbox_backend, budget_alert_thresholds, model_routing, "
            "allow_paid_fallback, auto_spend_limit_micros, stall_seconds, max_tool_calls_per_run, "
            "max_artifacts_per_run, max_tokens_per_run, ai_disclosure_enabled, work_mode, "
            "budget_lock_version, created_at, updated_at) VALUES "
            "(:i, :c, 'autonomous', '[]', 2, 3, 10, 3, 2, 900, 12, 900, 50000, '[]', 1, 25000000, 6, 200, 8, "
            "'auto', '[50,80,100]', 'free_first', 1, 0, 180, 300, 50, 3000000, 1, 'api', 0, :t, :t)"
        ),
        {"i": str(uuid.uuid4()), "c": company_id, "t": now},
    )
    task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO tasks (id, company_id, title, kind, status, priority, assignee_agent_id, "
            "required_skills, attempt_count, revision_count, delegation_depth, input_refs, output_refs, "
            "created_by_type, created_at, updated_at) VALUES "
            "(:i, :c, 'Old task', 'work', 'ready', 'normal', :a, '[]', 1, 0, 0, '[]', '[]', 'human', :t, :t)"
        ),
        {"i": task_id, "c": company_id, "a": worker_id, "t": now},
    )
    conn.execute(
        text(
            "INSERT INTO task_runs (id, company_id, task_id, agent_id, attempt_number, status, runtime, "
            "idempotency_key, queued_at, cancel_requested, cost_kind, created_at, updated_at) VALUES "
            "(:i, :c, :task, :a, 1, 'succeeded', 'scripted', :k, :t, 0, 'unknown', :t, :t)"
        ),
        {"i": run_id, "c": company_id, "task": task_id, "a": worker_id, "k": run_id, "t": now},
    )
    return task_id


def test_0011_defaults_existing_rows_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm12.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0010")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        _insert_legacy_rows(conn, ids["company"], ids["worker"])
    assert not POLICY_COLUMNS & {c["name"] for c in inspect(engine).get_columns("company_policies")}

    command.upgrade(cfg, "0011")
    with engine.begin() as conn:
        row = (
            conn.execute(text(f"SELECT {', '.join(sorted(POLICY_COLUMNS))} FROM company_policies"))
            .mappings()
            .one()
        )
        for name, expected in DEFAULTS.items():
            got = row[name]
            if name == "cli_role_families":
                assert got is None, name
            elif isinstance(expected, bool):
                assert bool(got) is expected, name
            else:
                assert got == expected, name
        task = conn.execute(
            text("SELECT next_attempt_at, transient_failures, agent_retry_count FROM tasks")
        ).one()
        assert tuple(task) == (None, 0, 0)
        assert conn.execute(text("SELECT workspace_ref FROM task_runs")).scalar() is None
        assert conn.execute(text("SELECT count(*) FROM autonomy_interventions")).scalar() == 0
        # the table accepts a row and enforces uniqueness per (company, kind, subject)
        now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
        params = {"c": ids["company"], "t": now, "d": json.dumps({"fp": "x"})}
        insert = text(
            "INSERT INTO autonomy_interventions (id, company_id, kind, subject_type, subject_id, "
            "count, last_at, data, created_at) VALUES (:i, :c, 'review_stall', 'task', 's1', 1, :t, :d, :t)"
        )
        conn.execute(insert, {**params, "i": str(uuid.uuid4())})
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(insert, {**params, "i": str(uuid.uuid4())})
    index_names = {i["name"] for i in inspect(engine).get_indexes("tasks")}
    assert "ix_tasks_company_status_next_attempt" in index_names

    command.downgrade(cfg, "0010")
    insp = inspect(engine)
    assert "autonomy_interventions" not in insp.get_table_names()
    assert not POLICY_COLUMNS & {c["name"] for c in insp.get_columns("company_policies")}
    assert not {"next_attempt_at", "transient_failures", "agent_retry_count"} & {
        c["name"] for c in insp.get_columns("tasks")
    }
    assert "workspace_ref" not in {c["name"] for c in insp.get_columns("task_runs")}
    assert "ix_tasks_company_status_next_attempt" not in {
        i["name"] for i in insp.get_indexes("tasks")
    }
    with engine.connect() as conn:  # the legacy rows survive the round trip
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar() == 1
    command.upgrade(cfg, "head")
    engine.dispose()


def test_new_companies_get_the_documented_defaults(client, company):
    policy = client.get(api(company["id"], "/policy")).json()
    for name, expected in DEFAULTS.items():
        assert policy[name] == expected, name


def _audit(client, cid):
    return client.get(api(cid, "/activity"), params={"target_type": "policy"}).json()["items"]


def test_owner_changes_the_m12_fields_and_it_is_audited(client, company):
    cid = company["id"]
    changes = {
        "cli_role_families": ["engineering", "qa"],
        "cli_run_reservation_micros": 2_000_000,
        "management_reserved_slots": 2,
        "max_concurrent_runs": 4,
        "max_parallel_runs_per_agent": 3,
        "max_work_tasks_per_project": 30,
        "max_subtasks_per_task": 8,
        "circuit_breaker_cooldown_seconds": 0,
        "initiative_enabled": False,
        "initiative_interval_seconds": 3600,
        "project_auto_complete": False,
    }
    r = client.patch(api(cid, "/policy"), json=changes)
    assert r.status_code == 200, r.text
    body = r.json()
    for name, value in changes.items():
        assert body[name] == value, name
    assert client.get(api(cid, "/policy")).json()["cli_role_families"] == ["engineering", "qa"]
    audit = _audit(client, cid)
    assert audit[0]["action"] == "policy.updated"
    assert set(audit[0]["data"]["changed"]) == set(changes)
    # a no-op patch writes nothing; null clears the role-family scope back to "all"
    client.patch(api(cid, "/policy"), json={"max_subtasks_per_task": 8})
    assert len(_audit(client, cid)) == 1
    r = client.patch(api(cid, "/policy"), json={"cli_role_families": None})
    assert r.status_code == 200 and r.json()["cli_role_families"] is None
    assert len(_audit(client, cid)) == 2
    r = client.patch(api(cid, "/policy"), json={"cli_role_families": []})
    assert r.json()["cli_role_families"] == []  # empty = no role family uses the CLI


def test_invalid_m12_values_are_rejected(client, company):
    cid = company["id"]
    for bad in (
        {"cli_role_families": ["wizard"]},
        {"cli_role_families": "engineering"},
        {"cli_run_reservation_micros": -1},
        {"cli_run_reservation_micros": None},
        {"management_reserved_slots": -1},
        {"management_reserved_slots": 100},
        {"max_parallel_runs_per_agent": 0},
        {"max_parallel_runs_per_agent": 11},
        {"max_work_tasks_per_project": 0},
        {"max_work_tasks_per_project": 201},
        {"max_subtasks_per_task": 0},
        {"max_subtasks_per_task": 51},
        {"circuit_breaker_cooldown_seconds": -5},
        {"circuit_breaker_cooldown_seconds": 10**9},
        {"initiative_enabled": None},
        {"initiative_interval_seconds": 59},
        {"project_auto_complete": None},
        {"work_model_provider_id": "p1"},  # a provider without a model name
        {"work_model_name": "gpt-x"},  # a model name without a provider
        {"management_reserved_slots": 2},  # not lower than max_concurrent_runs (2)
        {"management_reserved_slots": 3, "max_concurrent_runs": 3},
    ):
        assert client.patch(api(cid, "/policy"), json=bad).status_code == 422, bad
    policy = client.get(api(cid, "/policy")).json()
    for name, expected in DEFAULTS.items():
        assert policy[name] == expected, name
    assert _audit(client, cid) == []
    # lowering max_concurrent_runs alone is never refused
    assert client.patch(api(cid, "/policy"), json={"max_concurrent_runs": 1}).status_code == 200


def test_work_model_must_be_an_enabled_provider_of_this_company(client, company):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    body = {"name": "Local Ollama", "kind": "ollama", "default_model": "qwen2.5:7b"}
    mine = client.post(api(cid, "/model-providers"), json=body).json()
    theirs = client.post(api(other["id"], "/model-providers"), json=body).json()
    r = client.patch(
        api(cid, "/policy"), json={"work_model_provider_id": theirs["id"], "work_model_name": "m"}
    )
    assert r.status_code == 404
    r = client.patch(
        api(cid, "/policy"),
        json={"work_model_provider_id": str(uuid.uuid4()), "work_model_name": "m"},
    )
    assert r.status_code == 404
    r = client.patch(
        api(cid, "/policy"),
        json={"work_model_provider_id": mine["id"], "work_model_name": "qwen2.5:7b"},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["work_model_provider_id"], r.json()["work_model_name"]) == (
        mine["id"],
        "qwen2.5:7b",
    )
    assert _audit(client, cid)[0]["data"]["changed"] == [
        "work_model_provider_id",
        "work_model_name",
    ]
    # clearing needs both to go together
    r = client.patch(api(cid, "/policy"), json={"work_model_name": None})
    assert r.status_code == 422
    r = client.patch(
        api(cid, "/policy"), json={"work_model_provider_id": None, "work_model_name": None}
    )
    assert r.status_code == 200 and r.json()["work_model_provider_id"] is None


def test_agents_and_viewers_cannot_change_the_m12_fields(
    client, client_as, make_user, add_member, company
):
    cid = company["id"]
    attempts = (
        {"max_work_tasks_per_project": 200},
        {"circuit_breaker_cooldown_seconds": 0},
        {"initiative_enabled": False},
        {"cli_role_families": ["qa"]},
        {"management_reserved_slots": 0},
    )
    agent = new_agent(client, cid)
    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid))
    viewer = make_user("viewer-m12@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    for who in (ac, vc):
        for body in attempts:
            assert who.patch(api(cid, "/policy"), json=body).status_code == 403, body
        assert who.get(api(cid, "/policy")).json()["max_work_tasks_per_project"] == 15
    assert _audit(client, cid) == []


def test_0012_adds_the_digest_to_existing_standup_and_weekly_review(tmp_path):
    url = f"sqlite:///{tmp_path / 'm12-digest.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0011")
    engine = create_engine(url)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Digest")
        conn.execute(text("DELETE FROM schedules"))
        rows = {
            "daily-standup": (ids["founder"], {"title": "Standup"}),
            "weekly-strategy-review": (ids["founder"], {"title": "Weekly", "digest": False}),
            "daily-budget-review": (ids["founder"], {"title": "Budget"}),
            "worker-standup": (ids["worker"], {"title": "Not the Co-founder"}),
        }
        for name, (agent_id, template) in rows.items():
            conn.execute(
                text(
                    "INSERT INTO schedules (id, company_id, name, kind, agent_id, interval_seconds, "
                    "timezone, task_template, enabled, created_at, updated_at) VALUES "
                    "(:i, :c, :n, 'interval', :a, 3600, 'UTC', :tt, 1, :t, :t)"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "c": ids["company"],
                    "n": name if name != "worker-standup" else "daily-standup",
                    "a": agent_id,
                    "tt": json.dumps(template),
                    "t": now,
                },
            )

    def templates():
        with engine.connect() as conn:
            return sorted(
                (name, agent_id == ids["founder"], json.loads(tt))
                for name, agent_id, tt in conn.execute(
                    text("SELECT name, agent_id, task_template FROM schedules")
                )
            )

    command.upgrade(cfg, "0012")
    assert templates() == [
        ("daily-budget-review", True, {"title": "Budget"}),
        ("daily-standup", False, {"title": "Not the Co-founder"}),
        ("daily-standup", True, {"title": "Standup", "digest": True}),
        ("weekly-strategy-review", True, {"title": "Weekly", "digest": False}),  # owner's choice
    ]
    command.downgrade(cfg, "0011")
    assert ("daily-standup", True, {"title": "Standup"}) in templates()
