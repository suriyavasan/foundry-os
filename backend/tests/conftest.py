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
from app.runtimes.scripted import ScriptedRuntime
from app.skills.env import SkillsEnv
from tests.helpers import FakeClock


def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HERMES_API_URL",
        "HERMES_API_KEY",
        "COMPANY_OS_DATABASE_URL",
        "COMPANY_OS_ENABLE_TEST_RUNTIMES",
        "COMPANY_OS_EMBEDDED_WORKER",
        "COMPANY_OS_SECRET_KEY",
        "COMPANY_OS_DATA_DIR",
        "COMPANY_OS_RUNTIME_DIR",
        "COMPANY_OS_RUNTIME_PYTHON",
        "COMPANY_OS_GITHUB_TOKEN",
    ):
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
def make_settings(db_url: str, tmp_path: Path) -> Callable[..., Settings]:
    def _make(**overrides) -> Settings:
        # No runtime checkout by default: no Hermes skills and no scanner, so nothing in the
        # general suite shells out. Skill tests pass their own runtime_dir / scanner.
        overrides.setdefault("runtime_dir", tmp_path / "no-runtime")
        return Settings(_env_file=None, database_url=db_url, **overrides)

    return _make


@pytest.fixture
def scripted() -> ScriptedRuntime:
    return ScriptedRuntime()


@pytest.fixture
def skills_env_override() -> SkillsEnv | None:
    """Skill-related test modules override this to inject a fake runtime / scanner / GitHub."""
    return None


@pytest.fixture
def app(
    make_settings: Callable[..., Settings],
    scripted: ScriptedRuntime,
    skills_env_override: SkillsEnv | None,
) -> FastAPI:
    return create_app(
        make_settings(),
        runtime_registry=RuntimeRegistry([scripted]),
        skills_env=skills_env_override,
    )


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


# ------------------------------------------------------------------ work engine fixtures


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine(app: FastAPI, make_settings: Callable[..., Settings], clock: FakeClock):
    from app.engine.context import build_context

    return build_context(
        make_settings(),
        session_factory=app.state.session_factory,
        registry=app.state.runtimes,
        keys=app.state.keys,
        storage=app.state.storage,
        clock=clock,
        skills=app.state.skills,
    )


@pytest.fixture
def scheduler(engine):
    from app.engine.scheduler import Scheduler

    return Scheduler(engine)


@pytest.fixture
def worker(engine):
    from app.engine.worker import Worker

    # Only run executions: the skill-library seed job (enqueued with every company) has its own tests.
    return Worker(engine, worker_id="test-worker", monitor_interval=0.01, kinds=("execute_run",))


# ------------------------------------------------------------------ skills & HR fixtures


@pytest.fixture
def fake_scanner():
    from tests.helpers import FakeScanner

    return FakeScanner()


@pytest.fixture
def fake_runtime(tmp_path: Path) -> Path:
    """A tiny runtime checkout holding a minimal skill for every Hermes skill the built-in role
    templates reference (half bundled, half optional)."""
    from app.roles import loader
    from tests.helpers import write_skill

    root = tmp_path / "fake-runtime"
    (root / "company_agent").mkdir(parents=True)
    (root / "company_agent" / "__init__.py").write_text(
        'UPSTREAM_COMMIT = "db1fa153fe24a051af44ef8ee862b93e27998060"\n'
    )
    company = set(loader.company_seed_slugs())
    for i, slug in enumerate(s for s in loader.seed_skill_slugs() if s not in company):
        sub = "skills" if i % 2 == 0 else "optional-skills"
        write_skill(root / sub / "general", slug, tags=[slug, "general"], category="general")
    return root


@pytest.fixture
def fake_skills_env(make_settings: Callable[..., Settings], fake_runtime: Path, fake_scanner):
    from app.skills.env import build_skills_env

    return build_skills_env(make_settings(runtime_dir=fake_runtime), scanner=fake_scanner)
