"""A stateful in-memory fake of the GitHub REST API for the M14 tests.

``FakeGitHub.handler`` is an ``httpx.MockTransport`` handler. It models what the GitHub-backed
projects code reads and writes: users and organisations, repositories (created through the API),
branches and branch protection / rulesets, issues with comments and labels, pull requests with
reviews / review comments / ``mergeable_state`` / merge / update-branch, check runs, commit statuses,
Actions runs / jobs / log downloads (a 302 to a second host), Dependabot alerts, releases, refs and
webhooks. Cross-cutting behaviour: rate-limit headers (with a primary and a secondary limit knob),
ETags (``If-None-Match`` -> 304), the token-expiry header, SSO and "resource not accessible"
failures, and per-token permissions.

Git data is real: attach a ``GitRemote`` (tests/m14_git_remote.py) and branches, commits, mergeability
and merges are computed from its bare repositories (``git merge-tree --write-tree`` + ``commit-tree``
+ ``update-ref``), so a merge through the fake API really lands on the remote's default branch.

Nothing here knows about the code under test. Test-side helpers (``human_*``, ``set_ci``,
``protect`` ...) act as the people and services around the repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from tests.m14_git_remote import GitRemote

API_HOST = "api.github.test"
LOG_HOST = "logs.githubusercontent.test"
PERMISSIONS = (
    "metadata",
    "contents",
    "pull_requests",
    "issues",
    "administration",
    "dependabot",
    "actions",
    "checks",
    "statuses",
    "workflows",
    "webhooks",
)
_LEVEL = {"none": 0, "read": 1, "write": 2}
NOT_ACCESSIBLE = "Resource not accessible by personal access token"


def default_permissions() -> dict[str, str]:
    perms = {p: "write" for p in PERMISSIONS}
    perms["administration"] = "none"  # a token for an existing repository
    perms["workflows"] = "none"
    perms["webhooks"] = "none"
    perms["metadata"] = "read"
    return perms


@dataclass
class TokenInfo:
    token: str
    login: str
    kind: str = "fine_grained"  # fine_grained | classic
    permissions: dict[str, str] = field(default_factory=default_permissions)
    repos: set[str] | None = None  # None = every repository of the account
    expires_at: datetime | None = None
    revoked: bool = False
    orgs: set[str] = field(default_factory=set)
    sso_orgs: set[str] = field(default_factory=set)  # orgs that demand SSO authorisation
    approval_pending_orgs: set[str] = field(default_factory=set)
    scopes: list[str] = field(default_factory=lambda: ["repo"])


class GhError(Exception):
    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}
        self.extra = extra


@dataclass
class Call:
    method: str
    host: str
    path: str
    query: str
    headers: dict[str, str]
    body: Any
    status: int = 0

    @property
    def authorization(self) -> str | None:
        return self.headers.get("authorization")


@dataclass
class Check:
    name: str
    state: str  # success | failure | pending | cancelled | neutral | skipped
    required: bool = False
    workflow: bool = True
    run_id: int = 0
    job_id: int = 0
    log: str = ""


@dataclass
class FakeRepo:
    owner: str
    name: str
    private: bool = True
    default_branch: str = "main"
    archived: bool = False
    description: str | None = None
    org: bool = False
    labels: list[str] = field(default_factory=lambda: ["bug", "enhancement", "foundry"])
    protection: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ci_default: tuple[str, str] | None = None  # (state, name) for any sha without explicit checks
    ci: dict[str, list[Check]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    hooks: dict[int, dict[str, Any]] = field(default_factory=dict)
    denied: set[str] = field(default_factory=set)  # capabilities every token lacks here
    delete_branch_on_merge: bool = False
    next_number: int = 1
    size_kb: int = 10

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _etag(body: Any) -> str:
    digest = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return f'W/"{digest}"'


class FakeGitHub:
    def __init__(
        self,
        remote: GitRemote | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.remote = remote
        self.web_host = remote.host if remote else None
        self._clock = clock or (lambda: datetime.now(UTC))
        self.tokens: dict[str, TokenInfo] = {}
        self.repos: dict[str, FakeRepo] = {}
        self.orgs: dict[str, set[str]] = {}  # org login -> member logins
        self.calls: list[Call] = []
        self.log_calls: list[Call] = []
        self.rate_limit = 5000
        self.rate_remaining = 5000
        self.rate_reset = int(time.time()) + 3600
        self._secondary: list[int] = []  # retry_after values to serve to the next write requests
        self._fail: list[dict[str, Any]] = []
        self._ids = 1000
        self._routes = self._build_routes()
        if remote is not None:
            remote.authorizer = self.git_authorizer

    # ------------------------------------------------------------ setup helpers

    def now(self) -> datetime:
        return self._clock()

    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    def add_token(
        self,
        token: str,
        login: str,
        *,
        kind: str = "fine_grained",
        permissions: dict[str, str] | None = None,
        repos: set[str] | None = None,
        expires_at: datetime | None = None,
        orgs: set[str] | None = None,
    ) -> TokenInfo:
        perms = default_permissions()
        if kind == "classic":
            perms = {p: "write" for p in PERMISSIONS}
        perms.update(permissions or {})
        info = TokenInfo(
            token,
            login,
            kind,
            perms,
            repos,
            expires_at,
            orgs=set(orgs or ()),
        )
        self.tokens[token] = info
        for org in info.orgs:
            self.orgs.setdefault(org, set()).add(login)
        return info

    def add_org(self, login: str, members: set[str] | None = None) -> None:
        self.orgs.setdefault(login, set()).update(members or ())

    def add_repo(
        self,
        full_name: str,
        *,
        private: bool = True,
        default_branch: str = "main",
        files: dict[str, str] | None = None,
        empty: bool = False,
        org: bool = False,
        archived: bool = False,
        description: str | None = None,
    ) -> FakeRepo:
        owner, name = full_name.split("/")
        repo = FakeRepo(
            owner,
            name,
            private,
            default_branch,
            archived,
            description,
            org=org or owner in self.orgs,
        )
        self.repos[full_name] = repo
        if self.remote is not None and not self.remote.exists(full_name):
            self.remote.create_repo(
                full_name,
                default_branch=default_branch,
                files=None if empty else (files or {"README.md": f"# {name}\n"}),
            )
        return repo

    def repo(self, full_name: str) -> FakeRepo:
        return self.repos[full_name]

    def revoke(self, token: str) -> None:
        self.tokens[token].revoked = True

    def deny(self, full_name: str, capability: str) -> None:
        """Every token lacks ``capability`` (a permission name) on this repository."""
        self.repos[full_name].denied.add(capability)

    def set_rate(self, remaining: int, *, reset_in: int = 3600) -> None:
        self.rate_remaining = remaining
        self.rate_reset = int(time.time()) + reset_in

    def secondary_limit(self, retry_after: int = 30, times: int = 1) -> None:
        """The next ``times`` write requests answer a secondary rate limit."""
        self._secondary.extend([retry_after] * times)

    def fail_next(
        self,
        status: int,
        message: str = "Server error",
        *,
        headers: dict[str, str] | None = None,
        match: str | None = None,
        times: int = 1,
        method: str | None = None,
    ) -> None:
        for _ in range(times):
            self._fail.append(
                {
                    "status": status,
                    "message": message,
                    "headers": headers,
                    "match": match,
                    "method": method,
                }
            )

    def require_sso(self, token: str, org: str) -> None:
        self.tokens[token].sso_orgs.add(org)

    def require_org_approval(self, token: str, org: str) -> None:
        self.tokens[token].approval_pending_orgs.add(org)

    def calls_to(self, method: str | None = None, pattern: str = "") -> list[Call]:
        rx = re.compile(pattern)
        return [
            c
            for c in self.calls
            if (method is None or c.method == method.upper()) and rx.search(c.path)
        ]

    def everything_sent(self) -> str:
        """Every URL, header and body the code under test sent (to assert what was NOT sent)."""
        out = []
        for c in [*self.calls, *self.log_calls]:
            out.append(f"{c.method} {c.host}{c.path}?{c.query}")
            out.append(json.dumps(c.headers))
            out.append(json.dumps(c.body, default=str))
        return "\n".join(out)

    # ------------------------------------------------------------ test-side actors (people, CI)

    def protect(
        self,
        full_name: str,
        branch: str | None = None,
        *,
        require_pr: bool = True,
        approvals: int = 0,
        required_checks: list[str] | None = None,
        strict: bool = False,
        linear_history: bool = False,
    ) -> None:
        repo = self.repos[full_name]
        branch = branch or repo.default_branch
        repo.protection[branch] = {
            "require_pr": require_pr,
            "approvals": approvals,
            "required_checks": list(required_checks or []),
            "strict": strict,
            "linear_history": linear_history,
        }
        rules = []
        if require_pr:
            rules.append(
                {
                    "type": "pull_request",
                    "parameters": {"required_approving_review_count": approvals},
                }
            )
        if required_checks:
            rules.append({"type": "required_status_checks"})
        if linear_history:
            rules.append({"type": "required_linear_history"})
        rules.append({"type": "non_fast_forward"})
        repo.rules[branch] = rules

    def unprotect(self, full_name: str, branch: str | None = None) -> None:
        repo = self.repos[full_name]
        branch = branch or repo.default_branch
        repo.protection.pop(branch, None)
        repo.rules.pop(branch, None)

    def human_issue(
        self,
        full_name: str,
        title: str,
        body: str = "",
        *,
        author: str = "alice",
        association: str = "OWNER",
        labels: list[str] | None = None,
    ) -> int:
        repo = self.repos[full_name]
        return self._new_issue(repo, title, body, author, association, labels or [])["number"]

    def human_comment(
        self,
        full_name: str,
        number: int,
        body: str,
        *,
        author: str = "alice",
        association: str = "OWNER",
    ) -> dict[str, Any]:
        return self._add_comment(self.repos[full_name], number, body, author, association)

    def human_close(self, full_name: str, number: int) -> None:
        repo = self.repos[full_name]
        item = repo.issues.get(number)
        if item is not None:
            item["state"] = "closed"
            item["updated_at"] = _iso(self.now())
        pr = repo.pulls.get(number)
        if pr is not None and pr["state"] == "open":
            pr["state"] = "closed"
            pr["closed_at"] = _iso(self.now())
            pr["head_sha_final"] = self._sha(repo, pr["head_ref"])
            self._touch_issue(repo, number, state="closed")

    def human_reopen(self, full_name: str, number: int) -> None:
        repo = self.repos[full_name]
        if number in repo.issues:
            repo.issues[number]["state"] = "open"
            repo.issues[number]["updated_at"] = _iso(self.now())
        if number in repo.pulls:
            repo.pulls[number]["state"] = "open"

    def human_review(
        self,
        full_name: str,
        number: int,
        state: str,
        body: str = "",
        *,
        author: str = "alice",
        association: str = "OWNER",
    ) -> dict[str, Any]:
        repo = self.repos[full_name]
        pr = repo.pulls[number]
        review = {
            "id": self.next_id(),
            "user": {"login": author},
            "state": state,
            "body": body,
            "submitted_at": _iso(self.now()),
            "commit_id": self._sha(repo, pr["head_ref"]),
            "author_association": association,
        }
        pr["reviews"].append(review)
        self._touch_issue(repo, number)
        return review

    def human_review_comment(
        self,
        full_name: str,
        number: int,
        body: str,
        *,
        path: str = "README.md",
        author: str = "alice",
        association: str = "OWNER",
    ) -> dict[str, Any]:
        repo = self.repos[full_name]
        comment = {
            "id": self.next_id(),
            "user": {"login": author},
            "body": body,
            "path": path,
            "created_at": _iso(self.now()),
            "updated_at": _iso(self.now()),
            "author_association": association,
        }
        repo.pulls[number]["review_comments"].append(comment)
        self._touch_issue(repo, number)
        return comment

    def human_merge(
        self, full_name: str, number: int, *, method: str = "squash", login: str = "alice"
    ) -> dict[str, Any]:
        """A person clicks "merge" (no mergeable_state check, like an admin override)."""
        repo = self.repos[full_name]
        return self._do_merge(repo, number, method, None, login, force=True)

    def set_ci(
        self,
        full_name: str,
        state: str | None,
        *,
        sha: str | None = None,
        name: str = "ci",
        required: bool = False,
        workflow: bool = True,
        log: str | None = None,
    ) -> None:
        """Set the result of CI. ``state`` is success | failure | pending | cancelled | neutral |
        skipped; ``None`` removes it. Without ``sha`` it becomes the default for every commit that
        has no explicit checks (so a fresh PR head gets it too)."""
        repo = self.repos[full_name]
        if sha is None:
            repo.ci_default = None if state is None else (state, name)
            if required and state is not None:
                prot = repo.protection.setdefault(repo.default_branch, {})
                prot.setdefault("required_checks", [])
                if name not in prot["required_checks"]:
                    prot["required_checks"].append(name)
            return
        if state is None:
            repo.ci.pop(sha, None)
            return
        checks = [c for c in repo.ci.get(sha, []) if c.name != name]
        checks.append(
            Check(
                name,
                state,
                required,
                workflow,
                self.next_id(),
                self.next_id(),
                log if log is not None else f"##[group]Run tests\nresult: {state}\n",
            )
        )
        repo.ci[sha] = checks

    def add_alert(
        self,
        full_name: str,
        number: int,
        package: str,
        severity: str = "high",
        ghsa: str | None = None,
        state: str = "open",
        summary: str = "Vulnerability",
    ) -> None:
        self.repos[full_name].alerts.append(
            {
                "number": number,
                "state": state,
                "dependency": {
                    "package": {"name": package, "ecosystem": "pip"},
                    "manifest_path": "requirements.txt",
                },
                "security_advisory": {
                    "ghsa_id": ghsa or f"GHSA-{number:04d}-xxxx-xxxx",
                    "severity": severity,
                    "summary": summary,
                    "description": f"{summary} in {package}",
                },
                "security_vulnerability": {"first_patched_version": {"identifier": "9.9.9"}},
                "html_url": f"https://github.com/{full_name}/security/dependabot/{number}",
                "created_at": _iso(self.now()),
            }
        )

    def close_alert(self, full_name: str, number: int) -> None:
        for alert in self.repos[full_name].alerts:
            if alert["number"] == number:
                alert["state"] = "fixed"

    # ------------------------------------------------------------ git view

    def _sha(self, repo: FakeRepo, branch: str) -> str | None:
        if self.remote is None or not self.remote.exists(repo.full_name):
            return None
        return self.remote.branch_sha(repo.full_name, branch)

    def git_authorizer(self, token: str | None, full_name: str, write: bool) -> int:
        info = self.tokens.get(token or "")
        if info is None or info.revoked:
            return 401
        repo = self.repos.get(full_name)
        if repo is None or not self._can_see(info, repo):
            return 404
        if self._expired(info):
            return 401
        need = "write" if write else "read"
        if _LEVEL[info.permissions.get("contents", "none")] < _LEVEL[need]:
            return 403
        if write and repo.archived:
            return 403
        return 200

    def _expired(self, info: TokenInfo) -> bool:
        return info.expires_at is not None and info.expires_at <= self.now()

    def _can_see(self, info: TokenInfo, repo: FakeRepo) -> bool:
        if info.repos is not None and repo.full_name not in info.repos:
            return False
        return (
            not repo.private
            or repo.owner == info.login
            or repo.owner in info.orgs
            or (info.repos is not None and repo.full_name in info.repos)
        )

    # ------------------------------------------------------------ transport entry

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host + (f":{request.url.port}" if request.url.port else "")
        raw = request.content
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode("utf-8", "replace")
        call = Call(
            request.method,
            host,
            request.url.path,
            request.url.query.decode() if request.url.query else "",
            {k.lower(): v for k, v in request.headers.items()},
            body,
        )
        if host == LOG_HOST:
            self.log_calls.append(call)
            call.status = 200
            return self._log_download(call)
        self.calls.append(call)
        if self.web_host and host == self.web_host:
            resp = self._web(call, request)
            call.status = resp.status_code
            return resp
        try:
            resp = self._dispatch(call, request, body)
        except GhError as exc:
            resp = self._error(exc)
        call.status = resp.status_code
        return resp

    # ------------------------------------------------------------ plumbing

    def _error(self, exc: GhError, extra_headers: dict[str, str] | None = None) -> httpx.Response:
        payload: dict[str, Any] = {
            "message": exc.message,
            "documentation_url": "https://docs.github.com/rest",
        }
        payload.update(exc.extra)
        headers = {**self._rate_headers(), **exc.headers, **(extra_headers or {})}
        return httpx.Response(exc.status, json=payload, headers=headers)

    def _rate_headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit": str(self.rate_limit),
            "x-ratelimit-remaining": str(max(0, self.rate_remaining)),
            "x-ratelimit-reset": str(self.rate_reset),
            "x-ratelimit-resource": "core",
        }

    def _web(self, call: Call, request: httpx.Request) -> httpx.Response:
        """The web host (``<web>/<o>/<r>.git/info/refs``): what ``can_push`` probes."""
        match = re.match(r"^/([^/]+/[^/]+?)\.git/info/refs$", call.path)
        if not match:
            return httpx.Response(404, text="not found")
        token = None
        auth = call.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            import base64

            try:
                user, _, password = base64.b64decode(auth.split(" ", 1)[1]).decode().partition(":")
                token = password if user == "x-access-token" else None
            except Exception:
                token = None
        write = "service=git-receive-pack" in call.query
        status = self.git_authorizer(token, match.group(1), write)
        if status == 200:
            return httpx.Response(200, content=b"001f# service=git-receive-pack\n0000")
        return httpx.Response(status, text="denied")

    def _log_download(self, call: Call) -> httpx.Response:
        match = re.match(r"^/jobs/(\d+)/log$", call.path)
        if not match:
            return httpx.Response(404, text="no log")
        job_id = int(match.group(1))
        for repo in self.repos.values():
            for checks in repo.ci.values():
                for check in checks:
                    if check.job_id == job_id:
                        return httpx.Response(200, text=check.log)
            for check in self._default_checks(repo):
                if check.job_id == job_id:
                    return httpx.Response(200, text=check.log)
        return httpx.Response(404, text="no log")

    def _dispatch(self, call: Call, request: httpx.Request, body: Any) -> httpx.Response:
        auth = call.headers.get("authorization", "")
        scheme, _, value = auth.partition(" ")
        token = value.strip() if scheme.lower() in ("bearer", "token") else ""
        info = self.tokens.get(token)
        for failure in list(self._fail):
            if failure["match"] and failure["match"] not in call.path:
                continue
            if failure["method"] and failure["method"].upper() != call.method:
                continue
            self._fail.remove(failure)
            raise GhError(failure["status"], failure["message"], failure["headers"])
        if info is None or info.revoked or self._expired(info):
            raise GhError(401, "Bad credentials")
        if self.rate_remaining <= 0:
            raise GhError(403, "API rate limit exceeded for user ID 1.")
        is_write = call.method != "GET"
        if is_write and self._secondary:
            retry = self._secondary.pop(0)
            self.rate_remaining -= 1
            raise GhError(
                403,
                "You have exceeded a secondary rate limit. Please wait a few minutes before "
                "you try again.",
                {"retry-after": str(retry)},
            )
        for pattern, method, fn in self._routes:
            if method != call.method:
                continue
            match = pattern.match(call.path)
            if match is None:
                continue
            q = dict(request.url.params)
            result = fn(info, match, q, body)
            headers: dict[str, str] = {}
            status = 200
            if isinstance(result, tuple):
                status, payload = result[0], result[1]
                if len(result) > 2:
                    headers = result[2]
            else:
                payload = result
            if status == 200 and call.method == "GET" and payload is not None:
                etag = _etag(payload)
                if call.headers.get("if-none-match") == etag:
                    return httpx.Response(304, headers={**self._rate_headers(), "etag": etag})
                headers = {**headers, "etag": etag}
            self.rate_remaining -= 1
            all_headers = {**self._rate_headers(), **headers}
            if info.expires_at is not None:
                all_headers["github-authentication-token-expiration"] = info.expires_at.astimezone(
                    UTC
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
            if info.kind == "classic":
                all_headers["x-oauth-scopes"] = ", ".join(info.scopes)
            if payload is None:
                return httpx.Response(status, headers=all_headers)
            return httpx.Response(status, json=payload, headers=all_headers)
        raise GhError(404, "Not Found")

    # -- permission checks

    def _need(self, info: TokenInfo, perm: str, level: str, repo: FakeRepo | None = None) -> None:
        have = info.permissions.get(perm, "none")
        if _LEVEL[have] < _LEVEL[level] or (repo is not None and perm in repo.denied):
            raise GhError(403, NOT_ACCESSIBLE)
        if repo is not None:
            if repo.owner in info.approval_pending_orgs:
                raise GhError(
                    403,
                    "Organization requires approval of fine-grained personal access tokens",
                )
            if repo.owner in info.sso_orgs:
                raise GhError(
                    403,
                    "Resource protected by organization SAML enforcement. You must grant your "
                    "personal access token access to this organization.",
                    {
                        "x-github-sso": "required; url=https://github.com/orgs/"
                        f"{repo.owner}/sso?authorization_request=abc123"
                    },
                )

    def _repo_for(self, info: TokenInfo, match: re.Match) -> FakeRepo:
        full = f"{match.group('o')}/{match.group('r')}"
        repo = self.repos.get(full)
        if repo is None or not self._can_see(info, repo):
            raise GhError(404, "Not Found")
        return repo

    # ------------------------------------------------------------ routes

    def _build_routes(self):
        R = r"/repos/(?P<o>[^/]+)/(?P<r>[^/]+)"
        routes: list[tuple[re.Pattern[str], str, Callable]] = []

        def add(method: str, pattern: str, fn: Callable) -> None:
            routes.append((re.compile("^" + pattern + "$"), method, fn))

        add("GET", r"/user", self._r_user)
        add("GET", r"/user/orgs", self._r_user_orgs)
        add("POST", r"/user/repos", self._r_create_repo)
        add("POST", r"/orgs/(?P<org>[^/]+)/repos", self._r_create_org_repo)
        add("GET", R, self._r_repo)
        add("GET", R + r"/branches", self._r_branches)
        add("GET", R + r"/branches/(?P<b>.+)", self._r_branch)
        add("GET", R + r"/rules/branches/(?P<b>.+)", self._r_rules)
        add("GET", R + r"/git/ref/heads/(?P<b>.+)", self._r_ref_get)
        add("POST", R + r"/git/refs", self._r_ref_create)
        add("DELETE", R + r"/git/refs/heads/(?P<b>.+)", self._r_ref_delete)
        add("GET", R + r"/contents/(?P<path>.+)", self._r_contents)
        add("GET", R + r"/commits", self._r_commits)
        add("GET", R + r"/commits/(?P<ref>[^/]+)/check-runs", self._r_check_runs)
        add("GET", R + r"/commits/(?P<ref>[^/]+)/status", self._r_status)
        add("GET", R + r"/actions/runs", self._r_runs)
        add("GET", R + r"/actions/runs/(?P<run>\d+)/jobs", self._r_jobs)
        add("GET", R + r"/actions/jobs/(?P<job>\d+)/logs", self._r_job_logs)
        add("GET", R + r"/dependabot/alerts", self._r_alerts)
        add("GET", R + r"/labels", self._r_labels)
        add("GET", R + r"/issues", self._r_issues)
        add("POST", R + r"/issues", self._r_issue_create)
        add("PATCH", R + r"/issues/(?P<n>\d+)", self._r_issue_update)
        add("GET", R + r"/issues/(?P<n>\d+)/comments", self._r_issue_comments)
        add("POST", R + r"/issues/(?P<n>\d+)/comments", self._r_issue_comment_create)
        add("GET", R + r"/pulls", self._r_pulls)
        add("POST", R + r"/pulls", self._r_pull_create)
        add("GET", R + r"/pulls/(?P<n>\d+)", self._r_pull)
        add("PATCH", R + r"/pulls/(?P<n>\d+)", self._r_pull_update)
        add("PUT", R + r"/pulls/(?P<n>\d+)/merge", self._r_pull_merge)
        add("PUT", R + r"/pulls/(?P<n>\d+)/update-branch", self._r_pull_update_branch)
        add("GET", R + r"/pulls/(?P<n>\d+)/reviews", self._r_pull_reviews)
        add("GET", R + r"/pulls/(?P<n>\d+)/comments", self._r_pull_comments)
        add("POST", R + r"/releases", self._r_release)
        add("POST", R + r"/hooks", self._r_hook_create)
        add("DELETE", R + r"/hooks/(?P<id>\d+)", self._r_hook_delete)
        return routes

    # -- users and repositories

    def _r_user(self, info, m, q, body):
        return {"login": info.login, "id": 1, "type": "User", "name": info.login.title()}

    def _r_user_orgs(self, info, m, q, body):
        return [
            {"login": org} for org, members in sorted(self.orgs.items()) if info.login in members
        ]

    def _repo_json(self, repo: FakeRepo, info: TokenInfo | None = None) -> dict[str, Any]:
        empty = bool(
            self.remote
            and self.remote.exists(repo.full_name)
            and self.remote.is_empty(repo.full_name)
        )
        return {
            "id": abs(hash(repo.full_name)) % 10**8,
            "name": repo.name,
            "full_name": repo.full_name,
            "private": repo.private,
            "visibility": "private" if repo.private else "public",
            "owner": {"login": repo.owner, "type": "Organization" if repo.org else "User"},
            "html_url": f"https://github.com/{repo.full_name}",
            "description": repo.description,
            "fork": False,
            "archived": repo.archived,
            "default_branch": repo.default_branch,
            "size": 0 if empty else repo.size_kb,
            "has_issues": True,
            "permissions": {
                "admin": bool(info and info.permissions.get("administration") == "write"),
                "push": bool(info and info.permissions.get("contents") == "write"),
                "pull": True,
            },
        }

    def _create(self, info, owner: str, body, *, org: bool):
        self._need(info, "administration", "write")
        name = (body or {}).get("name", "")
        if not re.match(r"^[A-Za-z0-9._-]{1,100}$", name):
            raise GhError(422, "Repository creation failed.", errors=[{"field": "name"}])
        full = f"{owner}/{name}"
        if full in self.repos:
            raise GhError(
                422,
                "Repository creation failed.",
                errors=[
                    {
                        "resource": "Repository",
                        "code": "custom",
                        "field": "name",
                        "message": "name already exists on this account",
                    }
                ],
            )
        repo = self.add_repo(
            full,
            private=bool((body or {}).get("private", False)),
            empty=True,
            org=org,
            description=(body or {}).get("description"),
        )
        return 201, self._repo_json(repo, info)

    def _r_create_repo(self, info, m, q, body):
        return self._create(info, info.login, body, org=False)

    def _r_create_org_repo(self, info, m, q, body):
        org = m.group("org")
        if info.login not in self.orgs.get(org, set()):
            raise GhError(404, "Not Found")
        return self._create(info, org, body, org=True)

    def _r_repo(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "metadata", "read", repo)
        return self._repo_json(repo, info)

    def _r_branches(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "read", repo)
        if self.remote is None:
            return []
        out = [
            {"name": b, "commit": {"sha": sha}, "protected": b in repo.protection}
            for b, sha in sorted(self.remote.branches(repo.full_name).items())
        ]
        return self._page(out, q)

    def _r_branch(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "read", repo)
        sha = self._sha(repo, m.group("b"))
        if sha is None:
            raise GhError(404, "Branch not found")
        return {
            "name": m.group("b"),
            "commit": {"sha": sha},
            "protected": m.group("b") in repo.protection,
        }

    def _r_rules(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "metadata", "read", repo)
        return repo.rules.get(m.group("b"), [])

    def _r_ref_get(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "read", repo)
        sha = self._sha(repo, m.group("b"))
        if sha is None:
            raise GhError(404, "Not Found")
        return {"ref": f"refs/heads/{m.group('b')}", "object": {"sha": sha, "type": "commit"}}

    def _r_ref_create(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "write", repo)
        ref = (body or {}).get("ref", "")
        if not ref.startswith("refs/heads/") or self.remote is None:
            raise GhError(422, "Reference update failed")
        self.remote.set_branch(repo.full_name, ref[len("refs/heads/") :], body["sha"])
        return 201, {"ref": ref, "object": {"sha": body["sha"]}}

    def _r_ref_delete(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "write", repo)
        branch = m.group("b")
        if branch in repo.protection:
            raise GhError(422, "Cannot delete a protected branch")
        if self.remote is None or self.remote.branch_sha(repo.full_name, branch) is None:
            raise GhError(422, "Reference does not exist")
        self.remote.delete_branch(repo.full_name, branch)
        return 204, None

    def _r_contents(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "read", repo)
        ref = q.get("ref") or repo.default_branch
        path = m.group("path").rstrip("/")
        if self.remote is None or not self.remote.path_exists(repo.full_name, ref, path):
            raise GhError(404, "Not Found")
        kind = "dir" if self.remote.read_file(repo.full_name, ref, path) is None else "file"
        return {"type": kind, "path": path, "name": path.rsplit("/", 1)[-1]}

    def _r_commits(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "read", repo)
        if self.remote is None or self.remote.is_empty(repo.full_name):
            raise GhError(409, "Git Repository is empty.")
        return [
            {"sha": line.split(" ", 1)[0], "commit": {"message": line.split(" ", 1)[-1]}}
            for line in self.remote.log(
                repo.full_name, repo.default_branch, n=int(q.get("per_page", 30))
            )
        ]

    # -- CI

    def _default_checks(self, repo: FakeRepo) -> list[Check]:
        if repo.ci_default is None:
            return []
        state, name = repo.ci_default
        key = f"{repo.full_name}:{name}:{state}"
        # stable ids per (repo, name, state) so ETags and log lookups are deterministic
        run_id = 5000 + abs(hash(key)) % 100000
        return [
            Check(
                name,
                state,
                False,
                True,
                run_id,
                run_id + 1,
                f"##[group]Run tests\nresult: {state}\n",
            )
        ]

    def _checks_for(self, repo: FakeRepo, sha: str | None) -> list[Check]:
        if sha is None:
            return []
        return repo.ci.get(sha) or self._default_checks(repo)

    def _resolve(self, repo: FakeRepo, ref: str) -> str | None:
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            return ref
        return self._sha(repo, ref)

    @staticmethod
    def _check_json(c: Check, sha: str | None) -> dict[str, Any]:
        done = c.state != "pending"
        return {
            "id": c.run_id,
            "name": c.name,
            "head_sha": sha,
            "status": "completed" if done else "in_progress",
            "conclusion": c.state if done else None,
            "html_url": f"https://github.com/checks/{c.run_id}",
            "app": {"slug": "github-actions"},
        }

    def _r_check_runs(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "checks", "read", repo)
        sha = self._resolve(repo, m.group("ref"))
        runs = [self._check_json(c, sha) for c in self._checks_for(repo, sha)]
        return {"total_count": len(runs), "check_runs": runs}

    def _r_status(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "statuses", "read", repo)
        sha = self._resolve(repo, m.group("ref"))
        return {
            "state": "pending" if not repo.ci.get(sha or "") else "success",
            "statuses": [],
            "sha": sha,
            "total_count": 0,
        }

    def _r_runs(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "actions", "read", repo)
        sha = q.get("head_sha")
        runs = [
            {
                "id": c.run_id,
                "name": c.name,
                "head_sha": sha,
                "status": "completed" if c.state != "pending" else "in_progress",
                "conclusion": c.state if c.state != "pending" else None,
                "html_url": f"https://github.com/{repo.full_name}/actions/runs/{c.run_id}",
                "event": "pull_request",
            }
            for c in self._checks_for(repo, sha)
            if c.workflow
        ]
        return {"total_count": len(runs), "workflow_runs": runs}

    def _r_jobs(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "actions", "read", repo)
        run_id = int(m.group("run"))
        jobs = []
        for checks in [*repo.ci.values(), self._default_checks(repo)]:
            for c in checks:
                if c.run_id == run_id:
                    jobs.append(
                        {
                            "id": c.job_id,
                            "name": f"{c.name} / job",
                            "status": "completed" if c.state != "pending" else "in_progress",
                            "conclusion": c.state if c.state != "pending" else None,
                            "html_url": f"https://github.com/{repo.full_name}/actions/jobs/{c.job_id}",
                        }
                    )
        return {"total_count": len(jobs), "jobs": jobs}

    def _r_job_logs(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "actions", "read", repo)
        job = int(m.group("job"))
        location = f"https://{LOG_HOST}/jobs/{job}/log?sig=signed"
        return 302, None, {"location": location}

    def _r_alerts(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "dependabot", "read", repo)
        state = q.get("state", "open")
        return self._page([a for a in repo.alerts if a["state"] == state], q)

    # -- issues

    def _r_labels(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "read", repo)
        return self._page([{"name": n} for n in repo.labels], q)

    def _issue_json(self, repo: FakeRepo, number: int) -> dict[str, Any]:
        item = repo.issues[number]
        out = {
            **item,
            "labels": [{"name": n} for n in item["label_names"]],
            "comments": len(repo.comments.get(number, [])),
            "html_url": f"https://github.com/{repo.full_name}/issues/{number}",
        }
        out.pop("label_names", None)
        if number in repo.pulls:
            out["pull_request"] = {
                "url": f"https://api.github.com/repos/{repo.full_name}/pulls/{number}"
            }
            out["html_url"] = f"https://github.com/{repo.full_name}/pull/{number}"
        return out

    def _new_issue(self, repo, title, body, author, association, labels) -> dict[str, Any]:
        number = repo.next_number
        repo.next_number += 1
        for name in labels:
            if name not in repo.labels:
                repo.labels.append(name)
        repo.issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "user": {"login": author},
            "author_association": association,
            "label_names": list(labels),
            "created_at": _iso(self.now()),
            "updated_at": _iso(self.now()),
        }
        return repo.issues[number]

    def _touch_issue(self, repo: FakeRepo, number: int, **fields) -> None:
        item = repo.issues.get(number)
        if item is not None:
            item.update(fields)
            item["updated_at"] = _iso(self.now())

    def _add_comment(self, repo, number, body, author, association) -> dict[str, Any]:
        comment = {
            "id": self.next_id(),
            "user": {"login": author},
            "body": body,
            "created_at": _iso(self.now()),
            "updated_at": _iso(self.now()),
            "author_association": association,
        }
        repo.comments.setdefault(number, []).append(comment)
        self._touch_issue(repo, number)
        return comment

    def _r_issues(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "read", repo)
        state = q.get("state", "open")
        since = q.get("since")
        items = [
            n
            for n, it in repo.issues.items()
            if (state == "all" or it["state"] == state) and (not since or it["updated_at"] >= since)
        ]
        if q.get("labels"):
            wanted = set(q["labels"].split(","))
            items = [n for n in items if wanted <= set(repo.issues[n]["label_names"])]
        items.sort(key=lambda n: (repo.issues[n]["updated_at"], n), reverse=True)
        return self._page([self._issue_json(repo, n) for n in items], q)

    def _r_issue_create(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "write", repo)
        if not (body or {}).get("title"):
            raise GhError(422, "Validation Failed")
        item = self._new_issue(
            repo, body["title"], body.get("body", ""), info.login, "OWNER", body.get("labels", [])
        )
        return 201, self._issue_json(repo, item["number"])

    def _r_issue_update(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "write", repo)
        number = int(m.group("n"))
        if number not in repo.issues:
            raise GhError(404, "Not Found")
        fields: dict[str, Any] = {}
        for key in ("state", "title", "body"):
            if key in (body or {}):
                fields[key] = body[key]
        if "labels" in (body or {}):
            for name in body["labels"]:
                if name not in repo.labels:
                    repo.labels.append(name)  # GitHub creates unknown labels on update
            fields["label_names"] = list(body["labels"])
        self._touch_issue(repo, number, **fields)
        return self._issue_json(repo, number)

    def _r_issue_comments(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "read", repo)
        number = int(m.group("n"))
        since = q.get("since")
        items = [c for c in repo.comments.get(number, []) if not since or c["updated_at"] >= since]
        return self._page(items, q)

    def _r_issue_comment_create(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "issues", "write", repo)
        number = int(m.group("n"))
        if number not in repo.issues:
            raise GhError(404, "Not Found")
        return 201, self._add_comment(
            repo, number, (body or {}).get("body", ""), info.login, "OWNER"
        )

    # -- pull requests

    def _pr_json(self, repo: FakeRepo, number: int) -> dict[str, Any]:
        pr = repo.pulls[number]
        issue = repo.issues[number]
        head_sha = (
            pr.get("head_sha_final") if pr["state"] != "open" else self._sha(repo, pr["head_ref"])
        ) or pr.get("head_sha_final")
        base_sha = self._sha(repo, pr["base_ref"])
        state = (
            self._mergeable_state(repo, pr, head_sha, base_sha)
            if pr["state"] == "open"
            else "unknown"
        )
        return {
            "number": number,
            "title": issue["title"],
            "body": issue["body"],
            "state": pr["state"],
            "merged": pr["merged"],
            "merged_by": {"login": pr["merged_by"]} if pr["merged_by"] else None,
            "merge_commit_sha": pr["merge_commit_sha"],
            "merged_at": pr.get("merged_at"),
            "closed_at": pr.get("closed_at"),
            "draft": False,
            "html_url": f"https://github.com/{repo.full_name}/pull/{number}",
            "user": {"login": pr["author"]},
            "head": {
                "ref": pr["head_ref"],
                "sha": head_sha,
                "label": f"{repo.owner}:{pr['head_ref']}",
                "repo": {"full_name": repo.full_name},
            },
            "base": {
                "ref": pr["base_ref"],
                "sha": base_sha,
                "label": f"{repo.owner}:{pr['base_ref']}",
                "repo": {"full_name": repo.full_name},
            },
            "mergeable": None
            if state == "unknown"
            else state in ("clean", "unstable", "has_hooks", "blocked", "behind"),
            "mergeable_state": pr.get("mergeable_override") or state,
            "created_at": issue["created_at"],
            "updated_at": issue["updated_at"],
            "comments": len(repo.comments.get(number, [])),
        }

    def _mergeable_state(self, repo, pr, head_sha, base_sha) -> str:
        if head_sha is None or base_sha is None or self.remote is None:
            return "unknown"
        clean, _ = self.remote.merge_tree(repo.full_name, base_sha, head_sha)
        if not clean:
            return "dirty"
        prot = repo.protection.get(pr["base_ref"])
        checks = self._checks_for(repo, head_sha)
        by_name = {c.name: c.state for c in checks}
        if prot:
            if prot.get("strict") and not self.remote.is_ancestor(
                repo.full_name, base_sha, head_sha
            ):
                return "behind"
            if prot.get("require_pr") and prot.get("approvals", 0) > 0:
                latest: dict[str, str] = {}
                for r in pr["reviews"]:
                    latest[r["user"]["login"]] = r["state"]
                approvals = sum(
                    1 for u, s in latest.items() if s == "APPROVED" and u != pr["author"]
                )
                if approvals < prot["approvals"]:
                    return "blocked"
            for name in prot.get("required_checks", []):
                if by_name.get(name) != "success":
                    return "blocked"
        if any(c.state != "success" and c.state not in ("neutral", "skipped") for c in checks):
            return "unstable"
        return "clean"

    def _r_pulls(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "read", repo)
        state = q.get("state", "open")
        head = q.get("head")
        out = []
        for number, pr in sorted(repo.pulls.items(), reverse=True):
            if state != "all" and pr["state"] != state:
                continue
            if head:
                wanted = head.split(":", 1)[-1]
                if pr["head_ref"] != wanted:
                    continue
            out.append(self._pr_json(repo, number))
        return self._page(out, q)

    def _r_pull(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "read", repo)
        number = int(m.group("n"))
        if number not in repo.pulls:
            raise GhError(404, "Not Found")
        return self._pr_json(repo, number)

    def _r_pull_create(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "write", repo)
        body = body or {}
        head = str(body.get("head", "")).split(":", 1)[-1]
        base = body.get("base", "")
        if self.remote is None:
            raise GhError(422, "Validation Failed")
        head_sha, base_sha = self._sha(repo, head), self._sha(repo, base)
        if head_sha is None or base_sha is None:
            raise GhError(
                422,
                "Validation Failed",
                errors=[{"resource": "PullRequest", "code": "invalid", "field": "head"}],
            )
        for pr in repo.pulls.values():
            if pr["state"] == "open" and pr["head_ref"] == head and pr["base_ref"] == base:
                raise GhError(
                    422,
                    "Validation Failed",
                    errors=[
                        {
                            "resource": "PullRequest",
                            "code": "custom",
                            "message": f"A pull request already exists for {repo.owner}:{head}.",
                        }
                    ],
                )
        ahead, _ = self.remote.ahead_behind(repo.full_name, base_sha, head_sha)
        if ahead == 0:
            raise GhError(
                422,
                "Validation Failed",
                errors=[
                    {
                        "resource": "PullRequest",
                        "code": "custom",
                        "message": f"No commits between {base} and {head}",
                    }
                ],
            )
        item = self._new_issue(
            repo, body.get("title", ""), body.get("body", ""), info.login, "OWNER", []
        )
        repo.pulls[item["number"]] = {
            "author": info.login,
            "head_ref": head,
            "base_ref": base,
            "state": "open",
            "merged": False,
            "merged_by": None,
            "merge_commit_sha": None,
            "reviews": [],
            "review_comments": [],
        }
        return 201, self._pr_json(repo, item["number"])

    def _r_pull_update(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "write", repo)
        number = int(m.group("n"))
        pr = repo.pulls.get(number)
        if pr is None:
            raise GhError(404, "Not Found")
        fields = {k: v for k, v in (body or {}).items() if k in ("title", "body")}
        if "state" in (body or {}):
            pr["state"] = body["state"]
            fields["state"] = body["state"]
            if body["state"] == "closed":
                pr["closed_at"] = _iso(self.now())
                pr["head_sha_final"] = self._sha(repo, pr["head_ref"])
        if "base" in (body or {}):
            pr["base_ref"] = body["base"]
        self._touch_issue(repo, number, **fields)
        return self._pr_json(repo, number)

    def _r_pull_merge(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "write", repo)
        self._need(info, "pull_requests", "write", repo)
        number = int(m.group("n"))
        if number not in repo.pulls:
            raise GhError(404, "Not Found")
        body = body or {}
        return self._do_merge(
            repo,
            number,
            body.get("merge_method", "merge"),
            body.get("sha"),
            info.login,
            title=body.get("commit_title"),
        )

    def _do_merge(self, repo, number, method, sha, login, *, force=False, title=None):
        pr = repo.pulls[number]
        if pr["state"] != "open" or pr["merged"]:
            raise GhError(405, "Pull Request is not mergeable")
        head_sha = self._sha(repo, pr["head_ref"])
        base_sha = self._sha(repo, pr["base_ref"])
        if sha and head_sha != sha:
            raise GhError(409, "Head branch was modified. Review and try the merge again.")
        state = self._mergeable_state(repo, pr, head_sha, base_sha)
        if not force and state not in ("clean", "has_hooks", "unstable"):
            raise GhError(405, "Pull Request is not mergeable")
        prot = repo.protection.get(pr["base_ref"], {})
        if prot.get("linear_history") and method == "merge":
            raise GhError(405, "Merge commits are not allowed on this repository.")
        clean, tree = self.remote.merge_tree(repo.full_name, base_sha, head_sha)
        if not clean:
            raise GhError(405, "Pull Request is not mergeable")
        issue = repo.issues[number]
        message = title or f"{issue['title']} (#{number})"
        if method == "merge":
            new = self.remote.commit_tree(
                repo.full_name,
                tree,
                [base_sha, head_sha],
                message,
                f"{login} <{login}@users.noreply.github.test>",
            )
        elif method == "rebase" and self.remote.is_ancestor(repo.full_name, base_sha, head_sha):
            new = head_sha
        else:  # squash (and rebase of a diverged head, approximated as a squash)
            new = self.remote.commit_tree(
                repo.full_name,
                tree,
                [base_sha],
                message,
                f"{login} <{login}@users.noreply.github.test>",
            )
        self.remote.update_ref(repo.full_name, pr["base_ref"], new, base_sha)
        pr.update(
            state="closed",
            merged=True,
            merged_by=login,
            merge_commit_sha=new,
            merged_at=_iso(self.now()),
            closed_at=_iso(self.now()),
            head_sha_final=head_sha,
        )
        self._touch_issue(repo, number, state="closed")
        if repo.delete_branch_on_merge:
            self.remote.delete_branch(repo.full_name, pr["head_ref"])
        return {"sha": new, "merged": True, "message": "Pull Request successfully merged"}

    def _r_pull_update_branch(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "write", repo)
        number = int(m.group("n"))
        pr = repo.pulls.get(number)
        if pr is None or pr["state"] != "open":
            raise GhError(404, "Not Found")
        head_sha, base_sha = self._sha(repo, pr["head_ref"]), self._sha(repo, pr["base_ref"])
        expected = (body or {}).get("expected_head_sha")
        if expected and expected != head_sha:
            raise GhError(
                422,
                "Validation Failed",
                errors=[{"message": "expected head sha didn't match current head ref."}],
            )
        clean, tree = self.remote.merge_tree(repo.full_name, head_sha, base_sha)
        if not clean:
            raise GhError(422, "merge conflict between base and head")
        if self.remote.is_ancestor(repo.full_name, base_sha, head_sha):
            return 202, {"message": "Updating pull request branch.", "url": ""}
        new = self.remote.commit_tree(
            repo.full_name,
            tree,
            [head_sha, base_sha],
            f"Merge branch '{pr['base_ref']}' into {pr['head_ref']}",
            "GitHub <noreply@github.test>",
        )
        self.remote.update_ref(repo.full_name, pr["head_ref"], new, head_sha)
        return 202, {"message": "Updating pull request branch.", "url": ""}

    def _r_pull_reviews(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "read", repo)
        pr = repo.pulls.get(int(m.group("n")))
        if pr is None:
            raise GhError(404, "Not Found")
        return self._page(list(pr["reviews"]), q)

    def _r_pull_comments(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "pull_requests", "read", repo)
        pr = repo.pulls.get(int(m.group("n")))
        if pr is None:
            raise GhError(404, "Not Found")
        since = q.get("since")
        return self._page(
            [c for c in pr["review_comments"] if not since or c["updated_at"] >= since], q
        )

    # -- releases and hooks

    def _r_release(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "contents", "write", repo)
        body = body or {}
        rel = {
            "id": self.next_id(),
            "tag_name": body.get("tag_name"),
            "name": body.get("name"),
            "body": body.get("body", ""),
            "draft": bool(body.get("draft", False)),
            "prerelease": bool(body.get("prerelease", False)),
            "target_commitish": body.get("target_commitish", repo.default_branch),
            "html_url": f"https://github.com/{repo.full_name}/releases/tag/{body.get('tag_name')}",
        }
        repo.releases.append(rel)
        return 201, rel

    def _r_hook_create(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "webhooks", "write", repo)
        hook_id = self.next_id()
        repo.hooks[hook_id] = {"id": hook_id, **(body or {})}
        shown = {**repo.hooks[hook_id]}
        shown["config"] = {**shown.get("config", {}), "secret": "********"}
        return 201, shown

    def _r_hook_delete(self, info, m, q, body):
        repo = self._repo_for(info, m)
        self._need(info, "webhooks", "write", repo)
        if repo.hooks.pop(int(m.group("id")), None) is None:
            raise GhError(404, "Not Found")
        return 204, None

    # -- paging

    @staticmethod
    def _page(items: list[Any], q: dict[str, str]) -> list[Any]:
        per_page = max(1, min(int(q.get("per_page", 30)), 100))
        page = max(1, int(q.get("page", 1)))
        return items[(page - 1) * per_page : page * per_page]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
