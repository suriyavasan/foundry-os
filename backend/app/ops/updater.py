"""``foundryos update`` and ``foundryos rollback`` for installer installs.

Layout (made by ``install.sh``)::

    ~/.foundry-os/versions/<v>/      one unpacked release bundle per version
    ~/.foundry-os/current            symlink -> versions/<v>   (what the commands and the service run)
    ~/.foundry-os/previous           symlink -> versions/<v-1> (written by this module; what rollback uses)

An update downloads ``foundry-os-<v>-<platform>.tar.gz`` and ``SHA256SUMS`` from the same release,
refuses unless the tarball's SHA-256 matches the entry in ``SHA256SUMS``, unpacks into a staging folder,
checks the bundle, moves it to ``versions/<v>`` and then switches ``current`` with one atomic rename. The
previous version stays on disk. Nothing is switched unless every earlier step succeeded.

Homebrew installs, source checkouts, Docker and unmanaged bundles are never modified here: the caller
prints the right instruction (:func:`other_install_instructions`).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core import install
from app.core.config import Settings
from app.ops import updatecheck

MIN_FREE_BYTES = 1024**3


class UpdateError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def make_download_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, read=300.0),
        follow_redirects=True,
        headers={"User-Agent": f"foundry-os/{install.APP_VERSION}"},
    )


# Tests replace this (and ``updatecheck.http_client_factory`` for the API).
download_client_factory: Callable[[], httpx.Client] = make_download_client


@dataclass
class Layout:
    prefix: Path

    @property
    def versions(self) -> Path:
        return self.prefix / "versions"

    @property
    def current(self) -> Path:
        return self.prefix / "current"

    @property
    def previous(self) -> Path:
        return self.prefix / "previous"

    def version_dir(self, version: str) -> Path:
        return self.versions / version


def layout() -> Layout | None:
    prefix = install.installer_prefix()
    return Layout(prefix) if prefix is not None else None


def _link_version(link: Path) -> str | None:
    """The version a ``current`` / ``previous`` symlink points at (``versions/<v>`` -> ``<v>``)."""
    try:
        target = os.readlink(link)
    except OSError:
        return None
    return Path(target).name or None


def current_version(lay: Layout) -> str | None:
    return _link_version(lay.current)


def installed_versions(lay: Layout) -> list[str]:
    try:
        return sorted(
            p.name for p in lay.versions.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    except OSError:
        return []


def is_valid_bundle(path: Path, version: str | None = None) -> bool:
    try:
        text = (path / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return (path / "bin" / "foundryos").exists() and (version is None or text == version)


# ------------------------------------------------------------------------------ releases


@dataclass
class Release:
    version: str
    tarball_name: str
    tarball_url: str
    sums_url: str
    html_url: str | None = None


def parse_sums(text: str) -> dict[str, str]:
    """``SHA256SUMS``: ``<64 hex>  <file>`` (``sha256sum`` and ``shasum -a 256`` formats)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if m:
            out[Path(m.group(2)).name] = m.group(1).lower()
    return out


def resolve_release(
    settings: Settings,
    version: str | None,
    platform: str,
    *,
    client: httpx.Client | None = None,
) -> Release:
    """Find the release (latest, or ``version``) and the two files we need from it."""
    wanted = version[1:] if version and version.startswith("v") else version
    base = os.environ.get("FOUNDRYOS_DOWNLOAD_BASE", "").strip().rstrip("/")
    own = client is None
    client = client or updatecheck.http_client_factory()
    try:
        body: dict[str, Any] = {}
        if not (base and wanted):
            api = settings.github_api_url.rstrip("/")
            path = f"releases/tags/v{wanted}" if wanted else "releases/latest"
            try:
                resp = client.get(f"{api}/repos/{install.GITHUB_REPO}/{path}")
            except httpx.HTTPError as exc:
                raise UpdateError(
                    f"Could not reach GitHub to look up the release ({exc.__class__.__name__}). "
                    "Check your internet connection and try again.",
                    code="network",
                ) from exc
            if resp.status_code == 404:
                raise UpdateError(
                    f"There is no release {wanted!r} of Foundry AI OS."
                    if wanted
                    else "There is no published release of Foundry AI OS yet.",
                    code="not_found",
                )
            try:
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise UpdateError(
                    f"GitHub answered the release lookup with an error ({exc}).", code="network"
                ) from exc
    finally:
        if own:
            client.close()
    if not wanted:
        tag = str(body.get("tag_name") or "").lstrip("v")
        if not tag or updatecheck.parse_version(tag) is None:
            raise UpdateError(
                "GitHub returned a release without a usable version.", code="bad_release"
            )
        wanted = tag
    elif updatecheck.parse_version(wanted) is None:
        raise UpdateError(
            f"{wanted!r} is not a version number (expected 1.2.3).", code="bad_version"
        )
    name = f"foundry-os-{wanted}-{platform}.tar.gz"
    if base:
        return Release(wanted, name, f"{base}/{name}", f"{base}/SHA256SUMS", body.get("html_url"))
    assets = {a.get("name"): a.get("browser_download_url") for a in body.get("assets", [])}
    if name not in assets:
        raise UpdateError(
            f"Release {wanted} has no build for this computer ({platform}); expected {name}.",
            code="no_asset",
        )
    if "SHA256SUMS" not in assets:
        raise UpdateError(
            f"Release {wanted} has no SHA256SUMS file, so the download cannot be verified. "
            "Refusing to install it.",
            code="no_checksums",
        )
    for url in (assets[name], assets["SHA256SUMS"]):
        if urlparse(str(url)).scheme != "https":
            raise UpdateError(
                "The release points at a download that is not https.", code="bad_release"
            )
    return Release(wanted, name, assets[name], assets["SHA256SUMS"], body.get("html_url"))


# ------------------------------------------------------------------------------ download + verify


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(partial(fh.read, 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_file(url: str, dest: Path, *, client: httpx.Client | None = None) -> None:
    try:
        if url.startswith("file://"):
            shutil.copyfile(urlparse(url).path, dest)
            return
        own = client is None
        client = client or download_client_factory()
        try:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as out:
                    for chunk in resp.iter_bytes(1024 * 256):
                        out.write(chunk)
        finally:
            if own:
                client.close()
    except (httpx.HTTPError, OSError) as exc:
        raise UpdateError(f"Could not download {url}: {exc}", code="download") from exc


def download_verified(release: Release, workdir: Path, say: Callable[[str], None]) -> Path:
    """Download the tarball + SHA256SUMS into ``workdir`` and verify. Raises on any doubt."""
    sums = workdir / "SHA256SUMS"
    say("Downloading the checksums...")
    fetch_file(release.sums_url, sums)
    expected = parse_sums(sums.read_text(encoding="utf-8", errors="replace")).get(
        release.tarball_name
    )
    if not expected:
        raise UpdateError(
            f"SHA256SUMS has no entry for {release.tarball_name}. Refusing to install it.",
            code="no_checksum_entry",
        )
    tarball = workdir / release.tarball_name
    say(f"Downloading {release.tarball_name}...")
    fetch_file(release.tarball_url, tarball)
    actual = _sha256(tarball)
    if actual != expected:
        with contextlib.suppress(OSError):
            tarball.unlink()
        raise UpdateError(
            f"The download does not match its published checksum (expected {expected[:16]}..., "
            f"got {actual[:16]}...). It may be damaged or tampered with. Nothing was installed.",
            code="checksum_mismatch",
        )
    say("Checksum verified (SHA-256).")
    return tarball


def extract_bundle(tarball: Path, staging: Path, version: str) -> Path:
    """Unpack into ``staging`` and return the bundle folder; refuse anything that is not a bundle."""
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            for member in tar.getmembers():
                top = member.name.split("/", 1)[0]
                if top != "foundry-os":
                    raise UpdateError(
                        "The archive does not look like a Foundry AI OS bundle.", code="bad_bundle"
                    )
            tar.extractall(staging, filter="data")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise UpdateError(f"The archive could not be unpacked: {exc}", code="bad_bundle") from exc
    bundle = staging / "foundry-os"
    if not is_valid_bundle(bundle, version):
        raise UpdateError(
            f"The archive is not a complete Foundry AI OS {version} bundle (VERSION or bin/foundryos "
            "is missing or different).",
            code="bad_bundle",
        )
    return bundle


# ------------------------------------------------------------------------------ switching


def _atomic_symlink(link: Path, target: str) -> None:
    tmp = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    with contextlib.suppress(OSError):
        tmp.unlink()
    os.symlink(target, tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def switch_current(lay: Layout, version: str) -> str | None:
    """Point ``current`` at ``versions/<version>`` in one atomic rename. Returns the old version."""
    if lay.current.exists() and not lay.current.is_symlink():
        raise UpdateError(
            f"{lay.current} is a real folder, not a link made by the installer; refusing to replace it.",
            code="not_managed",
        )
    old = current_version(lay)
    _atomic_symlink(lay.current, f"versions/{version}")
    if old and old != version:
        _atomic_symlink(lay.previous, f"versions/{old}")
    return old


def prune(lay: Layout, say: Callable[[str], None] = lambda _m: None) -> list[str]:
    """Keep ``current`` and ``previous``; remove other versions and failed staging folders."""
    keep = {current_version(lay), _link_version(lay.previous)}
    removed = []
    for name in installed_versions(lay):
        if name not in keep:
            shutil.rmtree(lay.version_dir(name), ignore_errors=True)
            removed.append(name)
    for stale in lay.versions.glob(".incoming-*"):
        shutil.rmtree(stale, ignore_errors=True)
    if removed:
        say("Removed older versions: " + ", ".join(removed))
    return removed


# ------------------------------------------------------------------------------ update


@dataclass
class UpdateResult:
    from_version: str | None
    to_version: str
    switched: bool
    already_current: bool = False
    release_url: str | None = None

    def as_dict(self) -> dict:
        return {
            "from": self.from_version,
            "to": self.to_version,
            "switched": self.switched,
            "already_current": self.already_current,
            "release_url": self.release_url,
        }


def update(
    settings: Settings,
    say: Callable[[str], None] = print,
    *,
    version: str | None = None,
    platform: str | None = None,
) -> UpdateResult:
    lay = layout()
    if lay is None:
        raise UpdateError(
            "This copy of Foundry AI OS was not installed by the installer.", code="not_managed"
        )
    platform = platform or install.platform_tag()
    running = current_version(lay) or install.APP_VERSION
    say("Looking up the release...")
    release = resolve_release(settings, version, platform)
    if release.version == running:
        return UpdateResult(running, running, False, True, release.html_url)
    target = lay.version_dir(release.version)
    if is_valid_bundle(target, release.version):
        say(f"Version {release.version} is already unpacked in {target}.")
    else:
        lay.versions.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(lay.versions).free
        if free < MIN_FREE_BYTES:
            raise UpdateError(
                f"Only {free // 1024**2} MB free in {lay.versions}; an update needs about 1 GB.",
                code="disk_space",
            )
        staging = lay.versions / f".incoming-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            tarball = download_verified(release, staging, say)
            say("Unpacking...")
            unpacked = staging / "unpack"
            unpacked.mkdir()
            bundle = extract_bundle(tarball, unpacked, release.version)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(bundle, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    old = switch_current(lay, release.version)
    say(f"Switched to Foundry AI OS {release.version}" + (f" (was {old})." if old else "."))
    return UpdateResult(old, release.version, True, False, release.html_url)


# ------------------------------------------------------------------------------ rollback


def rollback_target(lay: Layout) -> str | None:
    cur = current_version(lay)
    prev = _link_version(lay.previous)
    if prev and prev != cur and is_valid_bundle(lay.version_dir(prev)):
        return prev
    others = [
        v for v in installed_versions(lay) if v != cur and is_valid_bundle(lay.version_dir(v))
    ]
    if not others:
        return None
    return max(others, key=lambda v: lay.version_dir(v).stat().st_mtime)


_REVISION = re.compile(r"""^revision(?:\s*:\s*[\w\[\]| .]+)?\s*=\s*["']([^"']+)["']""", re.M)


def revisions_in(versions: Path) -> set[str] | None:
    """Alembic revision ids declared by the migration files in ``versions`` (read, never executed)."""
    if not versions.is_dir():
        return None
    found: set[str] = set()
    for path in versions.glob("*.py"):
        try:
            m = _REVISION.search(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if m:
            found.add(m.group(1))
    return found or None


def known_revisions(bundle: Path) -> set[str] | None:
    """The revision ids the migrations shipped in ``bundle`` know."""
    return revisions_in(bundle / "app" / "backend" / "alembic" / "versions")


def data_compatible_with(settings: Settings, bundle: Path) -> bool | None:
    """Can the code in ``bundle`` open the database as it is now? None when that cannot be told."""
    from app.core.config import sqlite_file_path
    from app.ops.backup import _alembic_revision

    db = sqlite_file_path(settings.resolved_database_url)
    if db is None or not db.exists():
        return True
    revision = _alembic_revision(db)
    if revision is None:
        return True
    known = known_revisions(bundle)
    if known is None:
        return None
    return revision in known


def latest_pre_upgrade_backup(settings: Settings, version: str) -> Path | None:
    """The newest ``pre-<version>-*`` safety backup: the data as it was before upgrading to ``version``."""
    folder = settings.resolved_data_dir / "backups"
    found = sorted(folder.glob(f"pre-{version}-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def other_install_instructions(kind: str) -> str:
    if kind == install.HOMEBREW:
        return (
            "This copy was installed with Homebrew, so Homebrew updates it:\n"
            "  brew upgrade foundry-os && brew services restart foundry-os\n"
            "(drop the second command if you do not use `brew services`)"
        )
    if kind == install.SOURCE:
        return (
            "This is a source checkout, so you update it with git:\n"
            "  foundryos backup create\n"
            "  git pull\n"
            "  cd backend && uv sync\n"
            "  foundryos restart        # migrates the database to the new version on start"
        )
    if kind == install.DOCKER:
        return (
            "This runs in Docker. Pull the new image and recreate the container (your data is in the volume):\n"
            "  docker pull ghcr.io/suriyavasan/foundry-os:latest\n"
            "  docker rm -f foundry-os\n"
            "  docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest"
        )
    return (
        "This copy was not installed by the installer, so it cannot update itself.\n"
        "Install the latest release:  curl -fsSL https://raw.githubusercontent.com/"
        f"{install.GITHUB_REPO}/main/install.sh | sh"
    )
