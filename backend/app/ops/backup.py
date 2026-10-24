"""Backup and restore of a Company OS data directory (SQLite installs).

Archive layout (``.tar.gz``, mode 0600)::

    manifest.json          format, created_at, app version, alembic revision, SHA-256 + size per file
    db/company_os.db       a consistent snapshot taken with SQLite's online backup API (safe while running)
    files/<relative path>  secret.key, owner.token, artifacts/, skills/, agents/ ...

Not included (re-creatable or machine-local): ``workspaces/`` (run scratch space), ``bin/`` (downloaded
helper binaries), ``run/`` (pid files), ``backups/``, ``*.pid``, caches. **The archive contains the secret
key and the owner token**: treat it like the secrets themselves (store it encrypted, e.g. age/gpg).

Restore verifies every file against the manifest *before* anything in the data directory is touched,
refuses when a Company OS process is using the directory, and replaces existing data only with
``force`` plus a confirmation; whatever it replaces is moved aside, not deleted.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import posixpath
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from app.core.config import APP_VERSION, Settings, sqlite_file_path
from app.ops import pidfile

FORMAT = 1
MANIFEST = "manifest.json"
DB_MEMBER = "db/company_os.db"
FILES_PREFIX = "files/"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024

# Relative to the data dir, skipped by backup. (The SQLite files are handled separately.)
EXCLUDED_TOP = {"workspaces", "bin", "run", "backups"}
EXCLUDED_NAMES = {"__pycache__", ".cache", ".DS_Store"}
# What restore moves aside / replaces (top-level names inside the data dir).
MANAGED_TOP = ("secret.key", "owner.token", "artifacts", "skills", "agents")
PRIVATE_FILES = ("secret.key", "owner.token")


class OpsError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.code = code


@dataclass
class BackupResult:
    path: Path
    files: int
    bytes: int
    alembic_revision: str | None


@dataclass
class RestoreResult:
    files: int
    replaced_into: Path | None
    alembic_revision: str | None


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(partial(fh.read, 1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _database_file(settings: Settings) -> Path:
    path = sqlite_file_path(settings.resolved_database_url)
    if path is None:
        raise OpsError(
            "This install uses a database other than a SQLite file. Back it up with the database's own "
            "tools (pg_dump / pg_restore for PostgreSQL) and copy the data directory separately.",
            exit_code=2,
            code="unsupported_database",
        )
    return path


def _alembic_revision(db: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def snapshot_database(src: Path, dest: Path) -> None:
    """SQLite online backup into ``dest`` (consistent even while the server writes)."""
    if not src.is_file():
        raise OpsError(f"No database at {src}. Is this the right data directory?", exit_code=5)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            target.close()
    finally:
        source.close()
    if verdict != "ok":
        raise OpsError(f"The database snapshot failed its integrity check: {verdict}")


def _iter_data_files(data_dir: Path, db_file: Path):
    """(relative posix path, absolute path) of every file that belongs in a backup, sorted."""
    skip_db = {
        db_file.name,
        db_file.name + "-wal",
        db_file.name + "-shm",
        db_file.name + "-journal",
    }
    out: list[tuple[str, Path]] = []
    for root, dirs, files in os.walk(data_dir, followlinks=False):
        rel_root = Path(root).relative_to(data_dir)
        if rel_root == Path("."):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_TOP]
        dirs[:] = [d for d in dirs if d not in EXCLUDED_NAMES]
        for name in files:
            if name in EXCLUDED_NAMES or name.endswith(".pid"):
                continue
            path = Path(root) / name
            if rel_root == Path(".") and name in skip_db:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            out.append(((rel_root / name).as_posix(), path))
    return sorted(out)


class _HashingReader:
    """File object that hashes what tarfile reads, so the archived bytes are what the manifest says."""

    def __init__(self, fh) -> None:
        self._fh = fh
        self.hash = hashlib.sha256()
        self.size = 0

    def read(self, n: int = -1) -> bytes:
        data = self._fh.read(n)
        self.hash.update(data)
        self.size += len(data)
        return data


def create_backup(
    settings: Settings, out: Path | None = None, *, now: datetime | None = None
) -> BackupResult:
    db_file = _database_file(settings)
    data_dir = settings.resolved_data_dir
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    default_name = f"companyos-backup-{stamp}.tar.gz"
    if out is None:
        out = data_dir / "backups" / default_name
        n = 1
        while out.exists():  # two backups in the same second
            out = data_dir / "backups" / f"companyos-backup-{stamp}-{n}.tar.gz"
            n += 1
    out = out.expanduser()
    if out.is_dir():
        out = out / default_name
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise OpsError(f"{out} already exists; choose another --out.", exit_code=6, code="exists")

    with tempfile.TemporaryDirectory(prefix="companyos-backup-", dir=out.parent) as tmp:
        snapshot = Path(tmp) / "company_os.db"
        snapshot_database(db_file, snapshot)
        revision = _alembic_revision(snapshot)
        entries: list[dict] = []
        sources: list[tuple[str, Path]] = []
        digest, size = _sha256_file(snapshot)
        entries.append({"path": DB_MEMBER, "sha256": digest, "size": size})
        sources.append((DB_MEMBER, snapshot))
        for rel, path in _iter_data_files(data_dir, db_file):
            digest, size = _sha256_file(path)
            entries.append({"path": FILES_PREFIX + rel, "sha256": digest, "size": size})
            sources.append((FILES_PREFIX + rel, path))
        manifest = {
            "format": FORMAT,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "app_version": APP_VERSION,
            "database": {"kind": "sqlite", "member": DB_MEMBER, "alembic_revision": revision},
            "excluded": sorted(EXCLUDED_TOP),
            "files": entries,
        }
        tmp_out = out.with_name(out.name + ".partial")
        fd = os.open(tmp_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
                body = json.dumps(manifest, indent=2).encode()
                info = tarfile.TarInfo(MANIFEST)
                info.size = len(body)
                info.mtime = int(now.timestamp())
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(body))
                expected = {e["path"]: e for e in entries}
                for arcname, path in sources:
                    st = path.stat()
                    tinfo = tarfile.TarInfo(arcname)
                    tinfo.size = expected[arcname]["size"]
                    tinfo.mtime = int(st.st_mtime)
                    tinfo.mode = 0o600
                    with path.open("rb") as fh:
                        reader = _HashingReader(fh)
                        if st.st_size != tinfo.size:
                            raise OpsError(
                                f"{path} changed while it was being backed up; run the backup again."
                            )
                        tar.addfile(tinfo, reader)
                        if reader.hash.hexdigest() != expected[arcname]["sha256"]:
                            raise OpsError(
                                f"{path} changed while it was being backed up; run the backup again."
                            )
            os.replace(tmp_out, out)
        finally:
            with contextlib.suppress(OSError):
                tmp_out.unlink()
    return BackupResult(out, len(entries), sum(e["size"] for e in entries), revision)


# ------------------------------------------------------------------------------ restore


def _safe_member_path(name: str) -> str:
    norm = posixpath.normpath(name)
    if (
        name != norm
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or norm.startswith("..")
        or ".." in norm.split("/")
    ):
        raise OpsError(f"The archive contains an unsafe path: {name!r}", code="unsafe_archive")
    return norm


def read_manifest(archive: Path) -> dict:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if member.name == MANIFEST:
                    if member.size > MAX_MANIFEST_BYTES or not member.isfile():
                        raise OpsError("The archive's manifest is not valid.", code="bad_manifest")
                    fh = tar.extractfile(member)
                    assert fh is not None
                    data = json.loads(fh.read().decode("utf-8"))
                    break
            else:
                raise OpsError(
                    "This is not a Company OS backup: manifest.json is missing.",
                    code="bad_manifest",
                )
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise OpsError(
            f"Cannot read {archive} as a backup archive: {exc}", code="bad_archive"
        ) from exc
    except ValueError as exc:
        raise OpsError("The archive's manifest is not valid JSON.", code="bad_manifest") from exc
    if (
        not isinstance(data, dict)
        or data.get("format") != FORMAT
        or not isinstance(data.get("files"), list)
    ):
        raise OpsError(
            f"Unsupported backup format {data.get('format') if isinstance(data, dict) else '?'}.",
            code="bad_manifest",
        )
    return data


def verify_archive(archive: Path, staging: Path | None = None) -> dict:
    """Check every member against the manifest. With ``staging``, also extract the verified files there.
    Raises :class:`OpsError` on any difference (damaged, truncated or modified archive)."""
    manifest = read_manifest(archive)
    expected: dict[str, dict] = {}
    for entry in manifest["files"]:
        try:
            path = _safe_member_path(str(entry["path"]))
            digest, size = str(entry["sha256"]), int(entry["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OpsError(
                "The archive's manifest has a malformed entry.", code="bad_manifest"
            ) from exc
        if not (path == DB_MEMBER or path.startswith(FILES_PREFIX)) or path in expected:
            raise OpsError(f"The manifest lists an unexpected path: {path!r}", code="bad_manifest")
        expected[path] = {"sha256": digest, "size": size}
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if member.name == MANIFEST:
                    continue
                path = _safe_member_path(member.name)
                if not member.isfile():
                    raise OpsError(
                        f"The archive contains a non-file entry: {path!r}", code="unsafe_archive"
                    )
                if path not in expected:
                    raise OpsError(
                        f"The archive contains {path!r}, which the manifest does not list.",
                        code="tampered",
                    )
                if path in seen:
                    raise OpsError(f"The archive contains {path!r} twice.", code="tampered")
                seen.add(path)
                fh = tar.extractfile(member)
                assert fh is not None
                h = hashlib.sha256()
                size = 0
                dest_fh = None
                if staging is not None:
                    target = staging / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    dest_fh = target.open("wb")
                try:
                    for chunk in iter(partial(fh.read, 1024 * 1024), b""):
                        h.update(chunk)
                        size += len(chunk)
                        if dest_fh is not None:
                            dest_fh.write(chunk)
                finally:
                    if dest_fh is not None:
                        dest_fh.close()
                want = expected[path]
                if size != want["size"] or h.hexdigest() != want["sha256"]:
                    raise OpsError(
                        f"Checksum mismatch for {path}: the archive is damaged or has been modified. "
                        "Nothing was restored.",
                        code="checksum_mismatch",
                    )
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise OpsError(
            f"The archive could not be read completely ({exc}). Nothing was restored.",
            code="bad_archive",
        ) from exc
    missing = sorted(set(expected) - seen)
    if missing:
        raise OpsError(
            f"The archive is missing {missing[0]!r} (and {len(missing) - 1} more).",
            code="incomplete",
        )
    if DB_MEMBER not in expected:
        raise OpsError("The archive has no database snapshot.", code="incomplete")
    return manifest


def _head_revision() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from app.core.config import BACKEND_DIR

        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


def _has_data(data_dir: Path, db_file: Path) -> bool:
    if db_file.exists():
        return True
    return any((data_dir / name).exists() for name in MANAGED_TOP)


def restore_backup(
    archive: Path,
    settings: Settings,
    *,
    force: bool = False,
    confirm: Callable[[str], bool] | None = None,
    now: datetime | None = None,
) -> RestoreResult:
    db_file = _database_file(settings)
    data_dir = settings.resolved_data_dir
    now = now or datetime.now(UTC)
    if not archive.is_file():
        raise OpsError(f"No such archive: {archive}", exit_code=5, code="not_found")

    running = pidfile.live_processes(data_dir)
    if running:
        who = ", ".join(f"{p['role']} (pid {p['pid']})" for p in running)
        raise OpsError(
            f"Company OS is running against {data_dir}: {who}. Stop it first (Ctrl-C in `companyos up`). "
            f"If that is wrong, delete the stale files in {data_dir / pidfile.RUN_DIR}.",
            exit_code=6,
            code="running",
        )

    staging = data_dir.parent / f".{data_dir.name}.restore-staging-{now.strftime('%Y%m%d%H%M%S')}"
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        manifest = verify_archive(archive, staging)
        snapshot = staging / DB_MEMBER
        try:
            conn = sqlite3.connect(snapshot)
            try:
                verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise OpsError(
                f"The database in the archive is not usable: {exc}", code="bad_database"
            ) from exc
        if verdict != "ok":
            raise OpsError(
                f"The database in the archive failed its integrity check: {verdict}",
                code="bad_database",
            )
        revision = manifest.get("database", {}).get("alembic_revision")
        head = _head_revision()
        if revision and head and revision != head:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            from app.core.config import BACKEND_DIR

            cfg = Config(str(BACKEND_DIR / "alembic.ini"))
            cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
            known = {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}
            if revision not in known:
                raise OpsError(
                    f"The backup was made by a newer Company OS (database revision {revision}). "
                    "Upgrade this install first.",
                    code="newer_backup",
                )

        existing = _has_data(data_dir, db_file)
        aside: Path | None = None
        if existing:
            if not force:
                raise OpsError(
                    f"{data_dir} already holds a Company OS install. Restoring replaces it; "
                    "re-run with --force (the current data is moved aside, not deleted).",
                    exit_code=6,
                    code="exists",
                )
            question = (
                f"Replace the install in {data_dir} with the backup from {manifest.get('created_at')}? "
                "Current data is moved aside first."
            )
            if confirm is None or not confirm(question):
                raise OpsError("Cancelled; nothing was changed.", exit_code=1, code="cancelled")
            aside = data_dir.parent / f"{data_dir.name}.pre-restore-{now.strftime('%Y%m%d-%H%M%S')}"
            aside.mkdir(parents=True, exist_ok=False)
            _move_aside(data_dir, db_file, aside)

        data_dir.mkdir(parents=True, exist_ok=True)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(snapshot), str(db_file))
        os.chmod(db_file, 0o600)
        files_root = staging / "files"
        count = 1
        if files_root.exists():
            for item in sorted(files_root.iterdir()):
                dest = data_dir / item.name
                if dest.is_dir():  # only possible without a previous install: a stray partial dir
                    shutil.rmtree(dest)
                elif dest.exists():
                    dest.unlink()
                shutil.move(str(item), str(dest))
                count += sum(1 for p in dest.rglob("*") if p.is_file()) if dest.is_dir() else 1
        for name in PRIVATE_FILES:
            if (data_dir / name).exists():
                os.chmod(data_dir / name, 0o600)
        return RestoreResult(count, aside, revision)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _move_aside(data_dir: Path, db_file: Path, aside: Path) -> None:
    names = [db_file.name, db_file.name + "-wal", db_file.name + "-shm", db_file.name + "-journal"]
    for name in names:
        src = db_file.parent / name
        if src.exists():
            shutil.move(str(src), str(aside / name))
    for name in MANAGED_TOP:
        src = data_dir / name
        if src.exists():
            shutil.move(str(src), str(aside / name))
