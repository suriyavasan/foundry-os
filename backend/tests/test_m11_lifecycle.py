"""M11: `foundryos` with no arguments, start / stop / restart / open / logs, with fakes and with a real
detached stand-in server process."""

from __future__ import annotations

import contextlib
import os
import signal
import time

import pytest

from app.core.config import Settings
from app.ops import lifecycle, logs, pidfile
from tests.m11_helpers import (  # noqa: F401
    env,
    fake_launcher,
    free_port,
    invoke,
)

HEALTH = {"status": "ok", "database": "ok", "version": "0.1.0", "auth_mode": "password"}


class Opener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        return True


@pytest.fixture
def opener(monkeypatch, env):
    monkeypatch.delenv("FOUNDRYOS_NO_BROWSER")
    op = Opener()
    monkeypatch.setattr(lifecycle.webbrowser, "open", op)
    return op


class FakeProc:
    pid = 1234

    def __init__(self, code=None) -> None:
        self.code = code

    def poll(self):
        return self.code


@pytest.fixture
def world(env, monkeypatch):
    """Not running at first; `spawn` makes the fake server healthy."""
    state = {"up": False, "spawned": [], "proc": FakeProc()}
    monkeypatch.setattr(
        lifecycle, "probe_health", lambda port, **k: HEALTH if state["up"] else None
    )
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(lifecycle, "setup_pending", lambda port, **k: True)

    def spawn(cmd, *, env, cwd, log_file):
        state["spawned"].append((cmd, env, cwd, log_file))
        if state["proc"].code is None:
            state["up"] = True
        return state["proc"]

    monkeypatch.setattr(lifecycle, "spawn_detached", spawn)
    return state


def test_no_arguments_starts_in_the_background_and_opens_the_browser(world, opener, env):
    r = invoke()
    assert r.exit_code == 0, r.output
    (cmd, spawn_env, _cwd, log_file) = world["spawned"][0]
    assert cmd[-3:-1] == ["serve", "--port"] or cmd[-4:-2] == ["serve", "--port"]
    assert cmd[-1] == os.environ["FOUNDRYOS_PORT"]
    assert spawn_env["FOUNDRYOS_RUN_MODE"] == "background"
    assert log_file == env.data / "logs" / "foundry-os.log"
    assert opener.urls == [f"http://127.0.0.1:{os.environ['FOUNDRYOS_PORT']}"]
    assert "First run: create your account" in r.stdout


def test_when_it_is_already_running_it_only_opens(world, opener):
    world["up"] = True
    r = invoke()
    assert r.exit_code == 0 and not world["spawned"]
    assert opener.urls and "is running" in r.stdout


def test_companyos_alias_without_arguments_still_prints_help(world, monkeypatch):
    monkeypatch.setenv("FOUNDRYOS_PROG", "companyos")
    r = invoke()
    assert r.exit_code == 0 and "Usage:" in r.stdout and not world["spawned"]


def test_start_does_not_open_the_browser(world, opener):
    r = invoke("start")
    assert r.exit_code == 0 and world["spawned"] and not opener.urls
    assert invoke("start").stdout.count("is running") >= 1  # idempotent
    assert len(world["spawned"]) == 1


def test_open_needs_a_running_server(world, opener):
    r = invoke("open")
    assert r.exit_code == 5 and "not running" in r.output and not opener.urls
    world["up"] = True
    assert invoke("open").exit_code == 0 and len(opener.urls) == 1


def test_a_start_that_dies_shows_the_log_tail(world, env):
    world["proc"] = FakeProc(code=1)
    env.data.joinpath("logs").mkdir(parents=True)
    env.data.joinpath("logs", "foundry-os.log").write_text("boom: migration exploded\n")
    r = invoke("start")
    assert r.exit_code == 1
    assert "stopped right after starting" in r.output and "migration exploded" in r.output


def test_a_start_that_never_answers_times_out(world, monkeypatch):
    monkeypatch.setattr(lifecycle, "START_TIMEOUT", 0.3)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: time.sleep(0.05))
    world["proc"] = FakeProc()
    monkeypatch.setattr(lifecycle, "probe_health", lambda port, **k: None)
    r = invoke("start")
    assert r.exit_code == 1 and "did not answer" in r.output


def test_a_busy_port_is_reported(world, monkeypatch):
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    r = invoke("start")
    assert r.exit_code == 1 and "in use by another program" in r.output and not world["spawned"]


def test_stop_when_nothing_runs(world):
    r = invoke("stop")
    assert r.exit_code == 0 and "not running" in r.stdout


def test_stop_refuses_to_guess_about_a_server_it_did_not_start(world):
    world["up"] = True  # answers, but there is no run file and no service
    r = invoke("stop")
    assert r.exit_code == 1 and "not started by this install" in r.output


def test_stop_with_all_is_still_the_emergency_stop_and_reason_needs_all(world):
    r = invoke("stop", "--reason", "x")
    assert r.exit_code == 2 and "--all" in r.output


# ------------------------------------------------------------------ real processes


@pytest.fixture
def real(env, monkeypatch, tmp_path):
    launcher = fake_launcher(tmp_path)
    monkeypatch.setenv("FOUNDRYOS_LAUNCHER", str(launcher))
    monkeypatch.setenv("FOUNDRYOS_PORT", str(free_port()))
    yield
    settings = Settings(_env_file=None)
    for entry in pidfile.live_processes(settings.resolved_data_dir):
        with contextlib.suppress(OSError):
            os.kill(int(entry["pid"]), signal.SIGKILL)


def test_start_restart_stop_with_a_real_detached_process(real, env):
    settings = Settings(_env_file=None)
    r = invoke("start")
    assert r.exit_code == 0, r.output
    state = lifecycle.run_state(settings)
    assert state.running and state.managed_by == "background" and state.version == "9.9.9"
    first = state.supervisor_pid
    assert first and first != os.getpid()
    assert "is running in the background" in invoke("service", "status").stdout

    r = invoke("restart")
    assert r.exit_code == 0, r.output
    second = lifecycle.run_state(settings).supervisor_pid
    assert second and second != first and not lifecycle._alive(first)

    r = invoke("stop")
    assert r.exit_code == 0 and "Stopping" in r.stdout
    assert not lifecycle.run_state(settings).running and not lifecycle._alive(second)
    assert "not running" in invoke("stop").stdout


def test_a_stuck_supervisor_is_killed_after_the_grace_period(real, env, monkeypatch):
    settings = Settings(_env_file=None)
    invoke("start")
    pid = lifecycle.run_state(settings).supervisor_pid
    assert pid
    os.kill(pid, signal.SIGSTOP)  # cannot handle SIGTERM while stopped
    monkeypatch.setattr(lifecycle, "STOP_TIMEOUT", 0.5)
    r = invoke("stop")
    assert r.exit_code == 0 and "forcing it" in r.stdout
    deadline = time.monotonic() + 5
    while lifecycle._alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not lifecycle._alive(pid)


# ------------------------------------------------------------------ logs


def test_logs_prints_the_tail_and_follows(env, monkeypatch):
    path = env.data / "logs" / "foundry-os.log"
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {n}\n" for n in range(1, 11)))
    r = invoke("logs", "-n", "3")
    assert r.exit_code == 0 and r.stdout.splitlines() == ["line 8", "line 9", "line 10"]
    seen: list[str] = []
    ticks = {"n": 0}

    def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] == 1:
            with path.open("a") as fh:
                fh.write("fresh 1\nfresh 2\n")
        if ticks["n"] == 3:
            path.write_text("")  # rotated
        if ticks["n"] == 4:
            path.write_text("after rotation\n")

    logs.follow(path, seen.append, stop=lambda: ticks["n"] >= 6, sleep=sleep)
    assert seen[:2] == ["fresh 1", "fresh 2"] and seen[-1] == "after rotation"


def test_logs_without_a_log_says_so(env):
    r = invoke("logs")
    assert r.exit_code == 0 and "No log yet" in r.stdout


def test_rotation_copies_and_truncates_and_keeps_five(tmp_path):
    path = tmp_path / "foundry-os.log"
    for n in range(8):
        path.write_text(f"generation {n}\n" + "x" * 50)
        assert logs.rotate_if_large(path, max_bytes=10, keep=5) is True
        assert path.read_text() == ""
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [f"foundry-os.log.{n}" for n in range(1, 6)] + ["foundry-os.log"] or sorted(
        names
    ) == sorted(["foundry-os.log"] + [f"foundry-os.log.{n}" for n in range(1, 6)])
    assert (tmp_path / "foundry-os.log.1").read_text().startswith("generation 7")
    assert (tmp_path / "foundry-os.log.5").read_text().startswith("generation 3")
    small = tmp_path / "small.log"
    small.write_text("tiny")
    assert logs.rotate_if_large(small, max_bytes=1000) is False


def test_the_rotator_trims_a_large_log_at_start_and_stops(tmp_path):
    path = tmp_path / "foundry-os.log"
    path.write_text("x" * 100)
    rot = logs.Rotator(path, max_bytes=50, keep=2, every=0.05).start()
    try:
        assert path.read_text() == "" and (tmp_path / "foundry-os.log.1").read_text() == "x" * 100
        path.write_text("y" * 100)
        deadline = time.monotonic() + 3
        while path.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert path.read_text() == "" and (tmp_path / "foundry-os.log.1").read_text() == "y" * 100
    finally:
        rot.stop()
