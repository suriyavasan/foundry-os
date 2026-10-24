"""``foundryos uninstall [--purge]``.

* Stops the program and removes the background service we created.
* Installer installs: removes ``~/.foundry-os/versions``, ``current``, ``previous`` and the
  ``foundryos`` / ``companyos`` links that point into that folder.
* Homebrew installs: **nothing Homebrew owns is touched**; the command prints ``brew uninstall foundry-os``.
* Source checkouts, unmanaged bundles and Docker: nothing to remove (the message says what to do).
* Your data is kept. ``--purge`` deletes the data folder, and only after a typed confirmation
  (``--yes-delete-my-data`` for scripts), and never a folder that does not look like Foundry data.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core import install
from app.core.config import Settings
from app.ops import lifecycle, services

CONFIRM_PHRASE = "delete my data"
LINK_NAMES = ("foundryos", "companyos")
DATA_MARKERS = (
    "company_os.db",
    "secret.key",
    "owner.token",
    "state",
    "run",
    "logs",
    "backups",
    "artifacts",
)


@dataclass
class UninstallResult:
    kind: str
    stopped: bool = False
    service_removed: bool = False
    removed: list[str] = field(default_factory=list)
    purged: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "install_kind": self.kind,
            "stopped": self.stopped,
            "service_removed": self.service_removed,
            "removed": self.removed,
            "purged": self.purged,
            "notes": self.notes,
        }


class PurgeRefused(Exception):
    pass


def purge_block(data_dir: Path) -> str | None:
    """Why this folder must not be deleted as 'the data folder', or None when it may be."""
    try:
        path = data_dir.resolve()
    except OSError:
        return "the path cannot be resolved"
    home = install.home_dir().resolve()
    if len(path.parts) < 3 or path in (home, home.parent, Path(path.anchor)):
        return f"{path} is a system or home folder, not a Foundry AI OS data folder"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_dir():
        return f"{path} is not a plain folder"
    names = {p.name for p in path.iterdir()}
    if names and not names & set(DATA_MARKERS):
        return (
            f"{path} does not look like a Foundry AI OS data folder (none of "
            f"{', '.join(DATA_MARKERS[:4])}... in it)"
        )
    return None


def _path_dirs(prefix: Path) -> list[Path]:
    dirs = [install.home_dir() / ".local" / "bin", prefix / "bin"]
    dirs += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    seen: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def installer_links(prefix: Path) -> list[Path]:
    """Links named foundryos/companyos (on PATH, in ~/.local/bin, in <prefix>/bin) that lead into ``prefix``."""
    root = os.path.realpath(prefix)
    found = []
    for directory in _path_dirs(prefix):
        for name in LINK_NAMES:
            link = directory / name
            if not link.is_symlink():
                continue
            real = os.path.realpath(link)
            if real == root or real.startswith(root + os.sep):
                found.append(link)
    return found


def uninstall(
    settings: Settings,
    say: Callable[[str], None] = print,
    *,
    purge: bool = False,
    confirm_purge: Callable[[Path], bool] | None = None,
) -> UninstallResult:
    kind = install.install_kind()
    result = UninstallResult(kind)
    data_dir = settings.resolved_data_dir

    if purge:
        reason = purge_block(data_dir)
        if reason:
            raise PurgeRefused(f"Refusing to delete the data folder: {reason}.")
        if confirm_purge is None or not confirm_purge(data_dir):
            raise PurgeRefused("Not confirmed, so nothing was removed.")

    try:
        result.stopped = lifecycle.stop(settings, say)
    except lifecycle.LifecycleError as exc:
        say(f"Note: {exc.message}")

    if kind == install.HOMEBREW or services.brew_manager().installed():
        result.notes.append(
            "Homebrew owns this install. Remove the program with:\n"
            "  brew services stop foundry-os   (if you used brew services)\n"
            "  brew uninstall foundry-os"
        )
    native = services.native_manager()
    if native is not None and native.installed() and kind != install.HOMEBREW:
        try:
            for line in native.uninstall():
                say(line)
            result.service_removed = True
        except services.ServiceError as exc:
            say(f"Could not remove the background service: {exc}")

    prefix = install.installer_prefix()
    removals: list[Path] = []
    if kind == install.INSTALLER and prefix is not None:
        removals = [*installer_links(prefix)]
        removals += [prefix / "versions", prefix / "current", prefix / "previous"]
    elif kind == install.SOURCE:
        result.notes.append(
            "This is a source checkout: there is nothing installed to remove. Delete the folder "
            f"{install.REPO_DIR} yourself if you no longer want it."
        )
    elif kind == install.DOCKER:
        result.notes.append("This runs in Docker: remove it with `docker rm -f foundry-os`.")
    elif kind == install.BUNDLE:
        result.notes.append(
            f"This copy was unpacked by hand: delete {install.bundle_root()} yourself."
        )

    if purge:
        shutil.rmtree(data_dir, ignore_errors=False)
        result.purged = str(data_dir)
        say(f"Deleted your data folder {data_dir}.")
    else:
        result.notes.append(
            f"Your data was kept in {data_dir}. To delete it as well: foundryos uninstall --purge"
        )

    # Last: this removes the very program that is running, so nothing may be imported after it.
    for path in removals:
        if path.is_symlink() or path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
            result.removed.append(str(path))
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            result.removed.append(str(path))
    if prefix is not None and kind == install.INSTALLER:
        for leftover in (prefix / "bin", prefix):
            with contextlib.suppress(OSError):
                leftover.rmdir()
    return result
