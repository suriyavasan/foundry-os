"""GitHub skill source: repository search, skill discovery, pinned download.

All HTTP goes through an injectable ``httpx.Client`` (tests use ``httpx.MockTransport`` fixtures;
there is no network in tests). The optional token (``COMPANY_OS_GITHUB_TOKEN``, temporary until the
M3b credential store) is sent only to the API host and is never logged or put in an error message.
Skill content is downloaded from ``raw.githubusercontent.com`` at an immutable commit sha, so the
bytes we scan are the bytes we store. Only public repositories are supported.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from app.skills.blobstore import MAX_FILES, MAX_TOTAL_BYTES, SkillFile, SkillRejected

logger = logging.getLogger("company_os.skills.github")

API_VERSION = "2022-11-28"
RAW_BASE = "https://raw.githubusercontent.com"
USER_AGENT = "company-os-skills/0.1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SYMLINK_MODE = "120000"
_SUBMODULE_MODE = "160000"


class GithubError(Exception):
    """A GitHub request failed or returned something unusable (message is safe to show)."""


class GithubNotFound(GithubError):
    pass


class GithubRateLimited(GithubError):
    pass


@dataclass(frozen=True)
class RepoHit:
    full_name: str
    description: str | None
    stars: int
    default_branch: str | None
    license: str | None
    html_url: str


@dataclass(frozen=True)
class CodeHit:
    repo: str
    path: str  # path to SKILL.md
    description: str | None = None

    @property
    def skill_dir(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


@dataclass
class RepoInfo:
    full_name: str
    default_branch: str
    license: str | None  # SPDX id from GitHub, None when unknown
    archived: bool = False
    private: bool = False
    description: str | None = None


@dataclass
class TreeEntry:
    path: str
    mode: str
    type: str
    size: int = 0


@dataclass
class SkillDownload:
    files: list[SkillFile]
    commit_sha: str
    skill_path: str
    extras: dict[str, str] = field(default_factory=dict)


class GithubClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 20.0,
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=False)
        self._token = token or None
        self._api = api_url.rstrip("/")

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # ------------------------------------------------------------------ transport

    def _headers(self, *, api: bool) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if api:
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = API_VERSION
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, url: str, *, api: bool, params: dict | None = None) -> httpx.Response:
        try:
            resp = self._http.get(url, params=params, headers=self._headers(api=api))
        except httpx.HTTPError as exc:  # message of httpx errors can include the URL, never headers
            raise GithubError(f"GitHub is unreachable ({type(exc).__name__})") from exc
        if resp.status_code == 404:
            raise GithubNotFound("not found on GitHub (or the repository is private)")
        if resp.status_code in (403, 429):
            remaining = resp.headers.get("x-ratelimit-remaining")
            if resp.status_code == 429 or remaining == "0" or "rate limit" in resp.text.lower():
                raise GithubRateLimited(
                    "GitHub rate limit reached; set COMPANY_OS_GITHUB_TOKEN or retry later"
                )
            raise GithubError("GitHub refused the request (403)")
        if resp.status_code == 401:
            raise GithubError("GitHub rejected the configured token")
        if resp.status_code >= 400:
            raise GithubError(f"GitHub returned HTTP {resp.status_code}")
        return resp

    def _get_json(self, path: str, params: dict | None = None):
        resp = self._request(f"{self._api}{path}", api=True, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise GithubError("GitHub returned invalid JSON") from exc

    # ------------------------------------------------------------------ search

    def search_repositories(self, terms: str, *, limit: int = 10) -> list[RepoHit]:
        query = f"{terms.strip()} topic:agent-skills".strip()
        data = self._get_json(
            "/search/repositories", {"q": query, "per_page": min(limit, 30), "sort": "stars"}
        )
        hits = []
        for item in data.get("items", [])[:limit]:
            lic = (item.get("license") or {}).get("spdx_id")
            hits.append(
                RepoHit(
                    full_name=item.get("full_name", ""),
                    description=item.get("description"),
                    stars=int(item.get("stargazers_count") or 0),
                    default_branch=item.get("default_branch"),
                    license=None if lic in (None, "NOASSERTION") else lic,
                    html_url=item.get("html_url", ""),
                )
            )
        return [h for h in hits if h.full_name]

    def search_skill_files(self, terms: str, *, limit: int = 10) -> list[CodeHit]:
        """``filename:SKILL.md`` code search. GitHub requires authentication for code search."""
        if not self._token:
            raise GithubError("GitHub code search needs COMPANY_OS_GITHUB_TOKEN")
        data = self._get_json(
            "/search/code", {"q": f"{terms.strip()} filename:SKILL.md", "per_page": min(limit, 30)}
        )
        return [
            CodeHit(
                repo=(item.get("repository") or {}).get("full_name", ""),
                path=item.get("path", ""),
                description=(item.get("repository") or {}).get("description"),
            )
            for item in data.get("items", [])[:limit]
            if (item.get("repository") or {}).get("full_name") and item.get("path")
        ]

    # ------------------------------------------------------------------ repository

    def get_repo(self, owner: str, repo: str) -> RepoInfo:
        data = self._get_json(f"/repos/{quote(owner)}/{quote(repo)}")
        lic = (data.get("license") or {}).get("spdx_id")
        return RepoInfo(
            full_name=data.get("full_name", f"{owner}/{repo}"),
            default_branch=data.get("default_branch") or "main",
            license=None if lic in (None, "NOASSERTION") else lic,
            archived=bool(data.get("archived")),
            private=bool(data.get("private")),
            description=data.get("description"),
        )

    def repo_license(self, owner: str, repo: str) -> str | None:
        try:
            data = self._get_json(f"/repos/{quote(owner)}/{quote(repo)}/license")
        except GithubNotFound:
            return None
        spdx = (data.get("license") or {}).get("spdx_id")
        return None if spdx in (None, "NOASSERTION", "") else spdx

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        """Resolve a branch/tag/sha (default branch when None) to a full commit sha."""
        if ref is None:
            ref = self.get_repo(owner, repo).default_branch
        data = self._get_json(f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(ref, safe='/')}")
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            raise GithubError("GitHub returned an unexpected commit")
        return sha

    def list_tree(self, owner: str, repo: str, commit_sha: str) -> list[TreeEntry]:
        data = self._get_json(
            f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{commit_sha}", {"recursive": "1"}
        )
        if data.get("truncated"):
            raise GithubError("repository tree is too large to scan for skills")
        return [
            TreeEntry(
                path=e["path"],
                mode=str(e.get("mode", "")),
                type=e.get("type", ""),
                size=e.get("size") or 0,
            )
            for e in data.get("tree", [])
            if "path" in e
        ]

    def list_skill_paths(self, owner: str, repo: str, commit_sha: str) -> list[str]:
        """Directories (relative to the repo root; "" = root) that contain a SKILL.md."""
        paths = []
        for e in self.list_tree(owner, repo, commit_sha):
            if e.type == "blob" and (e.path == "SKILL.md" or e.path.endswith("/SKILL.md")):
                paths.append(e.path[: -len("SKILL.md")].rstrip("/"))
        return sorted(paths)

    # ------------------------------------------------------------------ download

    def download_skill(
        self, owner: str, repo: str, commit_sha: str, skill_path: str
    ) -> SkillDownload:
        """Download one skill directory at an exact commit. Limits are checked on the tree
        listing BEFORE any content is fetched. Symlinks and submodules are refused."""
        if not _SHA_RE.match(commit_sha):
            raise GithubError("download requires a full commit sha")
        prefix = f"{skill_path}/" if skill_path else ""
        entries = [
            e
            for e in self.list_tree(owner, repo, commit_sha)
            if e.type in ("blob", "commit") and (not prefix or e.path.startswith(prefix))
        ]
        if not any(e.path == f"{prefix}SKILL.md" for e in entries):
            raise GithubNotFound("no SKILL.md at that path")
        total = 0
        for e in entries:
            rel = e.path[len(prefix) :]
            if e.mode == _SYMLINK_MODE:
                raise SkillRejected(f"symlinks are not allowed: {rel}")
            if e.mode == _SUBMODULE_MODE or e.type == "commit":
                raise SkillRejected(f"submodules are not allowed: {rel}")
            total += e.size
        if len(entries) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise SkillRejected("skill exceeds the size or file-count limits")
        files: list[SkillFile] = []
        for e in entries:
            rel = e.path[len(prefix) :]
            url = f"{RAW_BASE}/{quote(owner)}/{quote(repo)}/{commit_sha}/{quote(e.path)}"
            resp = self._request(url, api=False)
            if len(resp.content) > MAX_TOTAL_BYTES:
                raise SkillRejected("skill exceeds the size or file-count limits")
            files.append(SkillFile(rel, resp.content, executable=e.mode == "100755"))
        return SkillDownload(files=files, commit_sha=commit_sha, skill_path=skill_path)
