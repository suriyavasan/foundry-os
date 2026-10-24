from __future__ import annotations

import logging

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401
from app.core.config import BACKEND_DIR, Settings, resolve_database_url
from app.db.base import Base
from app.db.session import build_engine
from app.main import create_app


def test_migrations_create_expected_schema_and_match_models(db_url):
    engine = create_engine(db_url)
    names = set(inspect(engine).get_table_names())
    assert {
        "users",
        "companies",
        "company_members",
        "departments",
        "agents",
        "audit_events",
    } <= names
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models drifted from migrations: {diff}"
    idx = {i["name"] for i in inspect(engine).get_indexes("agents")}
    assert {"ix_agents_company_id", "uq_agents_one_live_founder"} <= idx
    engine.dispose()


def test_startup_does_not_create_tables(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'empty.db'}")
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="alembic upgrade head"), TestClient(app):
        pass
    assert inspect(create_engine(settings.database_url)).get_table_names() == []


def test_sqlite_pragmas(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    engine.dispose()


def test_default_database_url_resolves_relative_to_backend():
    url = resolve_database_url("sqlite:///../data/company_os.db")
    assert url == f"sqlite:///{(BACKEND_DIR.parent / 'data' / 'company_os.db')}"
    assert resolve_database_url("sqlite:////abs/x.db") == "sqlite:////abs/x.db"
    assert resolve_database_url("postgresql://u@h/db") == "postgresql://u@h/db"
    assert Settings(_env_file=None).auth_mode == "password"  # M7: accounts are the default


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("HERMES_API_URL", "http://h:1")
    s = Settings(_env_file=None)
    assert s.database_url == "sqlite:///x.db" and s.hermes_api_url == "http://h:1"
    monkeypatch.setenv("COMPANY_OS_AUTH_MODE", "oidc")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_startup_creates_the_owner_token_and_never_logs_it(app, caplog):
    with caplog.at_level(logging.INFO, logger="company_os"), TestClient(app):
        pass
    token = app.state.owner_token.path.read_text().strip()
    assert len(token) >= 32
    assert token not in caplog.text  # the login URL goes to the terminal only, never to logs


def test_foreign_keys_enforced(app, client, company):
    from sqlalchemy.exc import IntegrityError

    from app.models.agent import Agent

    with app.state.session_factory() as db:
        db.add(Agent(company_id="no-such-company", name="x", role_title="y"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_db_unique_founder_index_backstop(app, client, company):
    from sqlalchemy.exc import IntegrityError

    from app.models.agent import Agent

    with app.state.session_factory() as db:
        db.add(
            Agent(
                company_id=company["id"], name="x", role_title="y", kind="founder", status="active"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_startup_refuses_a_database_behind_the_latest_migration(tmp_path):
    from alembic import command
    from tests.conftest import alembic_config

    url = f"sqlite:///{tmp_path / 'm1.db'}"
    command.upgrade(alembic_config(url), "0001")  # the Milestone 1 schema
    app = create_app(Settings(_env_file=None, database_url=url))
    with pytest.raises(RuntimeError, match="out of date.*alembic upgrade head"), TestClient(app):
        pass
