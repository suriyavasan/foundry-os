"""GitHub: the Founder's identity and the fork -> branch -> file -> pull request flow that
free-subdomain registries use. A fine-grained or classic token (``public_repo``) is enough."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

from app.integrations.base import ApiClient, ProviderError


class GithubClient(ApiClient):
    provider = "github"
    base_url = "https://api.github.com"

    def __init__(
        self,
        http,
        token: str,
        *,
        base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(http, token)
        self._token = token
        if base_url:
            self.base_url = base_url
        self._sleep = sleep
        self.scopes: list[str] = []

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

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
