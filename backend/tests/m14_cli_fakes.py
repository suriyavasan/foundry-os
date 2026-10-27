"""A fake Foundry server for the M14 CLI tests (``test_m14_cli_setup_repo.py``, ``test_m14_cli_repo.py``).

``FakeApi`` is an ``httpx.MockTransport`` handler that speaks only the routes the CLI uses for GitHub
repositories (the step-0 routes of ``app/api/routes/repositories.py``, the credential and connected
account routes, and the few company routes the wizard reads). It records every request so tests can
assert what the CLI sent, in which order, and where a secret did or did not travel. Nothing here
touches a network.

Use the fixtures:

    from tests.m14_cli_fakes import cfg_dir, fake, runner  # noqa: F401
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from app.cli import context
from app.cli.commands import onboard
from app.cli.commands import repo as repo_commands
from app.cli.main import app as cli_app

IDEA = "A newsletter that explains one research paper a day"
TOKEN = "github_pat_11CLIFAKE000_" + "q1W2e3R4t5Y6u7I8o9P0a1S2d3F4g5H6j7K8l9Z0x1C2v3B4n5M6"
CID = "c1"

REMOTE_DEFAULT = {
    "exists": True,
    "accessible": True,
    "archived": False,
    "empty": False,
    "visibility": "private",
    "default_branch": "main",
    "protected": False,
    "has_workflows": False,
    "can_push": True,
    "capabilities": {},
}


def error_body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def page(items: list) -> dict:
    return {"items": items, "next_cursor": None}


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self):
        return json.loads(self.out)


class FakeApi:
    """State and behaviour of the fake server. Tweak the attributes before running the CLI."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []  # (method, path below the company, json body)
        self._raw: list[tuple[str, str, str]] = []  # (method, path, everything that was sent)
        self.company = {"id": CID, "name": "Acme Labs", "slug": "acme-labs"}
        self.projects = [{"id": "p1", "name": "Existing"}]
        self.repos: list[dict] = []
        self.accounts: list[dict] = []
        self.credentials: list[dict] = []
        self.notes: list[str] = []
        self.pulls: list[dict] = []
        self.items: list[dict] = []
        self.activity: list[dict] = []
        self.owners = [{"login": "foundry-bot", "kind": "user"}]
        self.missing: list[str] = []
        self.remote_repos = {"octo/widget": dict(REMOTE_DEFAULT, full_name="octo/widget")}
        self.available: dict = {
            "available": True,
            "reason": None,
            "git_version": "2.50.0",
            "milestone": "M14",
        }
        self.progress: list[str] = []  # statuses returned by successive GET /repositories/{id}
        self.status_reason: str | None = None
        self.onboarding = "not_needed"
        self.token_error: str | None = None
        self.bad_tokens_left: int | None = (
            None  # None: every token is refused while token_error is set
        )
        self.check_ok = True  # POST /connected-accounts/{id}/test
        self._errors: dict[tuple[str, str], tuple[int, dict]] = {}

    # ------------------------------------------------------------------ test helpers

    def fail(
        self, method: str, rel: str, status: int, code: str, message: str, details: Any = None
    ) -> None:
        self._errors[(method, rel)] = (status, error_body(code, message, details))

    def bodies(self, method: str, rel: str) -> list[Any]:
        return [b for m, p, b in self.calls if (m, p) == (method, rel)]

    def calls_to(self, rel: str) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[1] == rel]

    def where_seen(self, secret: str) -> list[str]:
        """``METHOD path`` of every request that carried ``secret`` anywhere (URL, headers, body)."""
        return [f"{m} {p}" for m, p, raw in self._raw if secret in raw]

    def add_account(
        self, *, login: str = "foundry-bot", label: str = "GitHub", status: str = "connected"
    ) -> dict:
        n = len(self.accounts) + 1
        account = {
            "id": f"a{n}",
            "provider": "github",
            "label": label,
            "status": status,
            "credential_id": f"cred{n}",
            "metadata": {"login": login},
            "last_error": None,
        }
        self.accounts.append(account)
        return account

    def add_repo(self, full_name: str, **fields: Any) -> dict:
        n = len(self.repos) + 1
        owner, name = full_name.split("/")
        row = {
            "id": f"r{n}",
            "company_id": CID,
            "project_id": f"p{n + 1}",
            "project_name": fields.pop("project_name", name),
            "provider": "github",
            "connected_account_id": self.accounts[0]["id"] if self.accounts else "a1",
            "owner": owner,
            "name": name,
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "visibility": "private",
            "default_branch": "main",
            "work_branch": f"foundry/{name}",
            "origin": "connected",
            "status": "active",
            "status_reason": None,
            "sync_state": "idle",
            "policy": {
                "merge_mode": "pr_human_merge",
                "pr_merge_method": "squash",
                "ci_mode": "if_present",
                "issue_intake": "labeled",
                "poll_interval_seconds": 300,
            },
            "last_synced_at": "2026-10-27T10:00:00Z",
            "next_sync_at": "2026-10-27T10:05:00Z",
            "token_expires_at": None,
            "rate_limit_remaining": 4900,
            "webhook": {"enabled": False, "url": None, "stale": False},
            "onboarding_status": "not_needed",
            "is_company_default": False,
            "pr_opening_paused": False,
            "open_pull_request": None,
        }
        row.update(fields)
        self.repos.append(row)
        return row

    def repo(self, rid: str) -> dict | None:
        return next((r for r in self.repos if r["id"] == rid), None)

    # ------------------------------------------------------------------ the transport

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith("/api"), path
        rel = path[len("/api") :]
        company = f"/companies/{CID}"
        if rel.startswith(company):
            rel = rel[len(company) :]
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode(errors="replace")
        self.calls.append((request.method, rel, body))
        sent = f"{request.url}\n{dict(request.headers)}\n{request.content.decode(errors='replace')}"
        self._raw.append((request.method, rel, sent))
        scripted = self._errors.get((request.method, rel))
        if scripted:
            return httpx.Response(scripted[0], json=scripted[1])
        try:
            status, payload = self.route(request.method, rel, body, dict(request.url.params))
        except KeyError:
            return httpx.Response(404, json=error_body("not_found", "No such thing"))
        return httpx.Response(status, json=payload)

    # ------------------------------------------------------------------ routes

    def route(self, method: str, rel: str, body: Any, params: dict) -> tuple[int, Any]:  # noqa: C901
        key = (method, rel)
        if key == ("GET", "/auth/session"):
            return 200, {"state": "authenticated", "user": {"username": "alice"}}
        if key == ("GET", "/me"):
            return 200, {"user": {"username": "alice"}}
        if key == ("GET", "") or key == ("GET", "/companies"):
            return 200, page([self.company])
        if rel == "/ai-setup":
            return 200, {"ready": True, "management": {"provider_name": "P", "model": "m"}}
        if rel == "/notes":
            if method == "POST":
                self.notes.append(body["body"])
                return 201, {"id": f"n{len(self.notes)}", "body": body["body"]}
            return 200, page([{"id": f"n{i}", "body": b} for i, b in enumerate(self.notes)])
        if key == ("GET", "/projects"):
            return 200, page(self.projects)
        if key == ("GET", "/repositories/status"):
            return 200, self.available
        if key == ("GET", "/repositories"):
            return 200, page([dict(r) for r in self.repos])
        if key == ("POST", "/repositories"):
            return 201, self.create_repo(body)
        if rel == "/credentials" or re.fullmatch(r"/credentials/[^/]+/rotate", rel):
            return self.credential(method, rel, body)
        if rel.startswith("/connected-accounts"):
            return self.account(method, rel, body)
        if key == ("POST", "/github/inspect"):
            return 200, self.inspect(body)
        m = re.fullmatch(r"/repositories/([^/]+)(/.*)?", rel)
        if m:
            return self.repository(method, m.group(1), m.group(2) or "", body)
        raise KeyError(rel)

    def create_repo(self, body: dict) -> dict:
        create = body["mode"] == "create"
        name = body.get("name")
        full = body.get("full_name") or f"{body.get('owner') or 'foundry-bot'}/{name}"
        project = body.get("project_name") or next(
            (p["name"] for p in self.projects if p["id"] == body.get("project_id")), "Project"
        )
        row = self.add_repo(
            full,
            project_name=project,
            status="creating" if create else "cloning",
            origin="created" if create else "connected",
            connected_account_id=body["connected_account_id"],
            visibility=body.get("visibility", "private"),
            default_branch=body.get("default_branch") or "main",
            onboarding_status=self.onboarding,
            is_company_default=True,
        )
        row["policy"] = {
            **row["policy"],
            "merge_mode": "pr_auto_merge" if create else "pr_human_merge",
            "issue_intake": "collaborators" if create else "labeled",
            **(body.get("policy") or {}),
        }
        return dict(row)

    def credential(self, method: str, rel: str, body: Any) -> tuple[int, Any]:
        if method == "GET":
            return 200, page([{"id": c["id"], "name": c["name"]} for c in self.credentials])
        if rel.endswith("/rotate"):
            cid = rel.split("/")[2]
            cred = next(c for c in self.credentials if c["id"] == cid)
            cred["value"] = body["value"]
            return 200, {"id": cid, "name": cred["name"]}
        if any(c["name"] == body["name"] for c in self.credentials):
            return 409, error_body(
                "conflict", f"A credential named {body['name']!r} already exists"
            )
        cred = {
            "id": f"cred{len(self.credentials) + 1}",
            "name": body["name"],
            "value": body["value"],
        }
        self.credentials.append(cred)
        return 201, {"id": cred["id"], "name": cred["name"]}

    def account(self, method: str, rel: str, body: Any) -> tuple[int, Any]:
        if (method, rel) == ("GET", "/connected-accounts"):
            return 200, page([dict(a) for a in self.accounts])
        if (method, rel) == ("POST", "/connected-accounts"):
            account = self.add_account(label=body["label"])
            account["credential_id"] = body["credential_id"]
            refused = self.token_error and (
                self.bad_tokens_left is None or self.bad_tokens_left > 0
            )
            if refused:
                if self.bad_tokens_left is not None:
                    self.bad_tokens_left -= 1
                account["status"] = "invalid"
                account["last_error"] = self.token_error
            return 201, dict(account)
        m = re.fullmatch(r"/connected-accounts/([^/]+)(/test)?", rel)
        if m and method == "DELETE":
            self.accounts = [a for a in self.accounts if a["id"] != m.group(1)]
            return 200, {"id": m.group(1), "status": "revoked"}
        if m and method == "POST" and m.group(2):
            return 200, {
                "ok": self.check_ok,
                "message": None if self.check_ok else "Bad credentials",
            }
        raise KeyError(rel)

    def inspect(self, body: dict) -> dict:
        result: dict[str, Any] = {
            "connected_account_id": body["connected_account_id"],
            "login": "foundry-bot",
            "token_kind": "fine_grained",
            "token_expires_at": None,
            "owners": self.owners,
            "repository": None,
            "missing_permissions": list(self.missing),
            "warnings": [],
        }
        full = body.get("full_name")
        if full:
            known = self.remote_repos.get(full)
            if known is None:
                known = dict(
                    REMOTE_DEFAULT, full_name=full, exists=False, accessible=False, can_push=False
                )
            result["repository"] = dict(known)
        return result

    def repository(self, method: str, rid: str, tail: str, body: Any) -> tuple[int, Any]:
        row = self.repo(rid)
        if row is None:
            return 404, error_body("not_found", "No such repository")
        if (method, tail) == ("GET", ""):
            if self.progress:
                row["status"] = self.progress.pop(0)
            elif row["status"] in ("creating", "cloning"):
                row["status"] = "active"
            if row["status"] in ("error", "auth_failed"):
                row["status_reason"] = self.status_reason
            return 200, dict(row)
        if (method, tail) == ("PATCH", ""):
            row["policy"] = {**row["policy"], **(body.get("policy") or {})}
            if "is_company_default" in body:
                row["is_company_default"] = body["is_company_default"]
            return 200, dict(row)
        if (method, tail) == ("GET", "/pull-requests"):
            return 200, page(self.pulls)
        if (method, tail) == ("GET", "/items"):
            return 200, page(self.items)
        if (method, tail) == ("GET", "/activity"):
            return 200, page(self.activity)
        if (method, tail) == ("PUT", "/webhook"):
            row["webhook"] = {"enabled": body["enabled"], "url": None, "stale": False}
            return 200, dict(row)
        if (method, tail) == ("POST", "/sync"):
            return 202, {"queued": True}
        if method == "POST" and tail in (
            "/pause",
            "/resume",
            "/disconnect",
            "/integration/reset",
            "/pull-requests/resume-opening",
        ):
            row["status"] = {
                "/pause": "paused",
                "/resume": "active",
                "/disconnect": "disconnected",
            }.get(tail, row["status"])
            return 200, dict(row)
        raise KeyError(tail)


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cfg"
    monkeypatch.setenv("COMPANYOS_CONFIG_DIR", str(d))
    for name in ("COMPANYOS_API_URL", "COMPANYOS_COMPANY", "TZ"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COMPANYOS_TOKEN", "owner-token-for-the-fake-server")
    monkeypatch.setenv("COLUMNS", "200")
    return d


@pytest.fixture
def fake(cfg_dir, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = FakeApi()
    monkeypatch.setattr(
        context,
        "http_factory",
        lambda url: httpx.Client(transport=api.transport(), base_url=url),
    )
    monkeypatch.setattr(onboard, "sleep", lambda seconds: None)
    monkeypatch.setattr(repo_commands, "sleep", lambda seconds: None)
    return api


@pytest.fixture
def runner(fake: FakeApi):
    cli = CliRunner()

    def invoke(*args: str, input: str | None = None, expect: int | None = 0) -> Result:
        r = cli.invoke(cli_app, list(args), input=input)
        res = Result(r.exit_code, r.stdout, r.stderr)
        if expect is not None:
            assert res.code == expect, (
                f"exit {res.code} (wanted {expect})\nargs: {args}\nout: {res.out}\nerr: {res.err}"
            )
        return res

    return invoke
