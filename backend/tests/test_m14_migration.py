"""M14 schema (migration 0013) and the owner-only ``new_project_repository`` policy field."""

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
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

M14_TABLES = {"project_repositories", "repo_pull_requests", "repo_items"}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _insert_project(conn, company_id: str) -> str:
    project_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO projects (id, company_id, name, status, created_by_type, created_at, "
            "updated_at) VALUES (:i, :c, 'Legacy project', 'active', 'human', :t, :t)"
        ),
        {"i": project_id, "c": company_id, "t": _now()},
    )
    return project_id


def _insert_repo(conn, company_id: str, project_id: str, **overrides) -> str:
    repo_id = str(uuid.uuid4())
    values = {
        "i": repo_id,
        "c": company_id,
        "p": project_id,
        "t": _now(),
        "pol": json.dumps({}),
        **overrides,
    }
    conn.execute(
        text(
            "INSERT INTO project_repositories (id, company_id, project_id, owner, name, full_name, "
            "remote_url, default_branch, work_branch, policy, capabilities, protection, etags, "
            "created_at, updated_at) VALUES (:i, :c, :p, 'acme', 'app', 'acme/app', "
            "'https://github.com/acme/app.git', 'main', 'foundry/app', :pol, '{}', '{}', '{}', "
            ":t, :t)"
        ),
        values,
    )
    return repo_id


def test_0013_adds_the_tables_defaults_constraints_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm14.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0012")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        _insert_legacy_rows(conn, ids["company"], ids["worker"])  # a policy row and a task
        project_id = _insert_project(conn, ids["company"])
    insp = inspect(engine)
    assert not M14_TABLES & set(insp.get_table_names())
    assert "new_project_repository" not in {c["name"] for c in insp.get_columns("company_policies")}

    command.upgrade(cfg, "0013")
    insp = inspect(engine)
    assert set(insp.get_table_names()) >= M14_TABLES
    with engine.begin() as conn:
        # the existing policy row is valid at once: no backfill, the documented default
        assert (
            conn.execute(text("SELECT new_project_repository FROM company_policies")).scalar()
            == "local"
        )
        repo_id = _insert_repo(conn, ids["company"], project_id)
        row = conn.execute(
            text(
                "SELECT provider, auth_kind, visibility, origin, status, sync_state, sync_failures, "
                "onboarding_status, is_company_default, pr_opening_paused, next_sync_at, "
                "webhook_secret_credential_id FROM project_repositories WHERE id = :i"
            ),
            {"i": repo_id},
        ).one()
        assert tuple(row) == (
            "github",
            "pat",
            "private",
            "connected",
            "pending",
            "idle",
            0,
            "not_needed",
            0,
            0,
            None,
            None,
        )
        # one repository per project
        with pytest.raises(Exception, match="UNIQUE"):
            _insert_repo(conn, ids["company"], project_id)
        now = _now()
        pr = text(
            "INSERT INTO repo_pull_requests (id, company_id, repository_id, number, title, "
            "head_branch, base_branch, checks, task_ids, created_at, updated_at) VALUES "
            "(:i, :c, :r, :n, 'T', 'foundry/app', 'main', '[]', '[]', :t, :t)"
        )
        conn.execute(
            pr, {"i": str(uuid.uuid4()), "c": ids["company"], "r": repo_id, "n": 7, "t": now}
        )
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(
                pr, {"i": str(uuid.uuid4()), "c": ids["company"], "r": repo_id, "n": 7, "t": now}
            )
        defaults = conn.execute(
            text("SELECT stage, revision, checks_state, ci_fix_attempts FROM repo_pull_requests")
        ).one()
        assert tuple(defaults) == ("opening", 0, "unknown", 0)
        item = text(
            "INSERT INTO repo_items (id, company_id, repository_id, kind, external_id, "
            "injection_flags, data, created_at, updated_at) VALUES "
            "(:i, :c, :r, 'issue', '12', '[]', '{}', :t, :t)"
        )
        conn.execute(item, {"i": str(uuid.uuid4()), "c": ids["company"], "r": repo_id, "t": now})
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(
                item, {"i": str(uuid.uuid4()), "c": ids["company"], "r": repo_id, "t": now}
            )
        assert conn.execute(text("SELECT status FROM repo_items")).scalar() == "new"
    index_names = {i["name"] for t in M14_TABLES for i in insp.get_indexes(t)}
    assert {
        "ix_project_repositories_due",
        "ix_project_repositories_company_full_name",
        "ix_repo_pull_requests_repo_stage",
        "ix_repo_items_repo_status",
    } <= index_names

    command.downgrade(cfg, "0012")
    insp = inspect(engine)
    assert not M14_TABLES & set(insp.get_table_names())
    assert "new_project_repository" not in {c["name"] for c in insp.get_columns("company_policies")}
    with engine.connect() as conn:  # the legacy rows survive the round trip
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM projects")).scalar() == 1
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= M14_TABLES
    engine.dispose()


def test_0013_follows_0012():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    revision = script.get_revision("0013")
    assert revision.down_revision == "0012"


def _audit(client, cid):
    return client.get(api(cid, "/activity"), params={"target_type": "policy"}).json()["items"]


def test_new_companies_default_to_local_projects(client, company):
    assert client.get(api(company["id"], "/policy")).json()["new_project_repository"] == "local"


def test_the_owner_changes_new_project_repository_and_it_is_audited(client, company):
    cid = company["id"]
    r = client.patch(api(cid, "/policy"), json={"new_project_repository": "company_repo"})
    assert r.status_code == 200, r.text
    assert r.json()["new_project_repository"] == "company_repo"
    assert client.get(api(cid, "/policy")).json()["new_project_repository"] == "company_repo"
    audit = _audit(client, cid)
    assert audit[0]["action"] == "policy.updated"
    assert audit[0]["data"]["changed"] == ["new_project_repository"]
    # a no-op patch writes nothing
    client.patch(api(cid, "/policy"), json={"new_project_repository": "company_repo"})
    assert len(_audit(client, cid)) == 1
    assert (
        client.patch(api(cid, "/policy"), json={"new_project_repository": "local"}).status_code
        == 200
    )


def test_invalid_values_are_rejected(client, company):
    cid = company["id"]
    for bad in (
        {"new_project_repository": "everywhere"},
        {"new_project_repository": None},
        {"new_project_repository": 3},
    ):
        assert client.patch(api(cid, "/policy"), json=bad).status_code == 422, bad
    assert client.get(api(cid, "/policy")).json()["new_project_repository"] == "local"
    assert _audit(client, cid) == []


def test_agents_and_viewers_cannot_change_it(client, client_as, make_user, add_member, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid))
    viewer = make_user("viewer-m14@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    for who in (ac, vc):
        r = who.patch(api(cid, "/policy"), json={"new_project_repository": "company_repo"})
        assert r.status_code == 403
        assert who.get(api(cid, "/policy")).json()["new_project_repository"] == "local"
    assert _audit(client, cid) == []
