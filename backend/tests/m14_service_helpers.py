"""Helpers of the repository-service tests (M14 WP-C): the fixture ``m14`` (a ``FakeGitHub`` plus a
git remote wired into the app, a token that may create repositories, no real sleeping, a fixed
capability probe), account/repository builders and small database readers.

    from tests.m14_helpers import github_world  # noqa: F401  (a fixture)
    from tests.m14_service_helpers import m14  # noqa: F401  (a fixture)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.repository import ProjectRepository, RepoPullRequest
from app.models.run import Job
from app.models.work import Task
from tests.helpers import api
from tests.m8_helpers import connect
from tests.m14_helpers import LOGIN

ADMIN_TOKEN = (
    "github_pat_11ADMINTOKEN000_"
    + "q1W2e3R4t5Y6u7I8o9P0a1S2d3F4g5H6j7K8l9Z0x1C2v3B4n5M6q7W8e9R0t1Y2"
)
CAPABILITIES = {"contents": True, "pull_requests": True, "issues": True, "checks": True}


@pytest.fixture
def m14(app, github_world, monkeypatch):
    """The GitHub world of the repository tests (see ``tests/m14_helpers.py``)."""
    from app.services import github_accounts

    github_world.github.add_token(ADMIN_TOKEN, LOGIN, permissions={"administration": "write"})
    app.state.integrations.sleep = lambda seconds: None
    monkeypatch.setattr(
        github_accounts,
        "probe_capabilities",
        lambda client, full_name, mode="connect": dict(CAPABILITIES),
    )
    return github_world


def github_account(client, cid: str, world, *, token: str | None = None, label: str = "GitHub"):
    account = connect(client, cid, "github", label=label, secret=token or world.token)
    assert account["status"] == "connected", account
    return account


def post_repo(client, cid: str, body: dict[str, Any]):
    return client.post(api(cid, "/repositories"), json=body)


def connect_body(account: dict, full_name: str, **overrides) -> dict[str, Any]:
    return {
        "mode": "connect",
        "connected_account_id": account["id"],
        "project_name": "App",
        "full_name": full_name,
        **overrides,
    }


def create_body(account: dict, name: str, **overrides) -> dict[str, Any]:
    return {
        "mode": "create",
        "connected_account_id": account["id"],
        "project_name": "App",
        "name": name,
        **overrides,
    }


def new_project(client, cid: str, name: str = "Existing") -> dict:
    resp = client.post(api(cid, "/projects"), json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def repo_row(app, repository_id: str) -> SimpleNamespace:
    """A detached copy of the binding (attributes only)."""
    with app.state.session_factory() as db:
        row = db.get(ProjectRepository, repository_id)
        assert row is not None
        return SimpleNamespace(
            **{c.name: getattr(row, c.name) for c in ProjectRepository.__table__.columns}
        )


def set_repo(app, repository_id: str, **fields: Any) -> None:
    with app.state.session_factory() as db:
        row = db.get(ProjectRepository, repository_id)
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()


def audit_actions(app, cid: str, target_id: str | None = None, prefix: str = "repository.") -> list:
    with app.state.session_factory() as db:
        stmt = select(AuditEvent).where(
            AuditEvent.company_id == cid, AuditEvent.action.like(f"{prefix}%")
        )
        if target_id:
            stmt = stmt.where(AuditEvent.target_id == target_id)
        return list(db.scalars(stmt.order_by(AuditEvent.created_at, AuditEvent.id)))


def clone_jobs(app, repository_id: str) -> list[Job]:
    with app.state.session_factory() as db:
        return [
            j
            for j in db.scalars(select(Job).where(Job.kind == "repo_clone"))
            if (j.payload or {}).get("repository_id") == repository_id
        ]


def add_pull_request(
    app, cid: str, repository_id: str, *, number: int = 7, stage: str = "reviewing", **fields: Any
) -> str:
    values: dict[str, Any] = {
        "title": "Add the landing page",
        "head_branch": "foundry/app",
        "base_branch": "main",
        "head_sha": "a" * 40,
        "review_sha": "a" * 40,
        **fields,
    }
    with app.state.session_factory() as db:
        pr = RepoPullRequest(
            company_id=cid, repository_id=repository_id, number=number, stage=stage, **values
        )
        db.add(pr)
        db.commit()
        return pr.id


def task_by_key(app, cid: str, key: str) -> Task | None:
    with app.state.session_factory() as db:
        return db.scalar(select(Task).where(Task.company_id == cid, Task.idempotency_key == key))


def no_secret_anywhere(text: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in text
