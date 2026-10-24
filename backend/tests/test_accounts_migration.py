"""M7 on an existing install: the placeholder owner (owner@localhost, no password) who already owns
a company is CLAIMED by the first-run setup -- same user id, same memberships, same company."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from alembic import command
from app.core.config import Settings
from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from tests.accounts_helpers import PASSWORD, setup_body
from tests.conftest import alembic_config


def legacy_database(tmp_path: Path) -> tuple[str, dict]:
    """A database as M6 left it: the owner placeholder, one company, an owner membership."""
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0006")
    ids = {
        k: str(uuid.uuid4()) for k in ("user", "company", "member", "other_user", "other_member")
    }
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, created_at, updated_at) "
                "VALUES (:id, 'owner@localhost', 'Owner', :t, :t)"
            ),
            {"id": ids["user"], "t": now},
        )
        conn.execute(
            text(
                "INSERT INTO companies (id, name, slug, constraints, timezone, status, created_at, updated_at) "
                "VALUES (:id, 'Legacy Co', 'legacy-co', '[]', 'UTC', 'active', :t, :t)"
            ),
            {"id": ids["company"], "t": now},
        )
        conn.execute(
            text(
                "INSERT INTO company_members (id, company_id, user_id, role, created_at) "
                "VALUES (:id, :c, :u, 'owner', :t)"
            ),
            {"id": ids["member"], "c": ids["company"], "u": ids["user"], "t": now},
        )
    engine.dispose()
    command.upgrade(cfg, "head")
    return url, ids


def app_for(url: str, tmp_path: Path):
    settings = Settings(
        _env_file=None,
        database_url=url,
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "no-runtime",
        argon2_time_cost=1,
        argon2_memory_kib=64,
        argon2_parallelism=1,
    )
    return create_app(settings, runtime_registry=RuntimeRegistry())


def test_migration_flags_the_placeholder_owner_and_keeps_members_active(tmp_path):
    url, ids = legacy_database(tmp_path)
    engine = create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT is_local_owner, username, password_hash, email FROM users WHERE id = :i"),
            {"i": ids["user"]},
        ).one()
        assert (row[0], row[1], row[2], row[3]) == (1, None, None, "owner@localhost")
        member = conn.execute(
            text("SELECT status, role, title, manager_ref FROM company_members WHERE id = :i"),
            {"i": ids["member"]},
        ).one()
        assert tuple(member) == ("active", "owner", None, None)
    engine.dispose()


def test_setup_claims_the_existing_owner_instead_of_creating_a_user(tmp_path):
    url, ids = legacy_database(tmp_path)
    app = app_for(url, tmp_path)
    token = None
    with TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 5)) as here:
        token = app.state.owner_token.get()
        # the install reports that it still needs an account ...
        s = here.get("/api/auth/session").json()
        assert s["state"] == "setup_required" and s["user"] is None
        # ... while the owner credential keeps working exactly as before
        owner = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        assert [c["id"] for c in owner.get("/api/companies").json()["items"]] == [ids["company"]]

        r = here.post("/api/auth/setup", json={**setup_body(app), "setup_token": None})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "authenticated"
        assert body["user"]["id"] == ids["user"]  # claimed, not created
        assert body["user"]["username"] == "alice"

        me = here.get("/api/me").json()
        assert me["user"]["id"] == ids["user"] and me["user"]["username"] == "alice"
        assert me["memberships"] == [
            {
                "company_id": ids["company"],
                "company_name": "Legacy Co",
                "role": "owner",
                "member_id": ids["member"],
            }
        ]
        assert [c["id"] for c in here.get("/api/companies").json()["items"]] == [ids["company"]]

    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM company_members")).scalar() == 1
        email = conn.execute(text("SELECT email FROM users")).scalar()
        assert email is None  # the placeholder address is gone; none was given
    engine.dispose()

    # the owner token / CLI now acts as the claimed account; a fresh login works
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        assert c.get("/api/me").json()["user"]["username"] == "alice"
        fresh = TestClient(app, client=("10.0.0.5", 1))
        fresh.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert fresh.get("/api/me").json()["memberships"][0]["company_id"] == ids["company"]
        assert fresh.get("/api/auth/session").json()["state"] == "authenticated"


def test_a_restart_after_the_claim_does_not_recreate_the_placeholder(tmp_path):
    url, ids = legacy_database(tmp_path)
    app = app_for(url, tmp_path)
    with TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 5)) as here:
        body = {k: v for k, v in setup_body(app).items() if k != "setup_token"}
        assert here.post("/api/auth/setup", json=body).status_code == 200
    app2 = app_for(url, tmp_path)  # restart
    with TestClient(app2, base_url="http://localhost:8700", client=("127.0.0.1", 5)) as c:
        assert c.get("/api/auth/session").json()["state"] == "unauthenticated"
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
    engine.dispose()
