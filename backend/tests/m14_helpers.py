"""Shared helpers for the M14 (GitHub-backed projects) tests.

``github_world`` wires a ``FakeGitHub`` (tests/m14_github_fake.py) and a local smart-HTTP
``GitRemote`` (tests/m14_git_remote.py) into the application: the GitHub API URL, the git web URL,
``github_allow_insecure_http`` (so git may talk to ``http://127.0.0.1``) and the HTTP transport of
the app's ``IntegrationContext``. Nothing reaches the real network. Test modules import what they
need from here (``conftest.py`` is deliberately not changed):

    from tests.m14_helpers import github_world  # noqa: F401  (a fixture)

``bound_project`` builds an ACTIVE repository binding directly in the database (and a local clone in
the M12 project directory), so the sync / PR tests do not depend on the onboarding code path.
"""

from __future__ import annotations

import re
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.base import IntegrationHttp
from app.integrations.github import GithubClient
from app.models.repository import ProjectRepository
from app.schemas.repository import default_policy
from app.services import project_workspaces as pw
from tests.helpers import api
from tests.m8_helpers import connect
from tests.m14_git_remote import GitRemote, git_env, run_git
from tests.m14_github_fake import FakeGitHub

API_URL = "https://api.github.test"
LOGIN = "foundry-bot"
# Shaped like a real fine-grained token, so the secret scanners recognise it.
TOKEN = (
    "github_pat_11TESTFOUNDRY0_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6a7B8c9D0e1"
)
OTHER_TOKEN = (
    "github_pat_11OTHERTOKEN00_"
    + "z9Y8x7W6v5U4t3S2r1Q0p9O8n7M6l5K4j3I2h1G0f9E8d7C6b5A4z3Y2x1W0v9U8"
)
READONLY_TOKEN = (
    "github_pat_11READONLY000_" + "m1N2b3V4c5X6z7L8k9J0h1G2f3D4s5A6p7O8i9U0y1T2r3E4w5Q6q7W8e9R0t1Y2"
)


@dataclass
class GithubWorld:
    github: FakeGitHub
    remote: GitRemote
    token: str = TOKEN
    login: str = LOGIN
    api_url: str = API_URL
    workspaces: Path | None = None
    http: IntegrationHttp = field(default_factory=IntegrationHttp)

    @property
    def web_url(self) -> str:
        return self.remote.url

    def client(self, token: str | None = None, **kwargs) -> GithubClient:
        """A ``GithubClient`` over the fake (the real client class, a mock transport)."""
        import httpx

        http = httpx.Client(transport=self.github.transport())
        kwargs.setdefault("sleep", lambda s: None)
        return GithubClient(
            http,
            token or self.token,
            base_url=self.api_url,
            web_url=self.web_url,
            **kwargs,
        )

    def wire_settings(self, settings) -> None:
        settings.github_api_url = self.api_url
        settings.github_web_url = self.web_url
        settings.github_allow_insecure_http = True

    def wire_engine(self, engine) -> None:
        """For the worker side: the engine's settings and the HTTP the sync loop will use."""
        self.wire_settings(engine.settings)
        engine.integration_http = self.http

    def add_repo(self, full_name: str, **kwargs):
        return self.github.add_repo(full_name, **kwargs)

    def human_commit(self, full_name: str, files: dict[str, str | None], **kwargs) -> str:
        return push_human_commit(self, full_name, files, **kwargs)


def make_world(tmp_path: Path) -> GithubWorld:
    """A fake GitHub plus a started git remote (stop it with ``world.remote.stop()``)."""
    remote = GitRemote(tmp_path / "remote").start()
    github = FakeGitHub(remote)
    github.add_token(TOKEN, LOGIN, permissions={"administration": "none"})
    github.add_token(OTHER_TOKEN, "other-user")
    github.add_token(READONLY_TOKEN, LOGIN, permissions={"contents": "read"})
    world = GithubWorld(github, remote)
    world.http.transport = github.transport()
    return world


@pytest.fixture
def github_world(app, tmp_path) -> Iterator[GithubWorld]:
    world = make_world(tmp_path)
    world.workspaces = app.state.settings.workspaces_dir
    world.wire_settings(app.state.settings)
    app.state.integrations.http.transport = world.github.transport()
    yield world
    world.remote.stop()


@pytest.fixture
def bare_world(tmp_path) -> Iterator[GithubWorld]:
    """The fakes without an application (self-tests of the fakes and of the client)."""
    world = make_world(tmp_path)
    yield world
    world.remote.stop()


# ------------------------------------------------------------------ git as a person


def push_human_commit(
    world: GithubWorld,
    full_name: str,
    files: dict[str, str | None],
    *,
    branch: str | None = None,
    message: str = "Human change",
    author: str = "Human Dev <human@example.test>",
    parent_branch: str | None = None,
) -> str:
    """A developer pushes a commit to the remote (default: its default branch). ``None`` deletes."""
    repo = world.github.repo(full_name)
    return world.remote.commit(
        full_name,
        branch or repo.default_branch,
        files,
        message=message,
        author=author,
        parent_branch=parent_branch,
    )


def fake_ci(world: GithubWorld, full_name: str, state: str | None, **kwargs) -> None:
    """Set the CI result (success | failure | pending | cancelled | None) for the repository's
    default (every commit without explicit checks) or, with ``sha=``, for one commit."""
    world.github.set_ci(full_name, state, **kwargs)


def askpass_env(tmp_path: Path, token: str, user: str = "x-access-token") -> dict[str, str]:
    """Environment that lets a plain ``git`` in a test authenticate against the ``GitRemote`` the
    way the code under test does (GIT_ASKPASS + the token in the environment)."""
    script = tmp_path / "test-askpass.sh"
    script.write_text(
        '#!/bin/sh\ncase "$1" in\n*Username*) echo "$TEST_GIT_USER" ;;\n*) echo "$TEST_GIT_TOKEN" ;;\nesac\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return {
        "GIT_ASKPASS": str(script),
        "TEST_GIT_USER": user,
        "TEST_GIT_TOKEN": token,
        "GIT_TERMINAL_PROMPT": "0",
    }


# ------------------------------------------------------------------ an active binding


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def bound_project(
    world: GithubWorld,
    app,
    client,
    cid: str,
    *,
    mode: str = "create",
    full_name: str = f"{LOGIN}/app",
    project_name: str = "App",
    files: dict[str, str] | None = None,
    clone: bool = True,
    account: dict | None = None,
    has_workflows: bool = False,
    **policy: Any,
) -> SimpleNamespace:
    """Create a project, a repository on the fake GitHub, a connected GitHub account (the M8 API)
    and an ACTIVE ``ProjectRepository`` with the onboarding defaults of ``mode`` (``create`` or
    ``connect``), overridden by ``policy``. With ``clone`` the M12 project directory holds a clone
    of the remote on the work branch ``foundry/<slug>`` (made with plain local git).

    Returns ``SimpleNamespace(project, repository_id, full_name, work_branch, default_branch,
    repo_dir, account, policy)``."""
    project = client.post(api(cid, "/projects"), json={"name": project_name})
    assert project.status_code == 201, project.text
    project = project.json()
    if account is None:
        account = connect(client, cid, "github", label="GitHub", secret=world.token)
        assert account["status"] == "connected", account
    seed = dict(files or {"README.md": f"# {project_name}\n"})
    if has_workflows:
        seed[".github/workflows/ci.yml"] = "name: ci\non: [push]\njobs: {}\n"
    if full_name not in world.github.repos:
        world.add_repo(full_name, files=seed)
    gh = world.github.repo(full_name)
    default_branch = gh.default_branch
    work_branch = f"foundry/{slugify(project_name)}"
    pol = default_policy(mode, has_workflows=has_workflows).model_dump()  # type: ignore[arg-type]
    pol.update(policy)
    head = world.remote.branch_sha(full_name, default_branch)
    with app.state.session_factory() as db:
        row = ProjectRepository(
            company_id=cid,
            project_id=project["id"],
            connected_account_id=account["id"],
            owner=gh.owner,
            name=gh.name,
            full_name=full_name,
            html_url=f"https://github.com/{full_name}",
            remote_url=f"{world.web_url}/{full_name}.git",
            visibility="private" if gh.private else "public",
            default_branch=default_branch,
            work_branch=work_branch,
            origin="created" if mode == "create" else "connected",
            status="active",
            policy=pol,
            capabilities={},
            protection={},
            etags={},
            last_fetched_default_sha=head,
            integration_head_sha=head,
            is_company_default=True,
            created_by_type="human",
        )
        db.add(row)
        db.commit()
        repository_id = row.id
    repo_dir = None
    if clone:
        repo_dir = clone_into_project(
            world, app, cid, project["id"], full_name, default_branch, work_branch
        )
    return SimpleNamespace(
        project=project,
        repository_id=repository_id,
        full_name=full_name,
        work_branch=work_branch,
        default_branch=default_branch,
        repo_dir=repo_dir,
        account=account,
        policy=pol,
    )


def clone_into_project(
    world: GithubWorld,
    app,
    cid: str,
    project_id: str,
    full_name: str,
    default_branch: str,
    work_branch: str,
) -> Path:
    """Plain-git stand-in for ``repo_git.clone``: the M12 project repository as a clone of the
    remote, on the work branch, with the M12 excludes and a credential-free ``origin`` URL."""
    dest = pw.main_repo(app.state.settings.workspaces_dir, cid, project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_git("clone", "-q", str(world.remote.repo_path(full_name)), str(dest))
    run_git("remote", "set-url", "origin", f"{world.web_url}/{full_name}.git", cwd=dest)
    run_git("checkout", "-q", "-b", work_branch, f"origin/{default_branch}", cwd=dest)
    info = dest / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text("\n".join(pw.EXCLUDES) + "\n")
    return dest


def git_log(repo: Path, ref: str = "HEAD", fmt: str = "%s", n: int = 50) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "log", f"--format={fmt}", f"-{n}", ref],
        capture_output=True,
        text=True,
        env=git_env(),
    )
    return proc.stdout.splitlines()
