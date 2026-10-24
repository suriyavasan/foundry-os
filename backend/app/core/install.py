"""Where this copy of Foundry AI OS lives and how it was installed.

Four kinds of installation exist (docs/specs/m11-easy-install.md):

``source``     a git checkout (developers). Data stays in ``<repo>/data``.
``installer``  ``install.sh``: a release bundle in ``<prefix>/versions/<v>`` reached through
               ``<prefix>/current`` (default prefix ``~/.foundry-os``). Updates itself.
``homebrew``   the same bundle inside the Homebrew Cellar. Homebrew owns updates, services, removal.
``bundle``     a release bundle unpacked somewhere else (nobody manages it for us).
``docker``     inside a container image.

This module only reads the environment and the file system; it depends on nothing else in the app,
so that settings can use it.
"""

from __future__ import annotations

import os
import sys
from importlib import metadata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

APP_NAME = "Foundry AI OS"
COMMAND = "foundryos"
DEFAULT_PORT = 8700
GITHUB_REPO = "suriyavasan/foundry-os"
LAUNCHD_LABEL = "ai.foundry-os"
SYSTEMD_UNIT = "foundry-os.service"
BREW_FORMULA = "foundry-os"

SOURCE, INSTALLER, HOMEBREW, BUNDLE, DOCKER = "source", "installer", "homebrew", "bundle", "docker"


def bundle_root() -> Path | None:
    """The release bundle this code runs from (``<root>/app/backend`` + ``VERSION``), if any."""
    root = REPO_DIR.parent
    if REPO_DIR.name == "app" and (root / "VERSION").is_file():
        return root
    return None


def read_version() -> str:
    """The bundle's ``VERSION``, else the repository's, else the installed package's metadata."""
    candidates: list[Path] = []
    root = bundle_root()
    if root is not None:
        candidates.append(root / "VERSION")
    candidates.append(REPO_DIR / "VERSION")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    try:
        return metadata.version("company-os-backend")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def current_os() -> str:
    """``darwin``, ``linux`` or ``windows`` (a function so tests can pretend)."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def home_dir() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def source_checkout() -> bool:
    return (REPO_DIR / ".git").exists()


def installed_mode() -> bool:
    """Installed (platform data directory) unless this is a git checkout. ``FOUNDRYOS_INSTALLED=1``
    (set by the launcher) forces it on, ``0`` forces it off."""
    flag = os.environ.get("FOUNDRYOS_INSTALLED", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return not source_checkout()


def platform_data_dir(system: str | None = None, env: dict[str, str] | None = None) -> Path:
    """macOS ``~/Library/Application Support/Foundry AI OS``, Linux ``$XDG_DATA_HOME/foundry-os``
    (default ``~/.local/share/foundry-os``)."""
    system = system or current_os()
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    if system == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if system == "windows":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_NAME
    xdg = env.get("XDG_DATA_HOME")
    return (
        Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    ) / "foundry-os"


def is_homebrew_path(path: Path, env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if "Cellar" in path.parts:
        return True
    cellar = env.get("HOMEBREW_CELLAR")
    if cellar:
        try:
            path.resolve().relative_to(Path(cellar).resolve())
            return True
        except (ValueError, OSError):
            pass
    return False


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("FOUNDRYOS_IN_DOCKER") == "1"


def install_kind() -> str:
    root = bundle_root()
    if root is None:
        if in_container():
            return DOCKER
        return SOURCE
    if is_homebrew_path(root):
        return HOMEBREW
    if root.parent.name == "versions":
        return INSTALLER
    if in_container():
        return DOCKER
    return BUNDLE


def installer_prefix() -> Path | None:
    """``~/.foundry-os`` (holds ``versions/`` and ``current``) for installer installs."""
    root = bundle_root()
    if root is not None and root.parent.name == "versions":
        return root.parent.parent
    return None


def homebrew_prefix() -> Path | None:
    """The Homebrew prefix (``/opt/homebrew``) a Cellar path belongs to."""
    root = bundle_root()
    if root is None:
        return None
    parts = root.parts
    if "Cellar" in parts:
        return Path(*parts[: parts.index("Cellar")])
    env = os.environ.get("HOMEBREW_PREFIX")
    return Path(env) if env else None


def launcher_command() -> list[str]:
    """The stable command that starts this program (survives updates: names no version)."""
    override = os.environ.get("FOUNDRYOS_LAUNCHER")
    if override:
        return [override]
    kind = install_kind()
    root = bundle_root()
    if kind == INSTALLER:
        prefix = installer_prefix()
        if prefix is not None:
            return [str(prefix / "current" / "bin" / "foundryos")]
    if kind == HOMEBREW:
        prefix = homebrew_prefix()
        if prefix is not None:
            return [str(prefix / "opt" / BREW_FORMULA / "bin" / "foundryos")]
    if root is not None and (root / "bin" / "foundryos").exists():
        return [str(root / "bin" / "foundryos")]
    return [sys.executable, "-m", "app.cli.main"]


def platform_tag() -> str:
    """``darwin-arm64`` / ``linux-x86_64`` ...: the suffix of this machine's release tarball."""
    root = bundle_root()
    if root is not None:
        try:
            for line in (root / "BUILD_INFO").read_text(encoding="utf-8").splitlines():
                if line.startswith("platform:"):
                    value = line.split(":", 1)[1].strip()
                    if value:
                        return value
        except OSError:
            pass
    import platform

    machine = platform.machine().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        machine, machine
    )
    return f"{current_os()}-{arch}"


APP_VERSION = read_version()
