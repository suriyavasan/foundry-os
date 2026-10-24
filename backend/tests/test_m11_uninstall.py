"""M11: `foundryos uninstall [--purge]`: what it removes, what it must never touch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import install
from app.ops import lifecycle
from app.ops import uninstall as ops_uninstall
from tests.m11_helpers import (  # noqa: F401
    as_json,
    calls,
    env,
    fake_tool,
    installer_layout,
    invoke,
    write_bundle,
)


@pytest.fixture
def world(env, monkeypatch):
    installer_layout(env, monkeypatch)
    monkeypatch.setattr(lifecycle, "probe_health", lambda port, **k: None)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    # the links the installer makes
    bin_dir = env.home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("foundryos", "companyos"):
        (bin_dir / name).symlink_to(env.prefix / "current" / "bin" / "foundryos")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # data worth protecting
    (env.data / "logs").mkdir(parents=True)
    (env.data / "company_os.db").write_bytes(b"db")
    (env.data / "secret.key").write_text("k")
    return env


def test_uninstall_removes_the_program_and_keeps_the_data(world):
    env = world
    r = invoke("uninstall")
    assert r.exit_code == 0, r.output
    assert not (env.prefix / "versions").exists() and not (env.prefix / "current").exists()
    assert not (env.home / ".local" / "bin" / "foundryos").is_symlink()
    assert not (env.home / ".local" / "bin" / "companyos").is_symlink()
    assert (env.data / "company_os.db").read_bytes() == b"db"
    assert "Your data was kept" in r.stdout and "--purge" in r.stdout


def test_a_link_that_is_not_ours_is_left_alone(world, tmp_path):
    env = world
    other = tmp_path / "other" / "foundryos"
    other.parent.mkdir()
    other.write_text("#!/bin/sh\n")
    mine = env.home / ".local" / "bin" / "foundryos"
    mine.unlink()
    mine.symlink_to(other)
    invoke("uninstall")
    assert mine.is_symlink() and other.exists()  # points outside the install prefix: untouched
    assert not (env.home / ".local" / "bin" / "companyos").is_symlink()


def test_purge_needs_the_typed_confirmation(world):
    env = world
    r = invoke("uninstall", "--purge", input="nope\n")
    assert r.exit_code == 1 and "Not confirmed" in r.output
    assert (env.data / "company_os.db").exists() and (
        env.prefix / "versions"
    ).exists()  # nothing removed
    r = invoke("uninstall", "--purge", input="")  # no terminal / EOF
    assert r.exit_code == 1 and (env.data / "company_os.db").exists()
    r = invoke("uninstall", "--purge", input="delete my data\n")
    assert r.exit_code == 0, r.output
    assert not env.data.exists() and not (env.prefix / "versions").exists()


def test_purge_for_scripts_uses_the_explicit_flag(world):
    env = world
    r = invoke("uninstall", "--purge", "--yes-delete-my-data", "--json")
    assert r.exit_code == 0, r.output
    result = as_json(r)
    assert result["purged"] == str(env.data) and not env.data.exists()


def test_the_script_flag_alone_is_a_usage_error(world):
    r = invoke("uninstall", "--yes-delete-my-data")
    assert r.exit_code == 2 and (world.data / "company_os.db").exists()


@pytest.mark.parametrize("where", ["home", "root", "unrelated"])
def test_purge_refuses_folders_that_are_not_foundry_data(world, monkeypatch, tmp_path, where):
    env = world
    if where == "home":
        target = env.home
    elif where == "root":
        target = Path("/")
    else:
        target = tmp_path / "my-photos"
        target.mkdir()
        (target / "holiday.jpg").write_bytes(b"jpg")
    monkeypatch.setenv("FOUNDRYOS_DATA_DIR", str(target))
    r = invoke("uninstall", "--purge", "--yes-delete-my-data")
    assert r.exit_code == 1 and "Refusing to delete" in r.output
    assert target.exists()
    assert (env.prefix / "versions").exists()  # refused before anything was removed
    if where == "unrelated":
        assert (target / "holiday.jpg").exists()


def test_homebrew_installs_are_never_touched(env, monkeypatch, tmp_path):
    cellar = tmp_path / "brew" / "Cellar" / "foundry-os" / "0.1.0" / "libexec"
    write_bundle(cellar, "0.1.0")
    brew_bin = tmp_path / "brew" / "bin"
    brew_bin.mkdir()
    (brew_bin / "foundryos").symlink_to(cellar / "bin" / "foundryos")
    monkeypatch.setattr(install, "bundle_root", lambda: cellar)
    monkeypatch.setattr(lifecycle, "probe_health", lambda port, **k: None)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setenv("PATH", f"{brew_bin}{os.pathsep}{os.environ['PATH']}")
    env.data.mkdir()
    (env.data / "company_os.db").write_bytes(b"db")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "brew").rglob("*"))
    r = invoke("uninstall")
    assert r.exit_code == 0 and "brew uninstall foundry-os" in r.stdout
    after = sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "brew").rglob("*"))
    assert before == after  # not one Homebrew-managed file was touched
    assert (env.data / "company_os.db").exists()
    # even with --purge only the data goes; the Cellar and its links stay
    r = invoke("uninstall", "--purge", "--yes-delete-my-data")
    assert r.exit_code == 0 and not env.data.exists()
    assert (
        sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "brew").rglob("*")) == before
    )
    assert "brew uninstall foundry-os" in r.stdout


def test_a_source_checkout_removes_nothing(env):
    r = invoke("uninstall")
    assert r.exit_code == 0 and "source checkout" in r.stdout
    assert (install.REPO_DIR / "backend").exists()


def test_the_background_service_and_process_are_stopped_first(world, monkeypatch):
    env = world
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    plist = env.home / "Library" / "LaunchAgents" / "ai.foundry-os.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")
    fake_tool(env.bin, "launchctl", env.log)
    r = invoke("uninstall")
    assert r.exit_code == 0, r.output
    assert not plist.exists()
    assert any(c.startswith("launchctl bootout") for c in calls(env.log))


def test_purge_block_unit(tmp_path):
    assert ops_uninstall.purge_block(Path("/")) is not None
    assert ops_uninstall.purge_block(tmp_path / "does-not-exist-yet") is None
    empty = tmp_path / "empty-data"
    empty.mkdir()
    assert ops_uninstall.purge_block(empty) is None  # a data folder that never got anything is fine
    known = tmp_path / "data"
    (known / "state").mkdir(parents=True)
    assert ops_uninstall.purge_block(known) is None
