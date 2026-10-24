"""M9: `companyos doctor` checks, one at a time, with the outside world faked."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ops import doctor, pidfile


def settings_for(root: Path, **kw) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{root / 'company_os.db'}",
        data_dir=root,
        runtime_dir=root / "no-runtime",
        **kw,
    )


def fake_ollama(tags, ps):
    def fetch(path):
        return {"/api/tags": tags, "/api/ps": ps}.get(path)

    return fetch


def test_ollama_unreachable_says_how_to_start_it():
    c = doctor.check_ollama(fake_ollama(None, None))
    assert c.status == "warn" and "OLLAMA_CONTEXT_LENGTH=32768" in (c.fix or "")


def test_ollama_low_served_context_is_a_warning_with_the_fix():
    c = doctor.check_ollama(
        fake_ollama({"models": [{"name": "qwen2.5:7b"}]}, {"models": [{"context_length": 4096}]})
    )
    assert c.status == "warn" and "4096" in c.detail
    assert "OLLAMA_CONTEXT_LENGTH=32768" in (c.fix or "")


def test_ollama_good_context_and_unknown_context():
    ok = doctor.check_ollama(
        fake_ollama({"models": [{"name": "m"}]}, {"models": [{"context_length": 32768}]})
    )
    assert ok.status == "ok" and "32768" in ok.detail
    idle = doctor.check_ollama(fake_ollama({"models": [{"name": "m"}]}, {"models": []}))
    assert idle.status == "ok" and "unknown until" in idle.detail
    empty = doctor.check_ollama(fake_ollama({"models": []}, {"models": []}))
    assert empty.status == "warn" and "ollama pull" in (empty.fix or "")


def test_ollama_base_follows_ollama_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    assert doctor.ollama_base() == "http://127.0.0.1:11435"
    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434/")
    assert doctor.ollama_base() == "http://box:11434"
    monkeypatch.delenv("OLLAMA_HOST")
    assert doctor.ollama_base() == "http://127.0.0.1:11434"


def test_secret_file_permissions(tmp_path):
    s = settings_for(tmp_path)
    assert doctor.check_permissions(s).status == "info"  # nothing created yet
    (tmp_path / "owner.token").write_text("t" * 43)
    (tmp_path / "secret.key").write_text("k" * 64)
    os.chmod(tmp_path / "owner.token", 0o644)
    os.chmod(tmp_path / "secret.key", 0o600)
    bad = doctor.check_permissions(s)
    assert (
        bad.status == "fail"
        and "owner.token is mode 0644" in bad.detail
        and "chmod 600" in (bad.fix or "")
    )
    os.chmod(tmp_path / "owner.token", 0o600)
    assert doctor.check_permissions(s).status == "ok"


def test_disk_thresholds(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    usage = shutil._ntuple_diskusage  # type: ignore[attr-defined]
    for free, status in ((50 * 1024**3, "ok"), (1 * 1024**3, "warn"), (100 * 1024**2, "fail")):
        monkeypatch.setattr(
            doctor.shutil, "disk_usage", lambda p, free=free: usage(10**12, 0, free)
        )
        assert doctor.check_disk(s).status == status


def test_database_states(tmp_path, migrated_template):
    s = settings_for(tmp_path)
    assert doctor.check_database(s).status == "warn"  # no file yet
    sqlite3.connect(tmp_path / "company_os.db").close()
    assert "no migrations" in doctor.check_database(s).detail  # empty database
    (tmp_path / "company_os.db").unlink()
    shutil.copy(migrated_template, tmp_path / "company_os.db")
    ok = doctor.check_database(s)
    assert ok.status == "ok" and "migrations at head" in ok.detail
    conn = sqlite3.connect(tmp_path / "company_os.db")
    conn.execute("UPDATE alembic_version SET version_num='0001_old'")
    conn.commit()
    conn.close()
    behind = doctor.check_database(s)
    assert behind.status == "warn" and "alembic upgrade head" in (behind.fix or "")


def test_runtime_check(tmp_path):
    missing = doctor.check_runtime(settings_for(tmp_path))
    assert missing.status == "warn" and "uv sync" in (missing.fix or "")
    good = tmp_path / "good-python"
    good.write_text("#!/bin/sh\necho ok\n")
    good.chmod(0o755)
    assert doctor.check_runtime(settings_for(tmp_path, runtime_python=good)).status == "ok"
    bad = tmp_path / "bad-python"
    bad.write_text("#!/bin/sh\necho 'ModuleNotFoundError: company_agent' >&2\nexit 1\n")
    bad.chmod(0o755)
    broken = doctor.check_runtime(settings_for(tmp_path, runtime_python=bad))
    assert broken.status == "fail" and "ModuleNotFoundError" in broken.detail


def test_auth_gate_reports_the_blocking_reason(tmp_path, db_url):
    s = Settings(_env_file=None, database_url=db_url, argon2_time_cost=1, argon2_memory_kib=64)
    c = doctor.check_auth_gate(s)
    assert c.status in ("info", "ok") and "mode password" in c.detail
    local = Settings(_env_file=None, database_url=db_url, auth_mode="local")
    c = doctor.check_auth_gate(local)
    assert c.status == "warn" and "development only" in c.detail


def test_docker_and_python_checks_do_not_need_them(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.check_docker().status == "info"
    assert doctor.check_python().status == "warn"  # uv missing -> advice, not a failure
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor, "_run", lambda cmd, timeout=5.0: None)
    assert "daemon not running" in doctor.check_docker().detail


def test_a_crashing_check_does_not_hide_the_others(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    monkeypatch.setattr(doctor, "check_docker", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(doctor, "check_ollama", lambda: doctor.Check("ollama", "ok", "fake"))
    checks = doctor.run_checks(s)
    names = [c.name for c in checks]
    assert "python" in names and "ollama" in names and "disk" in names
    crashed = next(c for c in checks if "crashed" in c.detail)
    assert "boom" in crashed.detail


def test_pidfile_lifecycle(tmp_path):
    path = pidfile.register(tmp_path, "worker")
    assert path and path.exists()
    live = pidfile.live_processes(tmp_path)
    assert [(p["role"], p["pid"]) for p in live] == [("worker", os.getpid())]
    pidfile.unregister(path)
    assert pidfile.live_processes(tmp_path) == []
    (tmp_path / "run" / "garbage.json").write_text("{not json")
    assert pidfile.live_processes(tmp_path) == []  # unreadable files are removed, not fatal
    pidfile.unregister(None)  # no-op
    ro = tmp_path / "ro"
    ro.write_text("a file, not a directory")
    assert (
        pidfile.register(ro, "api") is None
    )  # a data dir that cannot hold run files must not stop startup


@pytest.mark.parametrize("name", ["api", "worker"])
def test_roles_are_free_text_for_forward_compat(tmp_path, name):
    p = pidfile.register(tmp_path, name, port=1)
    assert p and p.name == f"{name}.{os.getpid()}.json"
    pidfile.unregister(p)


def test_runtime_check_imports_from_the_runtime_dir(tmp_path, monkeypatch):
    """company_agent is importable only with cwd = runtime/ (how the worker launches it)."""
    python = tmp_path / "py"
    python.write_text("#!/bin/sh\necho ok\n")
    python.chmod(0o755)
    seen: dict = {}

    def fake_run(cmd, timeout=5.0, cwd=None):
        seen["cwd"] = cwd
        return doctor.subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(doctor, "_run", fake_run)
    s = settings_for(tmp_path, runtime_python=python)
    s.runtime_dir = tmp_path  # an existing runtime checkout
    assert doctor.check_runtime(s).status == "ok"
    assert seen["cwd"] == tmp_path
