"""M9: backup/restore round trip, checksum verification, tamper detection, safety rails."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import stat
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.core.config import Settings
from app.ops import pidfile
from app.ops.backup import (
    MANIFEST,
    OpsError,
    create_backup,
    read_manifest,
    restore_backup,
    verify_archive,
)

FILES = {
    "secret.key": b"k" * 64,
    "owner.token": b"t" * 43,
    "artifacts/c1/a1/1": b"artifact bytes \x00\x01\x02",
    "skills/blobs/ab/abcdef": b"skill blob",
    "agents/a1/home/notes.txt": b"agent home file",
}
EXCLUDED = {
    "workspaces/c1/r1/scratch.txt": b"scratch",
    "bin/cloudflared": b"binary",
    "backups/old.tar.gz": b"old backup",
    "agents/a1/home/__pycache__/x.pyc": b"cache",
}


def make_install(root: Path, template: Path, marker: str = "alpha") -> Settings:
    data = root / "data"
    data.mkdir(parents=True)
    db = data / "company_os.db"
    shutil.copy(template, db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES (?)", (marker,))
    conn.commit()
    conn.close()
    for rel, body in {**FILES, **EXCLUDED}.items():
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    os.chmod(data / "secret.key", 0o600)
    return Settings(_env_file=None, database_url=f"sqlite:///{db}", data_dir=data)


def target_settings(root: Path) -> Settings:
    data = root / "restored"
    return Settings(
        _env_file=None, database_url=f"sqlite:///{data / 'company_os.db'}", data_dir=data
    )


def marker_of(settings: Settings) -> str:
    db = settings.resolved_data_dir / "company_os.db"
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT v FROM marker").fetchone()[0]
    finally:
        conn.close()


def rewrite(src: Path, dest: Path, mutate) -> Path:
    """Copy an archive member by member, letting ``mutate(name, data) -> (name, data) | None`` change it."""
    with tarfile.open(src, "r:gz") as tin, tarfile.open(dest, "w:gz") as tout:
        for m in tin:
            data = tin.extractfile(m).read()  # type: ignore[union-attr]
            changed = mutate(m.name, data)
            if changed is None:
                continue
            name, body = changed
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tout.addfile(info, io.BytesIO(body))
    return dest


@pytest.fixture
def source(tmp_path: Path, migrated_template: Path) -> Settings:
    return make_install(tmp_path / "src", migrated_template)


@pytest.fixture
def archive(source: Settings, tmp_path: Path) -> Path:
    return create_backup(source, tmp_path / "out" / "b.tar.gz").path


# ------------------------------------------------------------------ round trip


def test_round_trip_restores_database_and_files(source, archive, tmp_path):
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    manifest = read_manifest(archive)
    paths = {e["path"] for e in manifest["files"]}
    assert paths == {"db/company_os.db"} | {f"files/{p}" for p in FILES}
    assert all(len(e["sha256"]) == 64 for e in manifest["files"])
    assert manifest["database"]["alembic_revision"]
    assert not any("workspaces" in p or "bin/" in p or "backups" in p or "run/" in p for p in paths)
    assert not any("__pycache__" in p for p in paths)

    target = target_settings(tmp_path / "dst")
    result = restore_backup(archive, target)
    assert result.replaced_into is None
    data = target.resolved_data_dir
    assert marker_of(target) == "alpha"
    for rel, body in FILES.items():
        assert (data / rel).read_bytes() == body, rel
    for rel in EXCLUDED:
        assert not (data / rel).exists(), rel
    assert stat.S_IMODE((data / "secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "owner.token").stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "company_os.db").stat().st_mode) == 0o600
    assert not list((tmp_path / "dst").glob(".*staging*"))  # staging is cleaned up
    # the restored database is a real, migrated Company OS database
    conn = sqlite3.connect(data / "company_os.db")
    assert (
        conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == (manifest["database"]["alembic_revision"])
    )
    conn.close()


def test_snapshot_includes_committed_wal_data_and_ignores_open_transactions(source, tmp_path):
    db = source.resolved_data_dir / "company_os.db"
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO marker VALUES ('committed-in-wal')")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO marker VALUES ('never-committed')")
    out = create_backup(source, tmp_path / "wal.tar.gz").path  # server still running and writing
    writer.execute("ROLLBACK")
    writer.close()
    target = target_settings(tmp_path / "dst")
    restore_backup(out, target)
    conn = sqlite3.connect(target.resolved_data_dir / "company_os.db")
    values = {r[0] for r in conn.execute("SELECT v FROM marker")}
    conn.close()
    assert values == {"alpha", "committed-in-wal"}


def test_default_location_and_name_collisions(source):
    result = create_backup(source)
    assert result.path.parent == source.resolved_data_dir / "backups"
    assert result.path.name.startswith("companyos-backup-")
    again = create_backup(source)  # the backups/ folder is excluded from itself
    assert not any("backups" in e["path"] for e in read_manifest(again.path)["files"])
    with pytest.raises(OpsError) as exc:
        create_backup(source, result.path)
    assert exc.value.code == "exists"


def test_postgres_installs_are_pointed_at_pg_dump(tmp_path):
    pg = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@db/x", data_dir=tmp_path)
    with pytest.raises(OpsError) as exc:
        create_backup(pg, tmp_path / "x.tar.gz")
    assert "pg_dump" in exc.value.message and exc.value.code == "unsupported_database"
    with pytest.raises(OpsError):
        restore_backup(tmp_path / "x.tar.gz", pg)


# ------------------------------------------------------------------ tamper detection


def assert_untouched(target: Settings):
    assert not target.resolved_data_dir.exists()
    assert not list(target.resolved_data_dir.parent.glob(".*staging*"))


def test_a_modified_file_fails_the_checksum_and_nothing_is_restored(archive, tmp_path):
    bad = rewrite(
        archive,
        tmp_path / "tampered.tar.gz",
        lambda n, d: (n, d + b"!") if n == "files/secret.key" else (n, d),
    )
    with pytest.raises(OpsError) as exc:
        verify_archive(bad)
    assert exc.value.code == "checksum_mismatch" and "secret.key" in exc.value.message
    target = target_settings(tmp_path / "dst")
    with pytest.raises(OpsError):
        restore_backup(bad, target)
    assert_untouched(target)


def test_same_size_flipped_byte_is_caught(archive, tmp_path):
    def flip(name, data):
        if name == "files/artifacts/c1/a1/1":
            return name, bytes([data[0] ^ 1]) + data[1:]
        return name, data

    bad = rewrite(archive, tmp_path / "flip.tar.gz", flip)
    with pytest.raises(OpsError) as exc:
        verify_archive(bad)
    assert exc.value.code == "checksum_mismatch"


def test_manifest_edits_are_caught(archive, tmp_path):
    def lie(name, data):
        if name == MANIFEST:
            m = json.loads(data)
            m["files"][1]["sha256"] = "0" * 64
            return name, json.dumps(m).encode()
        return name, data

    with pytest.raises(OpsError) as exc:
        verify_archive(rewrite(archive, tmp_path / "lie.tar.gz", lie))
    assert exc.value.code == "checksum_mismatch"


def test_extra_missing_duplicate_and_unsafe_members_are_refused(archive, tmp_path):
    # an extra file the manifest does not list
    extra = tmp_path / "extra.tar.gz"
    with tarfile.open(archive, "r:gz") as tin, tarfile.open(extra, "w:gz") as tout:
        for m in tin:
            tout.addfile(m, tin.extractfile(m))
        info = tarfile.TarInfo("files/evil.sh")
        info.size = 2
        tout.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(OpsError) as exc:
        verify_archive(extra)
    assert exc.value.code == "tampered"

    dropped = rewrite(
        archive,
        tmp_path / "drop.tar.gz",
        lambda n, d: None if n == "files/skills/blobs/ab/abcdef" else (n, d),
    )
    with pytest.raises(OpsError) as exc:
        verify_archive(dropped)
    assert exc.value.code == "incomplete"

    for evil in ("files/../../etc/passwd", "/etc/passwd", "files//x", "files/a/../b"):
        bad = rewrite(
            archive,
            tmp_path / "evil.tar.gz",
            lambda n, d, evil=evil: (evil if n == "files/secret.key" else n, d),
        )
        with pytest.raises(OpsError) as exc:
            verify_archive(bad)
        assert exc.value.code in ("unsafe_archive", "bad_manifest", "tampered"), evil


def test_links_and_devices_are_refused(archive, tmp_path):
    link = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "r:gz") as tin, tarfile.open(link, "w:gz") as tout:
        for m in tin:
            tout.addfile(m, tin.extractfile(m))
        sym = tarfile.TarInfo("files/escape")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc"
        tout.addfile(sym)
    with pytest.raises(OpsError) as exc:
        verify_archive(link)
    assert exc.value.code == "unsafe_archive"


def test_truncated_and_foreign_files(archive, tmp_path):
    raw = archive.read_bytes()
    truncated = tmp_path / "trunc.tar.gz"
    truncated.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(OpsError) as exc:
        verify_archive(truncated)
    assert exc.value.code in ("bad_archive", "bad_manifest", "incomplete")
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not an archive at all")
    with pytest.raises(OpsError) as exc:
        verify_archive(junk)
    assert exc.value.code == "bad_archive"
    plain = tmp_path / "plain.tar.gz"
    with tarfile.open(plain, "w:gz") as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(OpsError) as exc:
        verify_archive(plain)
    assert exc.value.code == "bad_manifest"
    with pytest.raises(OpsError) as exc:
        restore_backup(tmp_path / "missing.tar.gz", target_settings(tmp_path))
    assert exc.value.exit_code == 5


def test_a_backup_from_a_newer_version_is_refused(archive, tmp_path):
    def newer(name, data):
        if name == MANIFEST:
            m = json.loads(data)
            m["database"]["alembic_revision"] = "9999_from_the_future"
            return name, json.dumps(m).encode()
        return name, data

    bad = rewrite(archive, tmp_path / "future.tar.gz", newer)
    target = target_settings(tmp_path / "dst")
    with pytest.raises(OpsError) as exc:
        restore_backup(bad, target)
    assert exc.value.code == "newer_backup"
    assert_untouched(target)


# ------------------------------------------------------------------ safety rails


def test_refuses_while_a_process_uses_the_data_dir(archive, tmp_path, migrated_template):
    dst = make_install(tmp_path / "live", migrated_template, marker="live")
    run = pidfile.register(
        dst.resolved_data_dir, "api", port=8700
    )  # this very process is "the server"
    assert run is not None
    try:
        with pytest.raises(OpsError) as exc:
            restore_backup(archive, dst, force=True, confirm=lambda q: True)
        assert exc.value.code == "running" and "api" in exc.value.message
        assert marker_of(dst) == "live"
    finally:
        pidfile.unregister(run)
    # a stale file (dead pid) does not block, and is cleaned up
    stale = dst.resolved_data_dir / "run" / "api.999999999.json"
    stale.write_text(json.dumps({"role": "api", "pid": 999999999}))
    assert pidfile.live_processes(dst.resolved_data_dir) == []
    assert not stale.exists()


def test_existing_install_needs_force_and_confirmation_and_is_moved_aside(
    archive, tmp_path, migrated_template
):
    dst = make_install(tmp_path / "old", migrated_template, marker="precious")
    with pytest.raises(OpsError) as exc:
        restore_backup(archive, dst)
    assert exc.value.code == "exists" and "--force" in exc.value.message
    assert marker_of(dst) == "precious"

    with pytest.raises(OpsError) as exc:  # --force alone is not enough
        restore_backup(archive, dst, force=True)
    assert exc.value.code == "cancelled"
    declined = []
    with pytest.raises(OpsError):
        restore_backup(archive, dst, force=True, confirm=lambda q: declined.append(q) or False)
    assert declined and "Replace the install" in declined[0]
    assert marker_of(dst) == "precious"
    assert (dst.resolved_data_dir / "secret.key").read_bytes() == FILES["secret.key"]

    result = restore_backup(archive, dst, force=True, confirm=lambda q: True)
    assert marker_of(dst) == "alpha"
    aside = result.replaced_into
    assert aside and aside.exists()
    old = sqlite3.connect(aside / "company_os.db")
    assert old.execute("SELECT v FROM marker").fetchone()[0] == "precious"
    old.close()
    assert (aside / "secret.key").exists()
    # unmanaged local state (workspaces, bin) is left where it was
    assert (dst.resolved_data_dir / "workspaces/c1/r1/scratch.txt").exists()


# ------------------------------------------------------------------ the CLI


def test_cli_backup_and_restore(tmp_path, migrated_template, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    src = make_install(tmp_path / "src", migrated_template)
    runner = CliRunner()
    out = tmp_path / "cli.tar.gz"
    r = runner.invoke(
        cli_app,
        ["backup", "create", "--data-dir", str(src.resolved_data_dir), "--out", str(out), "--json"],
    )
    assert r.exit_code == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["path"] == str(out) and body["files"] == 1 + len(FILES)
    human = runner.invoke(
        cli_app,
        [
            "backup",
            "create",
            "--data-dir",
            str(src.resolved_data_dir),
            "--out",
            str(tmp_path / "h.tar.gz"),
        ],
    )
    assert "secret key" in human.stdout

    v = runner.invoke(cli_app, ["backup", "restore", str(out), "--verify-only", "--json"])
    assert v.exit_code == 0 and json.loads(v.stdout)["verified"] is True

    dst = tmp_path / "dst" / "data"
    ok = runner.invoke(cli_app, ["backup", "restore", str(out), "--data-dir", str(dst), "--json"])
    assert ok.exit_code == 0, ok.stderr
    assert json.loads(ok.stdout)["restored"] is True
    assert (dst / "secret.key").read_bytes() == FILES["secret.key"]

    # existing install: exit 6 without --force; prompt declined -> exit 1; --force --yes works
    again = runner.invoke(cli_app, ["backup", "restore", str(out), "--data-dir", str(dst)])
    assert again.exit_code == 6 and "--force" in again.stderr
    declined = runner.invoke(
        cli_app, ["backup", "restore", str(out), "--data-dir", str(dst), "--force"], input="n\n"
    )
    assert declined.exit_code == 1 and "Cancelled" in declined.stderr
    scripted = runner.invoke(
        cli_app, ["backup", "restore", str(out), "--data-dir", str(dst), "--force", "--json"]
    )
    assert scripted.exit_code == 1  # --json never blocks on a prompt: it refuses
    yes = runner.invoke(
        cli_app, ["backup", "restore", str(out), "--data-dir", str(dst), "--force", "--yes"]
    )
    assert yes.exit_code == 0, yes.stderr
    assert "moved to" in yes.stdout

    bad = rewrite(
        out,
        tmp_path / "bad.tar.gz",
        lambda n, d: (n, d + b"x") if n.endswith("owner.token") else (n, d),
    )
    t = runner.invoke(
        cli_app, ["backup", "restore", str(bad), "--data-dir", str(tmp_path / "other")]
    )
    assert t.exit_code == 1 and "Checksum mismatch" in t.stderr
    assert not (tmp_path / "other").exists()
    assert (
        runner.invoke(cli_app, ["backup", "restore", str(tmp_path / "nope.tar.gz")]).exit_code == 5
    )
