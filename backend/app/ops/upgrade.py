"""Safe upgrades: a backup before the database is migrated, and a clear stop if migrating fails.

Whenever the program version changed since the last start (or migrations are pending), ``foundryos up``
and ``serve`` first write ``<data dir>/backups/pre-<new version>-<UTC time>.tar.gz`` using the same
backup module as ``foundryos backup create`` (a consistent snapshot, checksummed, mode 0600) and keep
the five newest of those. If the backup cannot be made, or the migration fails, the server does not
start and the message says how to go back. The version that last started successfully is remembered in
``<data dir>/state/version.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import install
from app.core.config import Settings, sqlite_file_path
from app.ops import updater
from app.ops.backup import OpsError, _alembic_revision, _head_revision, create_backup

KEEP_PRE_UPGRADE = 5
MARKER_NAME = "version.json"
PRE_GLOB = "pre-*.tar.gz"


def marker_path(settings: Settings) -> Path:
    return settings.resolved_data_dir / "state" / MARKER_NAME


def read_marker(settings: Settings) -> dict[str, Any]:
    try:
        data = json.loads(marker_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_marker(settings: Settings, version: str, revision: str | None) -> None:
    path = marker_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": version,
                "alembic_revision": revision,
                "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def prune_pre_upgrade(backups: Path, keep: int = KEEP_PRE_UPGRADE) -> list[Path]:
    """Delete all but the newest ``keep`` automatic backups (manual ones are never touched)."""
    found = sorted(backups.glob(PRE_GLOB), key=lambda p: (p.stat().st_mtime, p.name))
    doomed = found[: max(len(found) - keep, 0)]
    for path in doomed:
        with contextlib.suppress(OSError):
            path.unlink()
    return doomed


def revision_known_to_code(revision: str) -> bool | None:
    """Does the code running now know this migration? None when that cannot be told."""
    from app.core.config import BACKEND_DIR

    known = updater.revisions_in(BACKEND_DIR / "alembic" / "versions")
    return revision in known if known else None


def recovery_text(settings: Settings, backup: Path | None, prior: str | None) -> list[str]:
    """What to do after a failed upgrade, for this kind of install."""
    kind = install.install_kind()
    lines = ["", "Your data has not been lost. To go back:"]
    step = 1
    if kind == install.INSTALLER:
        lines.append(f"  {step}. foundryos rollback   (switches back to the previous version)")
        step += 1
    elif kind == install.HOMEBREW:
        prior_text = f" {prior}" if prior else ""
        lines.append(
            f"  {step}. install the previous version{prior_text} again "
            "(brew install suriyavasan/tap/foundry-os@<version>, or the installer with --version)"
        )
        step += 1
    elif kind == install.SOURCE:
        lines.append(f"  {step}. git checkout the previous version and run `uv sync` in backend/")
        step += 1
    if backup is not None:
        lines.append(
            f'  {step}. foundryos backup restore "{backup}" --force   '
            "(puts your data back as it was before this upgrade; the current data is moved aside)"
        )
    else:
        lines.append(
            f"  {step}. restore a backup if the database was changed: foundryos backup restore ARCHIVE --force"
        )
    return lines


def upgrade_database(
    settings: Settings,
    say: Callable[[str], None],
    migrate: Callable[[Settings], None],
    *,
    version: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Backup (when needed), migrate, remember the version. False = do not start (reason already said)."""
    version = version or install.APP_VERSION
    db = sqlite_file_path(settings.resolved_database_url)
    marker = read_marker(settings)
    prior = marker.get("version")
    backup: Path | None = None
    revision: str | None = None
    if db is not None and db.is_file():
        revision = _alembic_revision(db)
        head = _head_revision()
        if revision is not None and revision_known_to_code(revision) is False:
            latest = updater.latest_pre_upgrade_backup(settings, prior or "") if prior else None
            say(
                f"This data was last used by a newer version of Foundry AI OS (database revision "
                f"{revision}); this version ({version}) does not know it, so it will not touch it."
            )
            for line in recovery_text(settings, latest, prior):
                say(line)
            return False
        changed = prior != version
        pending = bool(head) and revision != head
        if (
            revision is not None
            and (changed or pending)
            and not os.environ.get("FOUNDRYOS_SKIP_PRE_UPGRADE_BACKUP")
        ):
            stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
            target = settings.resolved_data_dir / "backups" / f"pre-{version}-{stamp}.tar.gz"
            n = 1
            while target.exists():
                target = target.with_name(f"pre-{version}-{stamp}-{n}.tar.gz")
                n += 1
            what = f"from {prior}" if prior else "from an earlier version"
            say(f"Backing up your data before upgrading to {version} ({what})...")
            try:
                create_backup(settings, target, now=now)
            except OpsError as exc:
                say(f"Could not make the safety backup: {exc.message}")
                say(
                    "Foundry AI OS did not start, and nothing was changed. Free some disk space or fix "
                    "the problem above, then start it again."
                )
                return False
            backup = target
            say(f"  saved {target}")
            prune_pre_upgrade(target.parent)
    try:
        migrate(settings)
    except Exception as exc:
        say(f"The database upgrade to {version} failed: {exc.__class__.__name__}: {exc}"[:600])
        say("Foundry AI OS did not start.")
        for line in recovery_text(settings, backup, prior):
            say(line)
        return False
    new_revision = _alembic_revision(db) if db is not None else None
    with contextlib.suppress(OSError):
        write_marker(settings, version, new_revision)
    return True
