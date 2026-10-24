"""M11: the automatic backup before migrations, its retention, and the refusal to start on a failed upgrade."""

from __future__ import annotations

import os
import sqlite3
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import install
from app.core.config import Settings
from app.ops import up, upgrade
from app.ops.backup import OpsError, _head_revision
from tests.m11_helpers import env, installer_layout  # noqa: F401
from tests.test_m9_up import run, seq  # noqa: F401


@pytest.fixture
def settings(db_url) -> Settings:
    return Settings(_env_file=None, database_url=db_url)


def backups(s: Settings) -> list[Path]:
    return sorted((s.resolved_data_dir / "backups").glob("pre-*.tar.gz"))


def set_revision(s: Settings, revision: str) -> None:
    conn = sqlite3.connect(s.resolved_data_dir / "test.db")
    conn.execute("UPDATE alembic_version SET version_num = ?", (revision,))
    conn.commit()
    conn.close()


class Migrate:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self, settings: Settings) -> None:
        self.calls += 1
        if self.fail:
            raise self.fail


def go(s: Settings, migrate=None, version="0.2.0", now=None):
    said: list[str] = []
    migrate = migrate or Migrate()
    ok = upgrade.upgrade_database(s, said.append, migrate, version=version, now=now)
    return ok, said, migrate


def test_a_fresh_install_needs_no_backup_but_remembers_the_version(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/fresh.db")
    ok, said, migrate = go(s)
    assert ok and migrate.calls == 1 and not backups(s) and not said
    assert upgrade.read_marker(s)["version"] == "0.2.0"


def test_a_version_change_backs_up_first_and_names_the_new_version(settings):
    upgrade.write_marker(settings, "0.1.0", _head_revision())
    ok, said, migrate = go(
        settings, version="0.2.0", now=datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    )
    assert ok and migrate.calls == 1
    (archive,) = backups(settings)
    assert archive.name == "pre-0.2.0-20261024-120000.tar.gz"
    assert any(
        "Backing up your data before upgrading to 0.2.0 (from 0.1.0)" in line for line in said
    )
    with tarfile.open(archive) as tar:  # a real, verifiable backup made by the normal backup module
        assert "manifest.json" in tar.getnames() and "db/company_os.db" in tar.getnames()
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    assert upgrade.read_marker(settings)["version"] == "0.2.0"


def test_the_same_version_with_nothing_pending_takes_no_backup(settings):
    upgrade.write_marker(settings, "0.2.0", _head_revision())
    ok, said, migrate = go(settings, version="0.2.0")
    assert ok and migrate.calls == 1 and not backups(settings)


def test_pending_migrations_trigger_a_backup_even_at_the_same_version(settings):
    upgrade.write_marker(settings, "0.2.0", "0001")
    set_revision(settings, "0001")  # known to the code, but behind head
    ok, _said, _m = go(settings, version="0.2.0")
    assert ok and len(backups(settings)) == 1


def test_an_existing_install_without_a_marker_is_backed_up_once(settings):
    ok, _said, _m = go(settings, version="0.1.0")
    assert ok and len(backups(settings)) == 1
    go(settings, version="0.1.0", now=datetime.now(UTC) + timedelta(seconds=5))
    assert len(backups(settings)) == 1  # second start: same version, at head


def test_only_the_five_newest_automatic_backups_are_kept_and_manual_ones_never_touched(settings):
    folder = settings.resolved_data_dir / "backups"
    folder.mkdir(exist_ok=True)
    manual = folder / "companyos-backup-20260101-000000.tar.gz"
    manual.write_bytes(b"mine")
    for n in range(7):
        old = folder / f"pre-0.0.{n}-20260101-00000{n}.tar.gz"
        old.write_bytes(b"x")
        os.utime(old, (1_000_000 + n, 1_000_000 + n))
    upgrade.write_marker(settings, "0.1.0", None)
    go(settings, version="0.2.0")
    kept = [p.name for p in backups(settings)]
    assert len(kept) == 5
    assert kept[-1].startswith("pre-0.2.0-")  # the new one survived
    assert (
        "pre-0.0.0-20260101-000000.tar.gz" not in kept
        and "pre-0.0.1-20260101-000001.tar.gz" not in kept
    )
    assert manual.exists()


def test_a_failed_migration_refuses_to_start_and_says_how_to_go_back(settings, env, monkeypatch):
    installer_layout(env, monkeypatch)
    upgrade.write_marker(settings, "0.1.0", _head_revision())
    ok, said, _m = go(settings, migrate=Migrate(RuntimeError("no such column")), version="0.2.0")
    assert ok is False
    text = "\n".join(said)
    assert "The database upgrade to 0.2.0 failed" in text and "no such column" in text
    assert "foundryos rollback" in text
    (archive,) = backups(settings)
    assert f'foundryos backup restore "{archive}" --force' in text
    assert upgrade.read_marker(settings)["version"] == "0.1.0"  # not remembered as upgraded


def test_recovery_text_differs_per_install_kind(settings, env, monkeypatch, tmp_path):
    archive = tmp_path / "pre.tar.gz"
    source = "\n".join(upgrade.recovery_text(settings, archive, "0.1.0"))
    assert "git checkout" in source and "rollback" not in source
    cellar = tmp_path / "brew" / "Cellar" / "foundry-os" / "0.2.0" / "libexec"
    cellar.mkdir(parents=True)
    (cellar / "VERSION").write_text("0.2.0")
    monkeypatch.setattr(install, "bundle_root", lambda: cellar)
    brew = "\n".join(upgrade.recovery_text(settings, archive, "0.1.0"))
    assert "install the previous version 0.1.0" in brew and "backup restore" in brew


def test_a_backup_that_cannot_be_made_stops_the_upgrade_before_any_change(settings, monkeypatch):
    def broken(*a, **k):
        raise OpsError("No space left on device")

    monkeypatch.setattr(upgrade, "create_backup", broken)
    upgrade.write_marker(settings, "0.1.0", None)
    ok, said, migrate = go(settings, version="0.2.0")
    assert ok is False and migrate.calls == 0
    assert any("Could not make the safety backup" in line for line in said)


def test_data_from_a_newer_version_is_not_touched(settings):
    set_revision(settings, "9999")
    upgrade.write_marker(settings, "0.9.0", "9999")
    ok, said, migrate = go(settings, version="0.1.0")
    assert ok is False and migrate.calls == 0 and not backups(settings)
    assert any("newer version" in line for line in said)


def test_the_backup_can_be_skipped_explicitly(settings, monkeypatch):
    monkeypatch.setenv("FOUNDRYOS_SKIP_PRE_UPGRADE_BACKUP", "1")
    upgrade.write_marker(settings, "0.1.0", None)
    ok, _said, migrate = go(settings, version="0.2.0")
    assert ok and migrate.calls == 1 and not backups(settings)


def test_a_non_sqlite_database_is_migrated_without_a_file_backup(tmp_path):
    s = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@localhost/x", data_dir=tmp_path
    )
    ok, _said, migrate = go(s)
    assert ok and migrate.calls == 1


# ------------------------------------------------------------------ `up` runs it


def test_up_does_not_start_anything_when_the_upgrade_is_refused(seq, monkeypatch):
    monkeypatch.setattr(up.upgrade, "upgrade_database", lambda s, say, migrate, **k: False)
    code = run(seq, up.UpOptions(port=8710))
    assert code == 1 and seq["spawned"] == [] and seq["migrated"] == 0


def test_up_migrates_through_the_upgrade_guard_and_registers_a_supervisor(seq, monkeypatch):
    seen = {}
    real = up.upgrade.upgrade_database

    def spy(settings, say, migrate, **k):
        seen["migrate"] = migrate
        return real(settings, say, migrate, **k)

    monkeypatch.setattr(up.upgrade, "upgrade_database", spy)
    code = run(seq, up.UpOptions(port=8710), after=lambda n, stop: stop.set() if n >= 2 else None)
    assert code == 0 and seen["migrate"] is up.ensure_migrated and seq["migrated"] == 1
    assert upgrade.read_marker(seq["settings"])["version"] == install.APP_VERSION
    # the supervisor's run file is gone again after a clean shutdown
    assert not list((seq["settings"].resolved_data_dir / "run").glob("supervisor.*.json"))


def test_migrate_command_backs_up_then_migrates_and_reports_refusal(tmp_path, monkeypatch, db_url):
    from tests.m11_helpers import invoke

    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", db_url)
    monkeypatch.delenv("FOUNDRYOS_DATA_DIR", raising=False)
    r = invoke("migrate")
    assert r.exit_code == 0, r.output
    assert "Backing up your data" in r.stdout and "The database is up to date." in r.stdout
    s = Settings(_env_file=None)
    assert len(backups(s)) == 1
    assert (
        invoke("migrate").stdout.count("Backing up") == 0
    )  # same version, at head: no second backup
    set_revision(s, "9999")
    refused = invoke("migrate")
    assert refused.exit_code == 1 and "newer version" in refused.stdout
