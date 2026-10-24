"""Vercel: projects, deployments and aliases (REST API, bearer token).

The hosted control UI is the built ``web/dist`` deployed as a static site. A generated
``vercel.json`` rewrites ``/api/*`` to the public URL of this device so the browser only ever talks
to the Vercel origin (cookies stay first-party) and the SPA falls back to ``index.html``.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.errors import ValidationFailed
from app.integrations.base import ApiClient, ProviderError

VERCEL_JSON = "vercel.json"
_NAME = re.compile(r"[^a-z0-9._-]+")


def project_name_for(company_name: str) -> str:
    """A valid Vercel project name (lowercase, <= 100 chars, no '---')."""
    slug = _NAME.sub("-", company_name.lower()).strip("-.") or "company"
    slug = re.sub(r"-{3,}", "--", slug)
    return f"{slug[:60]}-control"


def normalize_proxy_target(url: str) -> str:
    """The public https origin the hosted UI forwards ``/api/*`` to (no path, no trailing slash)."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationFailed(
            "The hosted UI needs a public https URL to forward /api to (start a tunnel or "
            "configure a domain first)"
        )
    if parsed.hostname in ("localhost", "127.0.0.1", "::1") or parsed.hostname.endswith(".local"):
        raise ValidationFailed("The hosted UI cannot forward to a local address")
    return f"https://{parsed.netloc}"


def build_vercel_json(proxy_target: str) -> dict[str, Any]:
    """``/api/*`` -> the public tunnel/domain; everything else that is not a file -> the SPA."""
    origin = normalize_proxy_target(proxy_target)
    return {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "cleanUrls": True,
        "rewrites": [
            {"source": "/api/:path*", "destination": f"{origin}/api/:path*"},
            {"source": "/((?!api/).*)", "destination": "/index.html"},
        ],
    }


def collect_dist(dist: Path, proxy_target: str, *, max_bytes: int) -> list[dict[str, str]]:
    """Inline file list for ``POST /v13/deployments`` (base64). Refuses a missing build, a build
    without ``index.html``, a build over ``max_bytes`` or a ``vercel.json`` shipped inside it (the
    generated one is the only one: nothing in the build may redirect ``/api`` elsewhere)."""
    if not dist.is_dir():
        raise ValidationFailed(
            f"The web build was not found at {dist}. Run `pnpm build` in web/ first.",
            details={"reason": "dist_missing", "path": str(dist)},
        )
    if not (dist / "index.html").is_file():
        raise ValidationFailed("The web build has no index.html", details={"reason": "dist_empty"})
    files: list[dict[str, str]] = []
    total = 0
    root = dist.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == VERCEL_JSON or rel.startswith("."):
            continue
        data = path.read_bytes()
        total += len(data)
        if total > max_bytes:
            raise ValidationFailed(
                "The web build is larger than the hosted-UI limit",
                details={"reason": "dist_too_large"},
            )
        files.append({"file": rel, "data": base64.b64encode(data).decode(), "encoding": "base64"})
    config = json.dumps(build_vercel_json(proxy_target), indent=2).encode()
    files.append(
        {"file": VERCEL_JSON, "data": base64.b64encode(config).decode(), "encoding": "base64"}
    )
    return files


class VercelClient(ApiClient):
    provider = "vercel"
    base_url = "https://api.vercel.com"

    def __init__(self, http, token: str, team_id: str | None = None) -> None:
        super().__init__(http, token)
        self._token = token
        self.team_id = team_id

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _team(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        out = dict(params or {})
        if self.team_id:
            out["teamId"] = self.team_id
        return out

    def user(self) -> dict[str, Any]:
        body = self.json("GET", "/v2/user", what="read the account")
        return body.get("user", body) if isinstance(body, dict) else {}

    def teams(self) -> list[dict[str, Any]]:
        body = self.json("GET", "/v2/teams", what="list teams", params={"limit": 20})
        return list(body.get("teams", [])) if isinstance(body, dict) else []

    def projects(self, search: str | None = None) -> list[dict[str, Any]]:
        params = self._team({"limit": 20})
        if search:
            params["search"] = search
        body = self.json("GET", "/v9/projects", what="list projects", params=params)
        return list(body.get("projects", [])) if isinstance(body, dict) else []

    def project(self, name: str) -> dict[str, Any] | None:
        try:
            return self.json(
                "GET", f"/v9/projects/{name}", what="read the project", params=self._team()
            )
        except ProviderError as exc:
            if (exc.details or {}).get("status") == 404:
                return None
            raise

    def project_create(self, name: str) -> dict[str, Any]:
        return self.json(
            "POST",
            "/v11/projects",
            what="create the project",
            params=self._team(),
            json={"name": name, "framework": None},
        )

    def deploy(self, name: str, files: list[dict[str, str]]) -> dict[str, Any]:
        body = {
            "name": name,
            "files": files,
            "target": "production",
            "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None},
        }
        return self.json(
            "POST",
            "/v13/deployments",
            what="create the deployment",
            params=self._team({"skipAutoDetectionConfirmation": 1}),
            json=body,
        )

    def deployment(self, deployment_id: str) -> dict[str, Any]:
        return self.json(
            "GET",
            f"/v13/deployments/{deployment_id}",
            what="read the deployment",
            params=self._team(),
        )

    def add_domain(self, project: str, domain: str) -> dict[str, Any]:
        return self.json(
            "POST",
            f"/v10/projects/{project}/domains",
            what="add the domain",
            params=self._team(),
            json={"name": domain},
        )
