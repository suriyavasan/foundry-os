from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alembic import command
from app.api.deps import get_principal
from app.core.config import BACKEND_DIR, Settings
from app.core.principal import Principal, PrincipalType
from app.main import create_app
from app.models.company import CompanyMember
from app.models.user import User
from app.runtimes.registry import RuntimeRegistry


def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HERMES_API_URL", "HERMES_API_KEY", "COMPANY_OS_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database built by running the real Alembic migrations once per test session."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return path


@pytest.fixture
def db_url(migrated_template: Path, tmp_path: Path) -> str:
    target = tmp_path / "test.db"
    shutil.copy(migrated_template, target)
    return f"sqlite:///{target}"


@pytest.fixture
def make_settings(db_url: str) -> Callable[..., Settings]:
    def _make(**overrides) -> Settings:
        return Settings(_env_file=None, database_url=db_url, **overrides)

    return _make


@pytest.fixture
def app(make_settings: Callable[..., Settings]) -> FastAPI:
    return create_app(make_settings(), runtime_registry=RuntimeRegistry())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Acts as the local owner (default local auth)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(app: FastAPI, client: TestClient) -> Callable[[str], User]:
    def _make(email: str) -> User:
        with app.state.session_factory() as db:
            user = User(email=email, display_name=email.split("@")[0].title())
            db.add(user)
            db.commit()
            return user

    return _make


@pytest.fixture
def client_as(
    make_settings: Callable[..., Settings], client: TestClient
) -> Iterator[Callable[[Principal], TestClient]]:
    """Build a client for the SAME database that acts as an arbitrary principal."""
    opened: list[TestClient] = []

    def _build(principal: Principal) -> TestClient:
        other = create_app(make_settings(), runtime_registry=RuntimeRegistry())
        other.dependency_overrides[get_principal] = lambda: principal
        tc = TestClient(other)
        tc.__enter__()
        opened.append(tc)
        return tc

    yield _build
    for tc in opened:
        tc.__exit__(None, None, None)


def human(user: User) -> Principal:
    return Principal(type=PrincipalType.HUMAN, id=user.id, label=user.display_name)


@pytest.fixture
def add_member(app: FastAPI) -> Callable[[str, str, str], None]:
    def _add(company_id: str, user_id: str, role: str) -> None:
        with app.state.session_factory() as db:
            db.add(CompanyMember(company_id=company_id, user_id=user_id, role=role))
            db.commit()

    return _add


@pytest.fixture
def company(client: TestClient) -> dict:
    resp = client.post("/api/companies", json={"name": "Acme Labs", "mission": "Ship"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def agents_url(cid: str) -> str:
    return f"/api/companies/{cid}/agents"


def new_agent(client: TestClient, cid: str, **overrides) -> dict:
    body = {"name": "Nova", "role_title": "Researcher", **overrides}
    resp = client.post(agents_url(cid), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()
