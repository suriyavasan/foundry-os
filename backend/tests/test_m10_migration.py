"""Migration 0009: tables, backfills (exchanges for existing notes, the daily-reflection routine),
and what an existing install looks like to the readiness gate afterwards."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.services import ai_setup
from tests.conftest import alembic_config
from tests.test_migrations import _legacy_company

NEW_TABLES = {"reflections", "reflection_responses", "company_standards", "command_exchanges"}


def test_0009_upgrades_an_existing_install_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm9.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0008")
    engine = create_engine(url)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        provider = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO model_providers (id, company_id, name, kind, base_url, default_model, "
                "context_length, is_local, enabled, last_test, created_at, updated_at) VALUES "
                "(:i, :c, 'Local Ollama', 'ollama', 'http://127.0.0.1:11434', 'qwen2.5:7b', 32768, 1, 1, "
                ":lt, :t, :t)"
            ),
            {
                "i": provider,
                "c": ids["company"],
                "t": now,
                "lt": json.dumps(
                    {
                        "status": "ok",
                        "message": "old",
                        "checked_at": "2026-10-20T00:00:00Z",
                        "tool_calling": True,
                        "warnings": [],
                    }
                ),
            },
        )
        conn.execute(
            text(
                "UPDATE company_policies SET default_model_provider_id = :p, default_model_name = 'qwen2.5:7b'"
            ),
            {"p": provider},
        ) if conn.execute(text("SELECT count(*) FROM company_policies")).scalar() else conn.execute(
            text(
                "INSERT INTO company_policies (id, company_id, autonomy_mode, human_approval_required_for, "
                "max_concurrent_runs, max_delegation_depth, max_tasks_per_planning_cycle, max_attempts_per_task, "
                "max_revisions, max_run_seconds, max_headcount, heartbeat_interval_seconds, "
                "default_run_reservation_micros, allowed_model_providers, auto_activate_hires, "
                "max_agent_monthly_budget_micros, max_planning_runs_per_hour, max_open_tasks, "
                "circuit_breaker_failures, default_model_provider_id, default_model_name, sandbox_backend, "
                "budget_alert_thresholds, model_routing, allow_paid_fallback, auto_spend_limit_micros, "
                "stall_seconds, max_tool_calls_per_run, max_artifacts_per_run, max_tokens_per_run, "
                "ai_disclosure_enabled, budget_lock_version, created_at, updated_at) VALUES "
                "(:i, :c, 'autonomous', '[]', 2, 3, 10, 3, 2, 900, 12, 900, 50000, '[]', 1, 25000000, 6, 200, 8, "
                ":p, 'qwen2.5:7b', 'auto', '[50,80,100]', 'free_first', 1, 0, 180, 300, 50, 3000000, 1, 0, :t, :t)"
            ),
            {"i": str(uuid.uuid4()), "c": ids["company"], "p": provider, "t": now},
        )
        for status, triage in (
            ("new", None),
            (
                "triaged",
                json.dumps(
                    {
                        "category": "idea",
                        "summary": "s",
                        "actions": [{"type": "goal", "ref_id": "g1", "label": "Goal"}],
                        "confidence": 0.9,
                    }
                ),
            ),
            ("archived", None),
        ):
            conn.execute(
                text(
                    "INSERT INTO founder_notes (id, company_id, body, attachments, status, triage, pinned, "
                    "created_at, updated_at) VALUES (:i, :c, :b, '[]', :s, :tr, 0, :t, :t)"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "c": ids["company"],
                    "b": f"note {status}",
                    "s": status,
                    "tr": triage,
                    "t": now,
                },
            )
    assert not NEW_TABLES & set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0009")
    assert set(inspect(engine).get_table_names()) >= NEW_TABLES
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT input_kind, status, input, actions FROM command_exchanges ORDER BY status")
        ).all()
        assert sorted((r[0], r[1]) for r in rows) == [
            ("note", "answered"),
            ("note", "answered"),
            ("note", "pending"),
        ]
        assert all(r[2] is None for r in rows)  # notes stay the storage of the text
        answered_with_chip = [r for r in rows if r[1] == "answered" and json.loads(r[3])]
        assert len(answered_with_chip) == 1
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM schedules WHERE company_id = :c"), {"c": ids["company"]}
            )
        }
        assert "daily-reflection" in names
        reflection = conn.execute(
            text(
                "SELECT agent_id, cron, enabled, next_run_at FROM schedules WHERE name = 'daily-reflection'"
            )
        ).one()
        assert (
            reflection[0] == ids["founder"]
            and reflection[1] == "0 20 * * *"
            and reflection[2] in (1, True)
        )
        assert reflection[3] is not None
        pol = conn.execute(text("SELECT work_mode, work_runtime FROM company_policies")).one()
        assert tuple(pol) == ("api", None)
        audit = conn.execute(
            text("SELECT summary FROM audit_events WHERE summary LIKE '%daily-reflection%'")
        ).all()
        assert audit

    # the existing install is NOT ready: its provider was never run through the M10 checks
    # (the ORM reads the current schema, so bring the database to head first)
    command.upgrade(cfg, "head")
    with sessionmaker(engine)() as db:
        state = ai_setup.evaluate(db, ids["company"])
        assert state.ready is False and state.blocking_reason == "no_tested_ai"

    # idempotent re-run of the data steps, then a clean round trip
    command.downgrade(cfg, "0008")
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM schedules WHERE name = 'daily-reflection'")
            ).scalar()
            == 0
        )
        assert (
            conn.execute(text("SELECT count(*) FROM founder_notes")).scalar() == 3
        )  # data untouched
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= NEW_TABLES
    engine.dispose()
