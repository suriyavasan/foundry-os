"""GitHub: the Founder's identity, the fork -> branch -> file -> pull request flow that
free-subdomain registries use (M8), and the repository API surface of GitHub-backed projects (M14).

A fine-grained or classic token is enough. The client is a small, synchronous wrapper over an
injectable ``httpx.Client``; nothing here touches git (``services/repo_git.py`` is the only module
that runs network git). M14 additions:

* every response updates ``last_rate`` (``X-RateLimit-*``) and ``token_expires_at``
  (``GitHub-Authentication-Token-Expiration``);
* conditional requests: a caller passes ``etags`` (a dict it persists per repository) and
  ``conditional=True`` on a read; a 304 returns ``NOT_MODIFIED`` and does not count against the
  primary rate limit;
* primary and secondary rate limits raise ``GithubRateLimited`` (with ``retry_after`` when GitHub
  gave one), a 403 with ``X-GitHub-SSO`` raises ``GithubSsoRequired``;
* content-creating requests are spaced at least ``write_interval`` seconds apart (injectable
  ``sleep`` and ``clock``);
* the job-log download follows GitHub's redirect to its log host WITHOUT the Authorization header.

Verified against GitHub's documentation only; the suite uses ``tests/m14_github_fake.py``.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.integrations.base import ApiClient, ProviderAuthError, ProviderError
from app.services.secrets_scan import scrub

DEFAULT_WRITE_INTERVAL = 1.0
MAX_PAGES = 5
LOG_TAIL_BYTES = 32 * 1024


class _NotModified:
    """The value of a conditional read answered with 304."""

    def __repr__(self) -> str:
        return "NOT_MODIFIED"

    def __bool__(self) -> bool:
        return False


NOT_MODIFIED = _NotModified()


class GithubRateLimited(ProviderError):
    """The primary or a secondary rate limit was hit (HTTP 403 or 429). ``retry_after`` is the
    number of seconds GitHub asked us to wait, when it said so; ``reset_at`` is the primary
    limit's reset time."""

    status_code = 429
    code = "github_rate_limited"
    default_message = "GitHub rate limit reached"

    def __init__(
        self,
        message: str | None = None,
        details: Any = None,
        *,
        retry_after: int | None = None,
        reset_at: datetime | None = None,
        secondary: bool = False,
    ) -> None:
        super().__init__(message, details)
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.secondary = secondary

    def wait_seconds(self, failures: int = 0, *, now: datetime | None = None) -> int:
        """How long to back off: ``Retry-After``, else the primary reset, else ``60 * 2**n``
        capped at 900 s."""
        if self.retry_after is not None:
            return max(1, int(self.retry_after))
        if self.reset_at is not None and not self.secondary:
            current = now or datetime.now(UTC)
            return max(1, int((self.reset_at - current).total_seconds()) + 1)
        return min(900, 60 * (2 ** max(0, failures)))


class GithubSsoRequired(ProviderAuthError):
    """The token must be authorised for the organisation's SAML SSO. ``sso_url`` is where."""

    code = "github_sso_required"
    default_message = "The token must be authorised for SAML SSO"

    def __init__(
        self, message: str | None = None, details: Any = None, *, sso_url: str | None = None
    ) -> None:
        super().__init__(message, details)
        self.sso_url = sso_url


@dataclass
class RateInfo:
    """The last ``X-RateLimit-*`` headers seen."""

    remaining: int | None = None
    limit: int | None = None
    reset_at: datetime | None = None
    resource: str | None = None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_expiration(value: str | None) -> datetime | None:
    """``GitHub-Authentication-Token-Expiration`` is ``2026-12-01 10:00:00 UTC`` (documented
    format, not exercised against GitHub here)."""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


class GithubClient(ApiClient):
    provider = "github"
    base_url = "https://api.github.com"
    web_url = "https://github.com"

    def __init__(
        self,
        http,
        token: str,
        *,
        base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        web_url: str | None = None,
        etags: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        write_interval: float = DEFAULT_WRITE_INTERVAL,
    ) -> None:
        super().__init__(http, token)
        self._token = token
        if base_url:
            self.base_url = base_url
        if web_url:
            self.web_url = web_url
        self._sleep = sleep
        self._clock = clock
        self.write_interval = write_interval
        self._last_write: float | None = None
        self.scopes: list[str] = []
        # M14: the caller persists this (``project_repositories.etags``).
        self.etags: dict[str, str] = etags if etags is not None else {}
        self.last_rate = RateInfo()
        self.token_expires_at: datetime | None = None

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ------------------------------------------------------------------ M14 plumbing

    def _etag_key(self, url: str, params: dict[str, Any] | None) -> str:
        query = urlencode(sorted((params or {}).items()))
        return f"{url}?{query}" if query else url

    def _observe(self, resp: httpx.Response) -> None:
        headers = resp.headers
        remaining = _int(headers.get("x-ratelimit-remaining"))
        if remaining is not None:
            self.last_rate.remaining = remaining
            self.last_rate.limit = _int(headers.get("x-ratelimit-limit"))
            reset = _int(headers.get("x-ratelimit-reset"))
            self.last_rate.reset_at = datetime.fromtimestamp(reset, UTC) if reset else None
            self.last_rate.resource = headers.get("x-ratelimit-resource")
        expires = parse_expiration(headers.get("github-authentication-token-expiration"))
        if expires is not None:
            self.token_expires_at = expires

    def _space_writes(self) -> None:
        """Content-creating requests are at least ``write_interval`` seconds apart."""
        if self.write_interval > 0 and self._last_write is not None:
            wait = self.write_interval - (self._clock() - self._last_write)
            if wait > 0:
                self._sleep(wait)
        self._last_write = self._clock()

    def request(  # type: ignore[override]
        self,
        method: str,
        path: str,
        *,
        what: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        absolute: bool = False,
        conditional: bool = False,
        redirect_ok: bool = False,
    ) -> httpx.Response:
        url = path if absolute else self.base_url.rstrip("/") + "/" + path.lstrip("/")
        merged = {**self.auth_headers(), **(headers or {})}
        key = None
        if conditional and method.upper() == "GET":
            key = self._etag_key(url, params)
            if key in self.etags:
                merged["If-None-Match"] = self.etags[key]
        try:
            resp = self.http.request(
                method,
                url,
                json=json,
                params=params,
                data=data,
                headers=merged,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise self._fail(None, type(exc).__name__ + ": " + str(exc), what) from exc
        self._observe(resp)
        if resp.status_code >= 400:
            raise self._github_error(resp, what)
        if resp.status_code in (301, 302, 307, 308) and not redirect_ok:
            raise self._fail(resp.status_code, "the repository moved or was renamed", what)
        if key is not None and resp.status_code == 200 and resp.headers.get("etag"):
            self.etags[key] = resp.headers["etag"]
        return resp

    def _github_error(self, resp: httpx.Response, what: str) -> ProviderError:
        status = resp.status_code
        text = self.error_text(resp)
        low = text.lower()
        headers = resp.headers
        retry_after = _int(headers.get("retry-after"))
        if status in (403, 429):
            sso = headers.get("x-github-sso")
            if sso:
                match = re.search(r"url=(\S+)", sso)
                url = match.group(1).rstrip(";") if match else None
                err = GithubSsoRequired(
                    f"github: {what} failed: authorise the token for SAML SSO",
                    {"provider": "github", "status": status, "reason": "sso_required"},
                    sso_url=url,
                )
                return err
            secondary = "secondary rate limit" in low or "abuse" in low
            primary = headers.get("x-ratelimit-remaining") == "0" and (
                status == 429 or "rate limit" in low
            )
            if status == 429 or retry_after is not None or secondary or primary:
                stamp = _int(headers.get("x-ratelimit-reset"))
                reset = datetime.fromtimestamp(stamp, UTC) if stamp else None
                message = f"github: {what} failed (HTTP {status}): rate limited"
                return GithubRateLimited(
                    message,
                    {"provider": "github", "status": status, "reason": "rate_limited"},
                    retry_after=retry_after,
                    reset_at=reset,
                    secondary=secondary or retry_after is not None,
                )
        err = self._fail(status, text, what)
        reason = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "not_allowed",
            409: "conflict",
            422: "unprocessable",
        }.get(status, "error")
        if status == 403 and "resource not accessible" in low:
            reason = "not_accessible"  # the token lacks a permission for this request
        details = dict(err.details or {})
        details["reason"] = reason
        if status == 422 and "already exists" in low:
            details["already_exists"] = True
        err.details = details
        return err

    def get_json(
        self,
        path: str,
        *,
        what: str,
        params: dict[str, Any] | None = None,
        conditional: bool = False,
    ) -> Any:
        """GET and decode; ``NOT_MODIFIED`` when ``conditional`` and GitHub answered 304."""
        resp = self.request("GET", path, what=what, params=params, conditional=conditional)
        if resp.status_code == 304:
            return NOT_MODIFIED
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise self._fail(resp.status_code, "response was not JSON", what) from exc

    def _paged(
        self,
        path: str,
        *,
        what: str,
        params: dict[str, Any] | None = None,
        per_page: int = 50,
        max_pages: int = MAX_PAGES,
        key: str | None = None,
        conditional: bool = False,
    ) -> Any:
        """All items of a list endpoint (at most ``max_pages`` pages). ``key`` unwraps an object
        like ``{"check_runs": [...]}``. ``conditional`` applies to the first page: a 304 returns
        ``NOT_MODIFIED``."""
        items: list[Any] = []
        for page in range(1, max_pages + 1):
            body = self.get_json(
                path,
                what=what,
                params={**(params or {}), "per_page": per_page, "page": page},
                conditional=conditional and page == 1,
            )
            if body is NOT_MODIFIED:
                return NOT_MODIFIED
            chunk = body.get(key, []) if key and isinstance(body, dict) else body
            if not isinstance(chunk, list):
                break
            items.extend(chunk)
            if len(chunk) < per_page:
                break
        return items

    def _write(self, method: str, path: str, *, what: str, json: Any = None) -> Any:
        """A content-creating request, spaced from the previous one."""
        self._space_writes()
        return self.json(method, path, what=what, json=json)

    def identity(self) -> dict[str, Any]:
        resp = self.request("GET", "/user", what="read the account")
        scopes = resp.headers.get("x-oauth-scopes", "")
        self.scopes = [s.strip() for s in scopes.split(",") if s.strip()]
        return resp.json()

    def repo(self, full_name: str) -> dict[str, Any] | None:
        try:
            return self.json("GET", f"/repos/{full_name}", what="read the repository")
        except ProviderError as exc:
            if (exc.details or {}).get("status") == 404:
                return None
            raise

    def ensure_fork(self, upstream: str, login: str, *, attempts: int = 10) -> dict[str, Any]:
        """The caller's fork of ``upstream`` (``owner/name``), created when missing."""
        name = upstream.split("/", 1)[1]
        existing = self.repo(f"{login}/{name}")
        if existing and existing.get("fork"):
            return existing
        self.json("POST", f"/repos/{upstream}/forks", what="fork the repository", json={})
        for _ in range(attempts):  # forking is asynchronous
            fork = self.repo(f"{login}/{name}")
            if fork:
                return fork
            self._sleep(2)
        raise ProviderError("github: the fork did not appear in time; try again in a minute")

    def sync_fork(self, fork: str, branch: str) -> None:
        self.json(
            "POST",
            f"/repos/{fork}/merge-upstream",
            what="sync the fork",
            json={"branch": branch},
        )

    def branch_sha(self, repo: str, branch: str) -> str:
        body = self.json("GET", f"/repos/{repo}/git/ref/heads/{branch}", what="read the branch")
        return body["object"]["sha"]

    def create_branch(self, repo: str, branch: str, sha: str) -> None:
        self.json(
            "POST",
            f"/repos/{repo}/git/refs",
            what="create the branch",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def put_file(self, repo: str, path: str, content: str, message: str, branch: str) -> dict:
        return self.json(
            "PUT",
            f"/repos/{repo}/contents/{path}",
            what="commit the file",
            json={
                "message": message,
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch,
            },
        )

    def open_pull(self, upstream: str, *, title: str, body: str, head: str, base: str) -> dict:
        return self.json(
            "POST",
            f"/repos/{upstream}/pulls",
            what="open the pull request",
            json={"title": title, "body": body, "head": head, "base": base},
        )

    def pull(self, upstream: str, number: int) -> dict[str, Any]:
        return self.json("GET", f"/repos/{upstream}/pulls/{number}", what="read the pull request")

    # ------------------------------------------------------------------ M14: repositories

    def get_repo(self, full_name: str, *, conditional: bool = False) -> Any:
        """The repository object (404 raises; ``repo()`` returns None instead)."""
        return self.get_json(
            f"/repos/{full_name}", what="read the repository", conditional=conditional
        )

    def create_repo(
        self,
        owner: str,
        name: str,
        *,
        private: bool = True,
        description: str | None = None,
        org: bool = False,
    ) -> dict[str, Any]:
        """Create an EMPTY repository (``auto_init`` is false: Foundry makes the first commit)."""
        path = f"/orgs/{owner}/repos" if org else "/user/repos"
        body: dict[str, Any] = {"name": name, "private": private, "auto_init": False}
        if description:
            body["description"] = description
        return self._write("POST", path, what="create the repository", json=body)

    def list_orgs(self) -> list[dict[str, Any]]:
        return self._paged("/user/orgs", what="list the organisations", per_page=100)

    def get_branch(self, repo: str, branch: str, *, conditional: bool = False) -> Any:
        """The branch object, including ``protected`` and ``commit.sha``."""
        return self.get_json(
            f"/repos/{repo}/branches/{quote(branch, safe='/')}",
            what="read the branch",
            conditional=conditional,
        )

    def list_branches(self, repo: str, *, max_pages: int = MAX_PAGES) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/branches", what="list the branches", per_page=100, max_pages=max_pages
        )

    def branch_rules(self, repo: str, branch: str) -> list[dict[str, Any]]:
        """Ruleset rules that apply to a branch (``[]`` when none, or when the API is absent)."""
        try:
            body = self.get_json(
                f"/repos/{repo}/rules/branches/{quote(branch, safe='/')}",
                what="read the branch rules",
            )
        except ProviderError as exc:
            if (exc.details or {}).get("status") == 404:
                return []
            raise
        return body if isinstance(body, list) else []

    def path_exists(self, repo: str, path: str, ref: str | None = None) -> bool:
        """Whether a file or directory exists (``.github/workflows`` decides the CI default)."""
        try:
            self.get_json(
                f"/repos/{repo}/contents/{quote(path, safe='/')}",
                what="read the repository contents",
                params={"ref": ref} if ref else None,
            )
        except ProviderError as exc:
            if (exc.details or {}).get("status") == 404:
                return False
            raise
        return True

    def is_empty(self, repo: str) -> bool:
        """True for a repository without any commit (GitHub answers 409 to a commit listing)."""
        try:
            self.get_json(
                f"/repos/{repo}/commits",
                what="read the commits",
                params={"per_page": 1},
            )
        except ProviderError as exc:
            if (exc.details or {}).get("status") == 409:
                return True
            raise
        return False

    def can_push(self, full_name: str) -> bool:
        """Whether this token may push: ``info/refs?service=git-receive-pack`` on the web host
        answers 200 only with Contents write (403 or 401 otherwise). No API quota is used."""
        url = f"{self.web_url.rstrip('/')}/{full_name}.git/info/refs"
        basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        try:
            resp = self.http.request(
                "GET",
                url,
                params={"service": "git-receive-pack"},
                headers={
                    "Authorization": f"Basic {basic}",
                    "User-Agent": "git/2",
                    "Accept": "*/*",
                },
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise self._fail(
                None, type(exc).__name__ + ": " + str(exc), "check push access"
            ) from exc
        if resp.status_code == 200:
            return True
        if resp.status_code in (401, 403, 404):
            return False
        raise self._fail(resp.status_code, resp.text[:200], "check push access")

    # ------------------------------------------------------------------ M14: issues

    def list_issues(
        self,
        repo: str,
        *,
        since: str | None = None,
        state: str = "open",
        per_page: int = 50,
        max_pages: int = 2,
        conditional: bool = False,
    ) -> Any:
        """Issues AND pull requests (entries with a ``pull_request`` key are PRs), newest update
        first. ``since`` is an ISO-8601 timestamp."""
        params: dict[str, Any] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
        }
        if since:
            params["since"] = since
        return self._paged(
            f"/repos/{repo}/issues",
            what="list the issues",
            params=params,
            per_page=per_page,
            max_pages=max_pages,
            conditional=conditional,
        )

    def issue_comments(
        self, repo: str, number: int, *, since: str | None = None, per_page: int = 30
    ) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/issues/{number}/comments",
            what="list the comments",
            params={"since": since} if since else None,
            per_page=per_page,
            max_pages=2,
        )

    def create_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._write(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            what="post the comment",
            json={"body": body},
        )

    def create_issue(
        self, repo: str, *, title: str, body: str = "", labels: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._write("POST", f"/repos/{repo}/issues", what="create the issue", json=payload)

    def update_issue(
        self,
        repo: str,
        number: int,
        *,
        state: str | None = None,
        labels: list[str] | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        return self._write(
            "PATCH", f"/repos/{repo}/issues/{number}", what="update the issue", json=payload
        )

    def list_labels(self, repo: str) -> list[dict[str, Any]]:
        return self._paged(f"/repos/{repo}/labels", what="list the labels", per_page=100)

    # ------------------------------------------------------------------ M14: pull requests

    def find_pull(self, repo: str, head: str, *, state: str = "open") -> dict[str, Any] | None:
        """The pull request from ``head`` (``owner:branch``), or None."""
        found = self.get_json(
            f"/repos/{repo}/pulls",
            what="look for the pull request",
            params={"head": head, "state": state, "per_page": 5},
        )
        return found[0] if isinstance(found, list) and found else None

    def create_pull(self, repo: str, *, title: str, body: str, head: str, base: str) -> dict:
        """Open a pull request. A 422 'already exists' raises with ``details['already_exists']``;
        the caller adopts the open one with ``find_pull``."""
        return self._write(
            "POST",
            f"/repos/{repo}/pulls",
            what="open the pull request",
            json={"title": title, "body": body, "head": head, "base": base},
        )

    def update_pull(
        self,
        repo: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        base: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            k: v
            for k, v in (("title", title), ("body", body), ("state", state), ("base", base))
            if v is not None
        }
        return self._write(
            "PATCH",
            f"/repos/{repo}/pulls/{number}",
            what="update the pull request",
            json=payload,
        )

    def get_pull(self, repo: str, number: int, *, conditional: bool = False) -> Any:
        """The pull request incl. ``mergeable_state``, ``head.sha``, ``merged``. GitHub computes
        ``mergeable`` lazily: ``mergeable_state`` may be ``unknown`` on the first read."""
        return self.get_json(
            f"/repos/{repo}/pulls/{number}", what="read the pull request", conditional=conditional
        )

    def merge_pull(
        self,
        repo: str,
        number: int,
        *,
        sha: str,
        method: str = "squash",
        title: str | None = None,
    ) -> dict[str, Any]:
        """Merge through the API. ``sha`` is the head the caller reviewed: GitHub answers 409 when
        the head moved, 405 when the pull request is not mergeable (protection, conflicts)."""
        payload: dict[str, Any] = {"sha": sha, "merge_method": method}
        if title:
            payload["commit_title"] = title
        try:
            return self._write(
                "PUT",
                f"/repos/{repo}/pulls/{number}/merge",
                what="merge the pull request",
                json=payload,
            )
        except ProviderError as exc:
            status = (exc.details or {}).get("status")
            if status in (405, 409):
                exc.details = {
                    **(exc.details or {}),
                    "reason": "sha_mismatch" if status == 409 else "not_mergeable",
                }
            raise

    def update_pull_branch(self, repo: str, number: int, expected_head_sha: str) -> dict[str, Any]:
        """Merge the base into the head branch on GitHub (202)."""
        return self._write(
            "PUT",
            f"/repos/{repo}/pulls/{number}/update-branch",
            what="update the pull request branch",
            json={"expected_head_sha": expected_head_sha},
        )

    def list_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/pulls/{number}/reviews", what="list the reviews", per_page=50
        )

    def list_review_comments(
        self, repo: str, number: int, *, since: str | None = None
    ) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/pulls/{number}/comments",
            what="list the review comments",
            params={"since": since} if since else None,
            per_page=50,
            max_pages=2,
        )

    # ------------------------------------------------------------------ M14: CI and alerts

    def check_runs(self, repo: str, ref: str, *, conditional: bool = False) -> Any:
        return self._paged(
            f"/repos/{repo}/commits/{ref}/check-runs",
            what="list the check runs",
            per_page=100,
            max_pages=1,
            key="check_runs",
            conditional=conditional,
        )

    def combined_status(self, repo: str, ref: str, *, conditional: bool = False) -> Any:
        return self.get_json(
            f"/repos/{repo}/commits/{ref}/status",
            what="read the commit status",
            conditional=conditional,
        )

    def workflow_runs(self, repo: str, head_sha: str) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/actions/runs",
            what="list the workflow runs",
            params={"head_sha": head_sha},
            per_page=50,
            max_pages=1,
            key="workflow_runs",
        )

    def run_jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
            what="list the workflow jobs",
            per_page=50,
            max_pages=1,
            key="jobs",
        )

    def job_log_tail(self, repo: str, job_id: int, limit: int = LOG_TAIL_BYTES) -> str:
        """The last ``limit`` bytes of a job's log, scrubbed. GitHub answers 302 to a signed URL on
        another host; that URL is fetched WITHOUT the Authorization header."""
        what = "download the job log"
        resp = self.request(
            "GET", f"/repos/{repo}/actions/jobs/{job_id}/logs", what=what, redirect_ok=True
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if not location.startswith(("https://", "http://")):
                raise self._fail(resp.status_code, "the log redirect was not a URL", what)
            try:  # the signed URL carries its own authorisation: no Authorization header here
                resp = self.http.request("GET", location, follow_redirects=False)
            except httpx.HTTPError as exc:
                raise self._fail(None, type(exc).__name__ + ": " + str(exc), what) from exc
            if resp.status_code >= 400:
                raise self._fail(resp.status_code, "the log host refused the download", what)
        return self._tail(resp.content, limit)

    @staticmethod
    def _tail(content: bytes, limit: int) -> str:
        text = content[-max(1, limit) :].decode("utf-8", "replace")
        return scrub(text) or ""

    def dependabot_alerts(self, repo: str, state: str = "open") -> list[dict[str, Any]]:
        return self._paged(
            f"/repos/{repo}/dependabot/alerts",
            what="list the Dependabot alerts",
            params={"state": state},
            per_page=50,
            max_pages=2,
        )

    # ------------------------------------------------------------------ M14: releases, refs, hooks

    def create_release(
        self,
        repo: str,
        *,
        tag: str,
        name: str | None = None,
        body: str = "",
        draft: bool = True,
        target_commitish: str | None = None,
        prerelease: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tag_name": tag,
            "name": name or tag,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
        }
        if target_commitish:
            payload["target_commitish"] = target_commitish
        return self._write(
            "POST", f"/repos/{repo}/releases", what="create the release", json=payload
        )

    def delete_ref(self, repo: str, branch: str) -> None:
        """Delete ``refs/heads/<branch>`` (only ``foundry/*`` branches are ever passed)."""
        self._write(
            "DELETE",
            f"/repos/{repo}/git/refs/heads/{quote(branch, safe='/')}",
            what="delete the branch",
        )

    def create_hook(self, repo: str, *, url: str, secret: str, events: list[str]) -> dict[str, Any]:
        self._secrets = (*self._secrets, secret)  # masked from any error text
        return self._write(
            "POST",
            f"/repos/{repo}/hooks",
            what="create the webhook",
            json={
                "name": "web",
                "active": True,
                "events": events,
                "config": {
                    "url": url,
                    "content_type": "json",
                    "secret": secret,
                    "insecure_ssl": "0",
                },
            },
        )

    def delete_hook(self, repo: str, hook_id: int | str) -> None:
        self._write("DELETE", f"/repos/{repo}/hooks/{hook_id}", what="delete the webhook")

    # ------------------------------------------------------------------ M14 WP-A additions

    def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        """One issue (or pull request: the entry then carries a ``pull_request`` key)."""
        return self.get_json(f"/repos/{repo}/issues/{number}", what="read the issue")

    def get_commit(self, repo: str, sha: str) -> dict[str, Any]:
        """One commit (``commit.committer.date`` dates a branch for the stale-branch cleanup)."""
        return self.get_json(f"/repos/{repo}/commits/{sha}", what="read the commit")

    def token_kind(self) -> str:
        """``fine_grained`` | ``classic`` | ``other``, from the token's prefix (never the token)."""
        return classify_token(self._token)


def submit_registry_pr(
    client: GithubClient,
    *,
    upstream: str,
    login: str,
    branch: str,
    file_path: str,
    file_content: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
) -> dict[str, Any]:
    """Fork, branch from the upstream default branch, commit the one file, open the pull request."""
    info = client.repo(upstream)
    if info is None:
        raise ProviderError(f"github: repository {upstream} was not found")
    base = info.get("default_branch") or "main"
    fork = client.ensure_fork(upstream, login)
    fork_name = fork["full_name"]
    client.sync_fork(fork_name, base)
    sha = client.branch_sha(upstream, base)
    client.create_branch(fork_name, branch, sha)
    client.put_file(fork_name, file_path, file_content, commit_message, branch)
    pr = client.open_pull(
        upstream, title=pr_title, body=pr_body, head=f"{login}:{branch}", base=base
    )
    return {"number": pr.get("number"), "url": pr.get("html_url"), "state": pr.get("state")}


def classify_token(token: str) -> str:
    """Token kind from its prefix: ``github_pat_`` is fine-grained, ``ghp_`` / ``gho_`` / ``ghu_`` /
    ``ghs_`` / ``ghr_`` are classic-style tokens, anything else is ``other``."""
    value = (token or "").strip()
    if value.startswith("github_pat_"):
        return "fine_grained"
    if re.match(r"^gh[pousr]_", value):
        return "classic"
    return "other"
