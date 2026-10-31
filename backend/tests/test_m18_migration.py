"""M18 Part A schema (migration 0018): approval deadlines, the two policy timeouts, founder questions,
and the retry-memory columns of tasks. Existing rows keep working and get the documented defaults."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

POLICY_COLUMNS = {"approval_timeout_seconds", "agent_approval_timeout_seconds"}
TASK_COLUMNS = {"last_attempt", "replans"}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_0018_adds_the_columns_and_the_questions_table_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm18.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0017")
    engine = create_engine(url)
    approval_id = str(uuid.uuid4())
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        task_id = _insert_legacy_rows(conn, ids["company"], ids["worker"])
        conn.execute(
            text(
                "INSERT INTO approvals (id, company_id, kind, status, approver, title, risk, "
                "evidence, scope, scope_hash, created_at, updated_at) VALUES "
                "(:i, :c, 'hire', 'pending', 'founder', 'Old approval', 'low', '{}', '{}', "
                "'h', :t, :t)"
            ),
            {"i": approval_id, "c": ids["company"], "t": _now()},
        )
    assert "founder_questions" not in inspect(engine).get_table_names()
    assert "expires_at" not in _columns(engine, "approvals")

    command.upgrade(cfg, "0018")
    assert "expires_at" in _columns(engine, "approvals")
    assert _columns(engine, "company_policies") >= POLICY_COLUMNS
    assert _columns(engine, "tasks") >= TASK_COLUMNS
    with engine.begin() as conn:
        # a pending approval from before has no deadline: the people who see it decide it
        assert (
            conn.execute(
                text("SELECT expires_at FROM approvals WHERE id = :i"), {"i": approval_id}
            ).scalar()
            is None
        )
        policy = conn.execute(
            text(
                "SELECT approval_timeout_seconds, agent_approval_timeout_seconds "
                "FROM company_policies WHERE company_id = :c"
            ),
            {"c": ids["company"]},
        ).one()
        assert tuple(policy) == (86_400, 1_800)
        task = conn.execute(
            text("SELECT replans, last_attempt FROM tasks WHERE id = :i"), {"i": task_id}
        ).one()
        assert tuple(task) == (0, None)
        # a question can be stored; open + blocking default to the documented values
        qid = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO founder_questions (id, company_id, agent_id, question, options, "
                "default_answer, status, deadline, created_at, updated_at) VALUES "
                "(:i, :c, :a, 'Q?', '[]', 'd', 'open', :t, :t, :t)"
            ),
            {"i": qid, "c": ids["company"], "a": ids["worker"], "t": _now()},
        )
        assert (
            conn.execute(
                text("SELECT blocking FROM founder_questions WHERE id = :i"), {"i": qid}
            ).scalar()
            == 0
        )
    indexes = {i["name"] for i in inspect(engine).get_indexes("founder_questions")}
    assert {
        "ix_founder_questions_company_id",
        "ix_founder_questions_company_status",
        "ix_founder_questions_status_deadline",
        "ix_founder_questions_task_id",
    } <= indexes

    command.downgrade(cfg, "0017")
    assert "founder_questions" not in inspect(engine).get_table_names()
    assert "expires_at" not in _columns(engine, "approvals")
    assert not POLICY_COLUMNS & _columns(engine, "company_policies")
    assert not TASK_COLUMNS & _columns(engine, "tasks")
    command.upgrade(cfg, "0018")  # and up again
    assert "founder_questions" in inspect(engine).get_table_names()
    engine.dispose()
