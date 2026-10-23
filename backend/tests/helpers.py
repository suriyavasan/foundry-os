"""Shared helpers for work-engine tests."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.core.time import utcnow


class FakeClock:
    """Injectable clock. Starts at the real "now" (so period math matches wall-clock queries)
    and only moves when a test advances it."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = (start or utcnow()).replace(microsecond=0)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float = 0, **kwargs) -> datetime:
        with self._lock:
            self._now += timedelta(seconds=seconds, **kwargs)
            return self._now

    def set(self, value: datetime) -> None:
        with self._lock:
            self._now = value.astimezone(UTC)


def api(cid: str, path: str = "") -> str:
    return f"/api/companies/{cid}{path}"


def new_agent(
    client: TestClient,
    cid: str,
    *,
    name: str = "Nova",
    runtime: str = "scripted",
    script: dict[str, Any] | None = None,
    activate: bool = True,
    **overrides,
) -> dict:
    body: dict[str, Any] = {"name": name, "role_title": "Engineer", "runtime": runtime, **overrides}
    if script is not None:
        body["runtime_config"] = {"script": script}
    resp = client.post(api(cid, "/agents"), json=body)
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    if activate:
        r = client.post(api(cid, f"/agents/{agent['id']}/activate"))
        assert r.status_code == 200, r.text
        agent = r.json()
    return agent


def new_task(client: TestClient, cid: str, **overrides) -> dict:
    body = {"title": "Do the thing", **overrides}
    resp = client.post(api(cid, "/tasks"), json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def get_task(client: TestClient, cid: str, task_id: str) -> dict:
    resp = client.get(api(cid, f"/tasks/{task_id}"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def settle(scheduler, worker, *, rounds: int = 12) -> None:
    """Drive dispatch + execution until nothing more happens (deterministic, no sleeping)."""
    for _ in range(rounds):
        report = scheduler.tick()
        processed = worker.drain()
        if not report.dispatch.queued_run_ids and not processed:
            return


def set_policy(client: TestClient, cid: str, **fields) -> dict:
    resp = client.patch(api(cid, "/policy"), json=fields)
    assert resp.status_code == 200, resp.text
    return resp.json()


def wait_until(
    predicate, *, timeout: float = 5.0, interval: float = 0.005, what: str = "condition"
):
    """Poll a cheap in-process condition (no fixed sleeps)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def run_in_thread(fn, *args) -> threading.Thread:
    thread = threading.Thread(target=fn, args=args, daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------ skills & HR helpers


def write_skill(
    root,
    slug: str,
    *,
    tags: list[str] | None = None,
    description: str | None = None,
    license: str | None = "MIT",
    version: str = "1.0.0",
    body: str = "# Skill\n\n1. Do the thing.\n",
    files: dict[str, str] | None = None,
    category: str | None = None,
):
    """Write ``<root>/<slug>/SKILL.md`` (+ extra files) and return the skill directory."""
    from pathlib import Path

    skill_dir = Path(root) / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    meta = [
        f"name: {slug}",
        f'description: "{description or f"The {slug} skill."}"',
        f"version: {version}",
    ]
    if license:
        meta.append(f"license: {license}")
    meta.append("metadata:\n  hermes:\n    tags: [" + ", ".join(tags or [slug]) + "]")
    if category:
        meta[-1] += f"\n    category: {category}"
    (skill_dir / "SKILL.md").write_text("---\n" + "\n".join(meta) + "\n---\n\n" + body)
    for rel, content in (files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return skill_dir


class FakeScanner:
    """Stands in for the runtime's skills guard: verdict per skill name (default ``safe``)."""

    def __init__(self, verdicts: dict[str, str] | None = None, *, available: bool = True) -> None:
        self.verdicts = dict(verdicts or {})
        self._available = available
        self.calls: list[tuple[str, str]] = []  # (skill name, guard source)

    def available(self) -> bool:
        return self._available

    def scan(self, path, *, source: str):
        from app.skills.frontmatter import split_frontmatter
        from app.skills.scan import ScanReport

        fm, _ = split_frontmatter((path / "SKILL.md").read_text())
        name = str(fm.get("name"))
        self.calls.append((name, source))
        verdict = self.verdicts.get(name, "safe")
        findings = (
            []
            if verdict == "safe"
            else [
                {
                    "category": "exfiltration",
                    "pattern": "fake_pattern",
                    "file": "SKILL.md",
                    "line": 3,
                    "severity": "critical" if verdict == "dangerous" else "high",
                    "description": "fake finding",
                }
            ]
        )
        return ScanReport(
            verdict=verdict, findings=findings, scanner_version="fake-1", summary=verdict
        )


class FakeGithub:
    """Recorded-fixture stand-in for api.github.com + raw.githubusercontent.com (no network)."""

    def __init__(self) -> None:
        self.repos: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict]] = []  # (host, path, headers)
        self.rate_limited = False
        self.search_items: list[dict] = []
        self.code_items: list[dict] = []

    def add_repo(
        self,
        full_name: str,
        files: dict[str, str | bytes],
        *,
        license: str | None = "MIT",
        commit: str | None = None,
        modes: dict[str, str] | None = None,
        default_branch: str = "main",
    ) -> str:
        import hashlib

        sha = commit or hashlib.sha1(full_name.encode() + repr(sorted(files)).encode()).hexdigest()
        self.repos[full_name.lower()] = {
            "full_name": full_name,
            "default_branch": default_branch,
            "license": license,
            "commit": sha,
            "files": {p: (c.encode() if isinstance(c, str) else c) for p, c in files.items()},
            "modes": modes or {},
        }
        return sha

    def client(self, *, token: str | None = None):
        import httpx

        from app.skills.github import GithubClient

        return GithubClient(
            httpx.Client(transport=httpx.MockTransport(self.handler)),
            token=token,
            api_url="https://api.github.com",
        )

    def handler(self, request):
        import httpx

        host, path = request.url.host, request.url.path
        self.requests.append((host, path, dict(request.headers)))
        if self.rate_limited:
            return httpx.Response(403, json={"message": "API rate limit exceeded"})
        if host == "raw.githubusercontent.com":
            _, owner, repo, sha, *rest = path.split("/")
            data = self.repos.get(f"{owner}/{repo}".lower())
            rel = "/".join(rest)
            if not data or sha != data["commit"] or rel not in data["files"]:
                return httpx.Response(404)
            return httpx.Response(200, content=data["files"][rel])
        if path == "/search/repositories":
            return httpx.Response(200, json={"items": self.search_items})
        if path == "/search/code":
            return httpx.Response(200, json={"items": self.code_items})
        parts = path.strip("/").split("/")
        if parts[0] != "repos" or len(parts) < 3:
            return httpx.Response(404)
        data = self.repos.get(f"{parts[1]}/{parts[2]}".lower())
        if data is None:
            return httpx.Response(404, json={"message": "Not Found"})
        tail = parts[3:]
        if not tail:
            lic = {"spdx_id": data["license"]} if data["license"] else None
            return httpx.Response(
                200,
                json={
                    "full_name": data["full_name"],
                    "default_branch": data["default_branch"],
                    "license": lic,
                    "private": False,
                    "archived": False,
                },
            )
        if tail == ["license"]:
            if not data["license"]:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"license": {"spdx_id": data["license"]}})
        if tail[0] == "commits":
            ref = "/".join(tail[1:])
            if ref in (data["default_branch"], data["commit"], "v1"):
                return httpx.Response(200, json={"sha": data["commit"]})
            return httpx.Response(404, json={"message": "No commit found"})
        if tail[:2] == ["git", "trees"]:
            if tail[2] != data["commit"]:
                return httpx.Response(404)
            tree = [
                {
                    "path": p,
                    "type": "blob",
                    "mode": data["modes"].get(p, "100644"),
                    "size": len(c),
                }
                for p, c in sorted(data["files"].items())
            ]
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        return httpx.Response(404)


GH_SKILL_MD = (
    "---\nname: {name}\ndescription: A {name} skill.\n{extra}metadata:\n  hermes:\n    tags: [{tags}]\n---\n\n"
    "# {name}\n\n1. Do it.\n"
)


def gh_skill_md(name: str, *, tags: str = "pdf, documents", license: str | None = None) -> str:
    extra = f"license: {license}\n" if license else ""
    return GH_SKILL_MD.format(name=name, tags=tags, extra=extra)


def agent_of_family(client: TestClient, cid: str, family: str) -> dict:
    items = client.get(api(cid, "/agents")).json()["items"]
    return next(a for a in items if a["role_family"] == family)


def seed_library(client: TestClient, cid: str) -> dict:
    resp = client.post(api(cid, "/skills/seed"), params={"wait": "true"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def skills_by_slug(client: TestClient, cid: str) -> dict[str, dict]:
    items = client.get(api(cid, "/skills"), params={"limit": 200}).json()["items"]
    return {s["slug"]: s for s in items}
