"""cloudflared: detection and the verified, owner-confirmed install.

No silent downloads. ``install`` refuses unless the owner confirmed, fetches the release metadata
from the official GitHub API, takes the asset only from
``https://github.com/cloudflare/cloudflared/releases/download/``, and installs it only when the
SHA-256 of the downloaded bytes equals the checksum GitHub publishes for that asset (the asset
``digest`` and/or the ``SHA256 Checksums`` block in the release notes; if none can be obtained, or
two published sources disagree, it refuses). The result goes to ``<data>/bin/cloudflared`` (0755).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import Conflict
from app.integrations.base import IntegrationHttp, ProviderError

OFFICIAL_PREFIX = "https://github.com/cloudflare/cloudflared/releases/download/"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+):\s*([0-9a-fA-F]{64})\s*$", re.MULTILINE)

INSTALL_INSTRUCTIONS = [
    "macOS: `brew install cloudflared`",
    "Linux: download the package for your distribution from "
    "https://github.com/cloudflare/cloudflared/releases and install it",
    "Windows: `winget install --id Cloudflare.cloudflared`",
    "Or let Company OS download the official release and verify its SHA-256 checksum "
    "(it asks you to confirm first).",
]


def platform_asset(system: str | None = None, machine: str | None = None) -> str | None:
    """Release asset name for this OS/CPU, or None when there is no official build."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if arch is None:
        return None
    if system == "darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    if system == "linux":
        return f"cloudflared-linux-{arch}"
    if system == "windows" and arch == "amd64":
        return "cloudflared-windows-amd64.exe"
    return None


@dataclass(frozen=True)
class BinaryInfo:
    found: bool
    path: str | None
    version: str | None


def find_cloudflared(
    settings: Settings, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    if settings.cloudflared_path is not None:
        p = Path(settings.cloudflared_path)
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    managed = settings.resolved_bin_dir / "cloudflared"
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)
    return which("cloudflared")


def binary_version(path: str, timeout: float = 10.0) -> str | None:
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"version\s+([0-9][^\s]*)", proc.stdout + proc.stderr)
    return match.group(1) if match else (proc.stdout.strip()[:40] or None)


def binary_info(settings: Settings) -> BinaryInfo:
    path = find_cloudflared(settings)
    return BinaryInfo(path is not None, path, binary_version(path) if path else None)


@dataclass(frozen=True)
class InstallPlan:
    version: str
    asset: str
    url: str
    sha256: str
    size_bytes: int | None
    checksum_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "asset": self.asset,
            "url": self.url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "checksum_sources": list(self.checksum_sources),
        }


def _published_checksums(release: dict[str, Any], asset: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:") and _SHA.match(digest[7:].lower()):
        found["github_asset_digest"] = digest[7:].lower()
    for name, value in _CHECKSUM_LINE.findall(str(release.get("body") or "")):
        if name == asset.get("name"):
            found["release_notes"] = value.lower()
    return found


def plan_install(
    settings: Settings, http: IntegrationHttp, asset_name: str | None = None
) -> InstallPlan:
    """Look up the latest official release and the published checksum for this platform. Metadata
    only: nothing is downloaded. Raises when no published checksum can be obtained."""
    asset_name = asset_name or platform_asset()
    if asset_name is None:
        raise Conflict(
            "There is no official cloudflared build for this system; install it manually"
        )
    with http.client(follow_redirects=False) as client:
        try:
            resp = client.get(
                settings.cloudflared_release_api,
                headers={"Accept": "application/vnd.github+json"},
            )
        except Exception as exc:
            raise ProviderError(
                f"cloudflared: could not reach GitHub ({type(exc).__name__})"
            ) from exc
    if resp.status_code != 200:
        raise ProviderError(f"cloudflared: the release lookup failed (HTTP {resp.status_code})")
    release = resp.json()
    asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
    if asset is None:
        raise ProviderError(f"cloudflared: release {release.get('tag_name')} has no {asset_name}")
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith(OFFICIAL_PREFIX):
        raise ProviderError("cloudflared: the download URL is not the official release location")
    sums = _published_checksums(release, asset)
    if not sums:
        raise ProviderError(
            "cloudflared: no published SHA-256 checksum could be found for this release; "
            "refusing to download. Install cloudflared manually."
        )
    if len(set(sums.values())) != 1:
        raise ProviderError("cloudflared: the published checksums disagree; refusing to download")
    return InstallPlan(
        version=str(release.get("tag_name") or ""),
        asset=asset_name,
        url=url,
        sha256=next(iter(sums.values())),
        size_bytes=asset.get("size") if isinstance(asset.get("size"), int) else None,
        checksum_sources=tuple(sorted(sums)),
    )


def install(
    settings: Settings,
    http: IntegrationHttp,
    *,
    confirm: bool,
    expected_sha256: str | None = None,
    asset_name: str | None = None,
) -> Path:
    """Download, verify and install cloudflared into ``<data>/bin``. ``confirm`` must be True (the
    owner's explicit yes); ``expected_sha256`` (what the owner was shown) must match when given."""
    if confirm is not True:
        raise Conflict(
            "Downloading cloudflared needs the owner's explicit confirmation",
            details={"reason": "confirmation_required"},
        )
    plan = plan_install(settings, http, asset_name)
    if expected_sha256 and not hmac.compare_digest(expected_sha256.lower(), plan.sha256):
        raise Conflict(
            "The published checksum is not the one you confirmed; review it and confirm again",
            details={"reason": "checksum_changed", "sha256": plan.sha256},
        )
    bin_dir = settings.resolved_bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    fd, tmp_name = tempfile.mkstemp(prefix=".cloudflared-download-", dir=bin_dir)
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            http.client(timeout=120, follow_redirects=True) as client,
            client.stream("GET", plan.url) as resp,
        ):
            if resp.status_code != 200:
                raise ProviderError(f"cloudflared: the download failed (HTTP {resp.status_code})")
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ProviderError("cloudflared: the download is larger than expected")
                digest.update(chunk)
                out.write(chunk)
        if not hmac.compare_digest(digest.hexdigest(), plan.sha256):
            raise ProviderError(
                "cloudflared: the SHA-256 of the download does not match the published checksum; "
                "nothing was installed",
                details={"expected": plan.sha256, "actual": digest.hexdigest()},
            )
        target = bin_dir / ("cloudflared.exe" if plan.asset.endswith(".exe") else "cloudflared")
        staged = bin_dir / f".{target.name}.new"
        if plan.asset.endswith(".tgz"):
            _extract_binary(tmp, staged)
        else:
            shutil.copyfile(tmp, staged)
        staged.chmod(0o755)
        os.replace(staged, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


def _extract_binary(archive: Path, dest: Path) -> None:
    """Only the member named ``cloudflared`` is read, as bytes: no path from the archive is used."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and os.path.basename(member.name) == "cloudflared":
                src = tar.extractfile(member)
                if src is None:
                    break
                with open(dest, "wb") as out:
                    shutil.copyfileobj(io.BufferedReader(src), out)
                return
    raise ProviderError("cloudflared: the archive does not contain the cloudflared binary")
