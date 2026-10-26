"""Is there a newer release? One request a day to the GitHub releases API, cached, switch-off-able.

* What is sent: a ``GET https://api.github.com/repos/suriyavasan/foundry-os/releases/latest`` with a
  ``User-Agent: foundry-os/<your version>``. Nothing else, nothing identifying beyond what any HTTP
  request reveals to GitHub. No telemetry anywhere else.
* The answer is cached in ``<data dir>/state/update-check.json`` for 24 hours (1 hour after a failed
  check, so an offline machine does not retry on every page load).
* Off switches: ``FOUNDRYOS_UPDATE_CHECK=0`` (environment), ``COMPANY_OS_UPDATE_CHECK=0``, or the owner's
  persistent preference stored in the same cache file (Settings in the web app, or
  ``foundryos update --no-auto-check``). ``foundryos update --check`` always asks, because you asked.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core import install
from app.core.config import Settings

CACHE_NAME = "update-check.json"
TTL = timedelta(hours=24)
ERROR_TTL = timedelta(hours=1)
TIMEOUT = 3.0


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"foundry-os/{install.APP_VERSION}",
        },
    )


# Tests (and nothing else) replace this.
http_client_factory: Callable[[], httpx.Client] = make_client


# ------------------------------------------------------------------------------ versions


def parse_version(text: str) -> tuple[tuple[int, ...], tuple[int, str]] | None:
    """``0.2.0``, ``v1.2.3-rc.1`` ... -> a sortable key (final releases sort after their pre-releases)."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z.\-]+))?(?:\+.*)?", text.strip())
    if not m:
        return None
    nums = tuple(int(p) for p in m.group(1).split("."))
    nums += (0,) * (4 - len(nums))
    pre = m.group(2)
    return nums, ((0, pre) if pre else (1, ""))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def is_newer_minor(candidate: str, current: str) -> bool:
    """Is ``candidate`` a newer major.minor than ``current``? Patch releases do not count."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a[0][:2] > b[0][:2]


def update_is_available(candidate: str | None, current: str, kind: str) -> bool:
    """Is a release worth telling this installation about?

    Every green push to main is released as a new patch version, so a source checkout (which is usually
    at some commit of main, not at a tag) would be told about an update daily. It hears about a release
    only when the major.minor is newer; the other kinds are told about every newer version.
    """
    if not candidate:
        return False
    if kind == install.SOURCE:
        return is_newer_minor(candidate, current)
    return is_newer(candidate, current)


# ------------------------------------------------------------------------------ cache


def cache_path(settings: Settings) -> Path:
    return settings.resolved_data_dir / "state" / CACHE_NAME


def load_cache(settings: Settings) -> dict[str, Any]:
    try:
        data = json.loads(cache_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(settings: Settings, data: dict[str, Any]) -> None:
    path = cache_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(text)
    return None


def preference_enabled(settings: Settings) -> bool:
    """Is the daily check switched on (environment/setting AND the owner's stored preference)?"""
    if not settings.update_check:
        return False
    return load_cache(settings).get("enabled", True) is not False


def set_preference(settings: Settings, enabled: bool) -> None:
    data = load_cache(settings)
    data["enabled"] = bool(enabled)
    save_cache(settings, data)


# ------------------------------------------------------------------------------ the check


def update_command(kind: str | None = None) -> str:
    kind = kind or install.install_kind()
    return {
        install.INSTALLER: "foundryos update",
        install.HOMEBREW: "brew upgrade foundry-os && brew services restart foundry-os",
        install.DOCKER: "docker pull ghcr.io/suriyavasan/foundry-os:latest",
        install.SOURCE: "git pull && (cd backend && uv sync) && foundryos restart",
    }.get(kind, "download the new release from GitHub and replace this copy")


def fetch_latest(settings: Settings, client: httpx.Client | None = None) -> dict[str, Any]:
    """{version, url, name} of the latest release, or {} when the repository has none yet."""
    own = client is None
    client = client or http_client_factory()
    try:
        resp = client.get(
            f"{settings.github_api_url.rstrip('/')}/repos/{install.GITHUB_REPO}/releases/latest"
        )
    finally:
        if own:
            client.close()
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    body = resp.json()
    tag = str(body.get("tag_name") or "").strip()
    if not tag or body.get("draft") or body.get("prerelease"):
        return {}
    version = tag[1:] if tag.startswith("v") else tag
    if parse_version(version) is None:
        return {}
    url = body.get("html_url")
    return {
        "version": version,
        # the UI links to it: only a plain https address is ever passed on
        "url": url if isinstance(url, str) and url.startswith("https://") else None,
        "name": body.get("name"),
    }


def status(
    settings: Settings,
    *,
    refresh: bool | str = "if-stale",
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The update status document.

    ``refresh``: ``"never"`` reads the cache only (doctor, status); ``"if-stale"`` asks GitHub when the
    cache is older than a day and the check is enabled (the web app, the daily check); ``"always"``
    asks now even if the check is switched off (the user typed ``update --check``).
    """
    now = now or _now()
    enabled = preference_enabled(settings)
    cache = load_cache(settings)
    error = cache.get("error")
    should = False
    if refresh == "always":
        should = True
    elif refresh == "if-stale" and enabled:
        checked = _parse_time(cache.get("checked_at"))
        ttl = ERROR_TTL if cache.get("error") else TTL
        should = checked is None or now - checked >= ttl
    if should:
        try:
            latest = fetch_latest(settings, client)
            cache.update(
                checked_at=now.isoformat(),
                latest_version=latest.get("version"),
                release_url=latest.get("url"),
                error=None,
            )
            error = None
        except (httpx.HTTPError, ValueError) as exc:
            error = f"{exc.__class__.__name__}: {exc}"[:300]
            cache.update(checked_at=now.isoformat(), error=error)
        with contextlib.suppress(OSError):
            save_cache(settings, cache)
    current = install.APP_VERSION
    latest_version = cache.get("latest_version")
    kind = install.install_kind()
    return {
        "enabled": enabled,
        "current_version": current,
        "latest_version": latest_version,
        "update_available": bool(
            (enabled or refresh == "always") and update_is_available(latest_version, current, kind)
        ),
        "checked_at": cache.get("checked_at"),
        "release_url": cache.get("release_url"),
        "install_kind": kind,
        "update_command": update_command(kind),
        "error": error,
    }
