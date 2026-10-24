"""M11: `foundryos update` / `rollback` (fake GitHub API + local tarballs) and the daily update check."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core import install
from app.core.config import Settings
from app.ops import lifecycle, updatecheck, updater
from tests.m11_helpers import (  # noqa: F401
    Env,
    as_json,
    env,
    fake_github,
    installer_layout,
    invoke,
    make_release,
    write_bundle,
)


@pytest.fixture
def world(env, monkeypatch, tmp_path):
    """An installer install of 0.1.0; release 0.2.0 (good) and 0.3.0 (bad checksum) on a fake GitHub."""
    installer_layout(env, monkeypatch)
    monkeypatch.setattr(updater, "MIN_FREE_BYTES", 0)  # the suite must not depend on this disk
    releases = {
        "0.2.0": make_release(tmp_path / "dl", "0.2.0"),
        "0.2.1": make_release(tmp_path / "dl", "0.2.1", revisions=("0001", "0002")),
        "0.3.0": make_release(tmp_path / "dl", "0.3.0", bad_sum=True),
    }
    seen: list[str] = []
    factory = fake_github(releases, latest="0.2.0", calls=seen)
    monkeypatch.setattr(updatecheck, "http_client_factory", factory)
    monkeypatch.setattr(updater, "download_client_factory", factory)
    monkeypatch.setattr(lifecycle, "probe_health", lambda port, **k: None)  # not running
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    return {"env": env, "releases": releases, "seen": seen, "factory": factory}


def versions(env: Env) -> list[str]:
    return sorted(p.name for p in (env.prefix / "versions").iterdir() if not p.name.startswith("."))


def current(env: Env) -> str:
    return os.readlink(env.prefix / "current")


# ------------------------------------------------------------------ update


def test_update_downloads_verifies_and_switches_atomically(world):
    env = world["env"]
    r = invoke("update")
    assert r.exit_code == 0, r.output
    assert current(env) == "versions/0.2.0"
    assert versions(env) == ["0.1.0", "0.2.0"]  # the previous version is kept
    assert os.readlink(env.prefix / "previous") == "versions/0.1.0"
    assert (env.prefix / "versions" / "0.2.0" / "VERSION").read_text().strip() == "0.2.0"
    assert os.access(env.prefix / "versions" / "0.2.0" / "bin" / "foundryos", os.X_OK)
    assert "Checksum verified" in r.stdout and "It was not running" in r.stdout
    assert "rollback" in r.stdout
    assert not list((env.prefix / "versions").glob(".incoming-*"))
    # the switch is a single rename of a symlink: never a moment without `current`
    assert (env.prefix / "current").is_symlink()


def test_update_is_idempotent_when_already_current(world, monkeypatch):
    invoke("update")
    r = invoke("update")
    assert r.exit_code == 0 and "Already on the latest release (0.2.0)" in r.stdout


def test_a_checksum_mismatch_is_refused_and_nothing_changes(world):
    env = world["env"]
    r = invoke("update", "--version", "0.3.0")
    assert r.exit_code == 1 and "does not match its published checksum" in r.output
    assert current(env) == "versions/0.1.0" and versions(env) == ["0.1.0"]
    assert not list((env.prefix / "versions").glob(".incoming-*"))


def test_a_tampered_tarball_is_refused(world, tmp_path, monkeypatch):
    env = world["env"]
    rel = world["releases"]["0.2.0"]
    rel.tarball.write_bytes(rel.tarball.read_bytes() + b"tampered")
    r = invoke("update")
    assert r.exit_code == 1 and "checksum" in r.output
    assert current(env) == "versions/0.1.0"


def test_missing_sha256sums_asset_refuses(world, monkeypatch):
    env = world["env"]
    factory = fake_github(world["releases"], latest="0.2.0", with_sums=False)
    monkeypatch.setattr(updatecheck, "http_client_factory", factory)
    r = invoke("update")
    assert r.exit_code == 1 and "no SHA256SUMS" in r.output and "Refusing" in r.output
    assert current(env) == "versions/0.1.0"


def test_sums_without_an_entry_for_this_platform_refuses(world, monkeypatch):
    env = world["env"]
    rel = world["releases"]["0.2.0"]
    world["releases"]["0.2.0"] = type(rel)(
        rel.version, rel.tarball, b"%s  foundry-os-0.2.0-linux-x86_64.tar.gz\n" % (b"a" * 64)
    )
    r = invoke("update")
    assert r.exit_code == 1 and "no entry" in r.output
    assert current(env) == "versions/0.1.0"


def test_a_bundle_with_the_wrong_version_inside_is_refused(world, tmp_path, monkeypatch):
    env = world["env"]
    from tests.m11_helpers import Release, make_tarball, sha256

    # a tarball named 0.4.0 whose VERSION file says 0.9.9
    inner = make_tarball(tmp_path / "liar", "0.9.9")
    named = inner.with_name("foundry-os-0.4.0-darwin-arm64.tar.gz")
    inner.rename(named)
    rel = Release("0.4.0", named, f"{sha256(named)}  {named.name}\n".encode())
    monkeypatch.setattr(
        updatecheck, "http_client_factory", fake_github({"0.4.0": rel}, latest="0.4.0")
    )
    monkeypatch.setattr(
        updater, "download_client_factory", fake_github({"0.4.0": rel}, latest="0.4.0")
    )
    r = invoke("update")
    assert r.exit_code == 1 and "not a complete" in r.output
    assert current(env) == "versions/0.1.0"


def test_a_tarball_with_paths_outside_the_bundle_is_refused(world, tmp_path, monkeypatch):
    env = world["env"]
    import io
    import tarfile

    from tests.m11_helpers import Release, sha256

    evil = tmp_path / "evil" / "foundry-os-0.5.0-darwin-arm64.tar.gz"
    evil.parent.mkdir()
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    rel = Release("0.5.0", evil, f"{sha256(evil)}  {evil.name}\n".encode())
    factory = fake_github({"0.5.0": rel}, latest="0.5.0")
    monkeypatch.setattr(updatecheck, "http_client_factory", factory)
    monkeypatch.setattr(updater, "download_client_factory", factory)
    r = invoke("update")
    assert r.exit_code == 1 and "does not look like" in r.output
    assert (
        not (env.prefix / "escape.txt").exists() and not (env.prefix.parent / "escape.txt").exists()
    )


def test_a_tarball_with_an_absolute_symlink_is_refused(world, tmp_path, monkeypatch):
    env = world["env"]
    import tarfile

    from tests.m11_helpers import Release, bundle_files, sha256

    evil = tmp_path / "evil2" / "foundry-os-0.6.0-darwin-arm64.tar.gz"
    evil.parent.mkdir()
    with tarfile.open(evil, "w:gz") as tar:
        import io

        for rel, data in bundle_files("0.6.0").items():
            info = tarfile.TarInfo(f"foundry-os/{rel}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("foundry-os/python")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        tar.addfile(link)
    rel = Release("0.6.0", evil, f"{sha256(evil)}  {evil.name}\n".encode())
    factory = fake_github({"0.6.0": rel}, latest="0.6.0")
    monkeypatch.setattr(updatecheck, "http_client_factory", factory)
    monkeypatch.setattr(updater, "download_client_factory", factory)
    r = invoke("update")
    assert r.exit_code == 1 and "could not be unpacked" in r.output
    assert current(env) == "versions/0.1.0" and versions(env) == ["0.1.0"]


def test_json_output_keeps_progress_off_stdout(world):
    r = invoke("update", "--json")
    assert r.exit_code == 0, r.output
    result = as_json(r)
    assert result["from"] == "0.1.0" and result["to"] == "0.2.0" and result["switched"] is True


def test_asking_for_the_version_you_run_changes_nothing(world):
    env = world["env"]
    invoke("update")
    r = invoke("update", "--version", "0.2.0")
    assert r.exit_code == 0 and "Already on the latest release (0.2.0)" in r.stdout
    assert os.readlink(env.prefix / "previous") == "versions/0.1.0"


def test_unknown_version_and_offline_are_clear_errors(world, monkeypatch):
    r = invoke("update", "--version", "9.9.9")
    assert r.exit_code == 1 and "no release" in r.output.lower()
    from tests.test_m11_update import offline_client  # noqa: PLC0415

    monkeypatch.setattr(updatecheck, "http_client_factory", offline_client)
    r = invoke("update")
    assert r.exit_code == 1 and "Could not reach GitHub" in r.output


def offline_client() -> httpx.Client:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    return httpx.Client(transport=httpx.MockTransport(refuse))


def test_update_from_a_local_release_folder(world, monkeypatch, tmp_path):
    env = world["env"]
    rel = world["releases"]["0.2.0"]
    folder = tmp_path / "release"
    folder.mkdir()
    (folder / rel.tarball.name).write_bytes(rel.tarball.read_bytes())
    (folder / "SHA256SUMS").write_bytes(rel.sums)
    monkeypatch.setenv("FOUNDRYOS_DOWNLOAD_BASE", f"file://{folder}")
    r = invoke("update", "--version", "0.2.0")
    assert r.exit_code == 0, r.output
    assert current(env) == "versions/0.2.0"


def test_homebrew_source_and_docker_installs_print_instructions_and_change_nothing(
    env, monkeypatch, tmp_path
):
    r = invoke("update")  # this suite runs from a source checkout
    assert r.exit_code == 0 and "git pull" in r.stdout
    cellar = tmp_path / "brew" / "Cellar" / "foundry-os" / "0.1.0" / "libexec"
    write_bundle(cellar, "0.1.0")
    monkeypatch.setattr(install, "bundle_root", lambda: cellar)
    r = invoke("update")
    assert (
        r.exit_code == 0
        and "brew upgrade foundry-os && brew services restart foundry-os" in r.stdout
    )
    assert invoke("rollback").exit_code == 1
    monkeypatch.setattr(install, "in_container", lambda: True)
    monkeypatch.setattr(install, "bundle_root", lambda: None)
    assert "docker pull" in invoke("update").stdout


def test_update_restarts_a_running_server_and_reports_the_new_version(world, monkeypatch):
    env = world["env"]
    state = {"version": "0.1.0", "restarts": 0}
    monkeypatch.setattr(
        lifecycle,
        "probe_health",
        lambda port, **k: {"status": "ok", "auth_mode": "password", "version": state["version"]},
    )

    def fake_restart(settings, say=print, **k):
        state["restarts"] += 1
        state["version"] = "0.2.0"
        return lifecycle.run_state(settings)

    monkeypatch.setattr(lifecycle, "restart", fake_restart)
    r = invoke("update")
    assert r.exit_code == 0, r.output
    assert state["restarts"] == 1 and "0.2.0 is running" in r.stdout
    assert current(env) == "versions/0.2.0"


def test_a_failed_restart_points_at_rollback(world, monkeypatch):
    monkeypatch.setattr(
        lifecycle,
        "probe_health",
        lambda port, **k: {"status": "ok", "auth_mode": "password", "version": "0.1.0"},
    )

    def broken(settings, say=print, **k):
        raise lifecycle.LifecycleError("it did not answer")

    monkeypatch.setattr(lifecycle, "restart", broken)
    r = invoke("update")
    assert r.exit_code == 1 and "foundryos rollback" in r.output


def test_old_versions_beyond_current_and_previous_are_removed(world):
    env = world["env"]
    invoke("update", "--version", "0.2.0")
    invoke("update", "--version", "0.2.1")
    assert versions(env) == ["0.2.0", "0.2.1"]  # 0.1.0 pruned; previous = 0.2.0


# ------------------------------------------------------------------ rollback


def test_rollback_switches_back_and_a_second_rollback_goes_forward(world):
    env = world["env"]
    invoke("update")
    r = invoke("rollback")
    assert r.exit_code == 0, r.output
    assert current(env) == "versions/0.1.0"
    assert "Switched back to Foundry AI OS 0.1.0" in r.stdout
    assert invoke("rollback").exit_code == 0 and current(env) == "versions/0.2.0"


def test_rollback_without_an_earlier_version(world):
    r = invoke("rollback")
    assert r.exit_code == 5 and "no earlier version" in r.output


def _migrated_db(env: Env, revision: str) -> None:
    import sqlite3

    env.data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(env.data / "company_os.db")
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    conn.commit()
    conn.close()


def test_rollback_refuses_when_the_older_version_cannot_open_the_data(world):
    env = world["env"]
    invoke("update", "--version", "0.2.1")  # 0.2.1 knows migration 0002
    _migrated_db(env, "0002")
    r = invoke("rollback")
    assert r.exit_code == 1 and "cannot open your data" in r.output and "--restore-data" in r.output
    assert current(env) == "versions/0.2.1"


def test_rollback_restore_data_puts_the_pre_update_backup_back(world, monkeypatch):
    env = world["env"]
    invoke("update", "--version", "0.2.1")
    _migrated_db(env, "0002")
    # the safety backup the new version took before migrating
    from app.ops import upgrade
    from app.ops.backup import create_backup

    settings = Settings(_env_file=None)
    import sqlite3

    conn = sqlite3.connect(env.data / "company_os.db")
    conn.execute("UPDATE alembic_version SET version_num='0001'")
    conn.commit()
    conn.close()
    pre = settings.resolved_data_dir / "backups" / "pre-0.2.1-20261024-120000.tar.gz"
    create_backup(settings, pre)
    conn = sqlite3.connect(env.data / "company_os.db")
    conn.execute("UPDATE alembic_version SET version_num='0002'")
    conn.commit()
    conn.close()
    assert upgrade  # imported for the module under test
    restarted = []
    monkeypatch.setattr(
        lifecycle,
        "restart",
        lambda settings, say=print, **k: restarted.append(1) or lifecycle.run_state(settings),
    )
    r = invoke("rollback", "--restore-data", "--yes")
    assert r.exit_code == 0, r.output
    assert restarted == [1]
    assert current(env) == "versions/0.1.0"
    conn = sqlite3.connect(env.data / "company_os.db")
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
    conn.close()
    assert list(
        env.data.parent.glob("*pre-restore*")
    )  # the replaced data was moved aside, not deleted


def test_rollback_restore_data_without_a_backup(world):
    env = world["env"]
    invoke("update", "--version", "0.2.1")
    r = invoke("rollback", "--restore-data", "--yes")
    assert r.exit_code == 5 and "No automatic backup" in r.output
    assert current(env) == "versions/0.2.1"


# ------------------------------------------------------------------ versions


def test_version_ordering():
    assert updatecheck.is_newer("0.2.0", "0.1.0") and updatecheck.is_newer("v1.0.0", "0.9.9")
    assert updatecheck.is_newer("0.10.0", "0.9.0")  # numeric, not alphabetical
    assert not updatecheck.is_newer("0.1.0", "0.1.0") and not updatecheck.is_newer("0.0.9", "0.1.0")
    assert updatecheck.is_newer("1.0.0", "1.0.0-rc.1")  # a final release follows its pre-release
    assert not updatecheck.is_newer("garbage", "0.1.0")
    assert updatecheck.parse_version("1.2") == updatecheck.parse_version("1.2.0")


# ------------------------------------------------------------------ the daily check


def settings_for(env: Env) -> Settings:
    return Settings(_env_file=None)


def test_check_asks_github_once_a_day_and_caches(env, monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        fake_github({"0.2.0": make_release(tmp_path / "dl", "0.2.0")}, latest="0.2.0", calls=seen),
    )
    s = settings_for(env)
    t0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    first = updatecheck.status(s, now=t0)
    assert first["update_available"] and first["latest_version"] == "0.2.0" and len(seen) == 1
    assert first["current_version"] == install.APP_VERSION
    updatecheck.status(s, now=t0 + timedelta(hours=23))
    assert len(seen) == 1  # cached
    updatecheck.status(s, now=t0 + timedelta(hours=25))
    assert len(seen) == 2
    assert updatecheck.status(s, refresh="never")["latest_version"] == "0.2.0"
    assert len(seen) == 2
    cache = json.loads((env.data / "state" / "update-check.json").read_text())
    assert set(cache) >= {"checked_at", "latest_version", "release_url"}  # nothing identifying


def test_the_check_sends_only_a_versioned_user_agent(env, monkeypatch):
    headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        headers.update(request.headers)
        return httpx.Response(404, json={})

    real = updatecheck.make_client
    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), headers=real().headers),
    )
    updatecheck.status(settings_for(env))
    assert headers["user-agent"] == f"foundry-os/{install.APP_VERSION}"
    assert "authorization" not in headers and "cookie" not in headers


def test_no_release_yet_is_not_an_error(env, monkeypatch):
    monkeypatch.setattr(updatecheck, "http_client_factory", fake_github({}, latest=None))
    info = updatecheck.status(settings_for(env))
    assert (
        info["latest_version"] is None
        and info["update_available"] is False
        and info["error"] is None
    )


def test_a_failed_check_is_remembered_for_an_hour(env, monkeypatch):
    attempts = {"n": 0}

    def counting() -> httpx.Client:
        attempts["n"] += 1
        return offline_client()

    monkeypatch.setattr(updatecheck, "http_client_factory", counting)
    s = settings_for(env)
    t0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    info = updatecheck.status(s, now=t0)
    assert info["error"] and info["update_available"] is False
    updatecheck.status(s, now=t0 + timedelta(minutes=30))
    assert attempts["n"] == 1
    updatecheck.status(s, now=t0 + timedelta(minutes=61))
    assert attempts["n"] == 2


def test_opt_out_by_environment_and_by_preference(env, monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        fake_github({"0.2.0": make_release(tmp_path / "dl", "0.2.0")}, latest="0.2.0", calls=seen),
    )
    monkeypatch.setenv("FOUNDRYOS_UPDATE_CHECK", "0")
    off = updatecheck.status(settings_for(env))
    assert off["enabled"] is False and off["update_available"] is False and not seen
    monkeypatch.delenv("FOUNDRYOS_UPDATE_CHECK")
    assert invoke("update", "--no-auto-check").exit_code == 0
    s = settings_for(env)
    assert updatecheck.preference_enabled(s) is False
    assert updatecheck.status(s)["enabled"] is False and not seen
    # an explicit `update --check` still asks (and says so), because the user asked
    r = invoke("update", "--check", "--json")
    assert as_json(r)["latest_version"] == "0.2.0" and len(seen) == 1
    assert invoke("update", "--auto-check").exit_code == 0
    assert updatecheck.preference_enabled(settings_for(env)) is True


def test_update_check_command_output(env, monkeypatch, tmp_path):
    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        fake_github({"0.2.0": make_release(tmp_path / "dl", "0.2.0")}, latest="0.2.0"),
    )
    r = invoke("update", "--check")
    assert r.exit_code == 0 and "Update available: 0.2.0" in r.stdout and "git pull" in r.stdout
    monkeypatch.setattr(updatecheck, "http_client_factory", fake_github({}, latest=None))
    assert "No release is published yet" in invoke("update", "--check").stdout
    monkeypatch.setattr(updatecheck, "http_client_factory", offline_client)
    updatecheck.save_cache(settings_for(env), {})
    r = invoke("update", "--check")
    assert r.exit_code == 1 and "Could not check for updates" in r.output


def test_doctor_surfaces_the_cached_check_without_touching_the_network(env, monkeypatch):
    from app.ops import doctor

    s = settings_for(env)
    assert doctor.check_updates(s).status == "info"  # nothing cached yet, and no request made
    updatecheck.save_cache(
        s,
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "latest_version": "9.0.0",
            "release_url": None,
        },
    )
    c = doctor.check_updates(s)
    assert c.status == "warn" and "9.0.0" in c.detail and "To update:" in (c.fix or "")
    assert doctor.check_install(s).name == "install"


def test_launching_prints_a_cached_update_hint(env, monkeypatch):
    s = settings_for(env)
    updatecheck.save_cache(
        s, {"checked_at": datetime.now(UTC).isoformat(), "latest_version": "9.0.0"}
    )
    monkeypatch.setattr(
        lifecycle,
        "probe_health",
        lambda port, **k: {"status": "ok", "auth_mode": "password", "version": "0.1.0"},
    )
    r = invoke()
    assert r.exit_code == 0 and "Update available: Foundry AI OS 9.0.0" in r.stdout


def test_a_hostile_release_document_cannot_inject_a_link_or_a_version(env, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"tag_name": "v1.0.0", "html_url": "javascript:alert(1)", "assets": []}
        )

    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    info = updatecheck.status(settings_for(env))
    assert info["latest_version"] == "1.0.0" and info["release_url"] is None

    def weird(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "latest; rm -rf /", "assets": []})

    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        lambda: httpx.Client(transport=httpx.MockTransport(weird)),
    )
    cleared = updatecheck.status(settings_for(env), refresh="always")
    assert cleared["latest_version"] is None and cleared["update_available"] is False
