"""M9: `companyos up` supervision logic (fake processes), web freshness, access lines, one real-process test."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ops import up
from app.ops.up import (
    ManagedChild,
    Supervisor,
    UpOptions,
    child_env,
    describe_access,
    web_status,
)


class FakeProc:
    _next_pid = 1000

    def __init__(self, name: str, *, stubborn: bool = False) -> None:
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.name = name
        self.code: int | None = None
        self.stubborn = stubborn
        self.calls: list[str] = []

    def poll(self):
        return self.code

    def terminate(self):
        self.calls.append("terminate")
        if not self.stubborn:
            self.code = 0

    def kill(self):
        self.calls.append("kill")
        self.code = -9

    def die(self, code: int = 1):
        self.code = code


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_supervisor(
    *children: ManagedChild, clock: Clock | None = None, **kw
) -> tuple[Supervisor, Clock]:
    clock = clock or Clock()
    return Supervisor(
        list(children), say=lambda _l: None, clock=clock, sleep=clock.sleep, **kw
    ), clock


def spawner(log: list[FakeProc], name: str, **kw):
    def start():
        p = FakeProc(name, **kw)
        log.append(p)
        return p

    return start


# ------------------------------------------------------------------ supervisor


def test_crashed_worker_is_restarted_with_exponential_backoff():
    procs: list[FakeProc] = []
    worker = ManagedChild("worker", spawner(procs, "worker"))
    sup, clock = make_supervisor(worker)
    sup.start_all()
    delays = []
    for _ in range(5):
        procs[-1].die(1)
        before = clock.now
        assert sup.step() is None  # notices the crash, schedules the restart
        assert worker.proc is None
        assert sup.step() is None  # too early: still waiting
        assert worker.proc is None
        while worker.proc is None:  # time passes until the backoff is over
            clock.sleep(0.5)
            sup.step()
        delays.append(round(clock.now - before))
    assert delays == [1, 2, 4, 8, 16]
    assert worker.restarts == 5 and len(procs) == 6
    assert any("restarting in 1s" in e for e in sup.events)


def test_backoff_is_capped_and_resets_after_a_stable_run():
    procs: list[FakeProc] = []
    worker = ManagedChild("worker", spawner(procs, "worker"))
    sup, clock = make_supervisor(worker, backoff_cap=10.0, stable_after=60.0)
    sup.start_all()
    for _ in range(6):  # crash loop
        procs[-1].die(2)
        sup.step()
        clock.sleep(11)
        sup.step()
    assert worker.failures == 6
    assert Supervisor.delay_for(worker.failures, 10.0) == 10.0  # capped
    clock.sleep(61)  # it then stays up for a minute
    sup.step()
    assert worker.failures == 0
    procs[-1].die(1)
    sup.step()
    assert "restarting in 1s" in sup.events[-1]


def test_a_crash_after_a_long_run_is_treated_as_a_first_failure():
    procs: list[FakeProc] = []
    worker = ManagedChild("worker", spawner(procs, "worker"))
    sup, clock = make_supervisor(worker, stable_after=30.0)
    sup.start_all()
    worker.failures = 5
    clock.sleep(120)
    procs[-1].die(1)
    sup.step()
    assert worker.failures == 1


def test_critical_child_exit_stops_everything():
    procs: list[FakeProc] = []
    api = ManagedChild("api", spawner(procs, "api"), restart=False, critical=True)
    worker = ManagedChild("worker", spawner(procs, "worker"))
    sup, _ = make_supervisor(api, worker)
    sup.start_all()
    assert sup.step() is None
    procs[0].die(3)
    assert sup.step() == "api exited with code 3"


def test_non_restarting_child_is_just_reported():
    procs: list[FakeProc] = []
    child = ManagedChild("job", spawner(procs, "job"), restart=False)
    sup, clock = make_supervisor(child)
    sup.start_all()
    procs[0].die(0)
    assert sup.step() is None
    clock.sleep(1000)
    assert sup.step() is None and len(procs) == 1


def test_shutdown_is_in_reverse_start_order_and_kills_the_stubborn():
    order: list[str] = []
    procs: dict[str, FakeProc] = {}

    class Tracked(FakeProc):
        def terminate(self):
            order.append(self.name)
            super().terminate()

    def start(name, **kw):
        def go():
            procs[name] = Tracked(name, **kw)
            return procs[name]

        return go

    sup, _ = make_supervisor(
        ManagedChild("api", start("api"), critical=True),
        ManagedChild("worker", start("worker", stubborn=True)),
        ManagedChild("web-dev", start("web-dev")),
    )
    sup.start_all()
    sup.stop_all(grace=3.0)
    assert order == ["web-dev", "worker", "api"]
    assert procs["worker"].calls == [
        "terminate",
        "kill",
    ]  # ignored SIGTERM -> killed after the grace period
    assert procs["api"].calls == ["terminate"]
    assert any("worker did not stop" in e for e in sup.events)
    sup.stop_all()  # idempotent


def test_run_returns_on_stop_event_and_on_critical_exit():
    procs: list[FakeProc] = []
    api = ManagedChild("api", spawner(procs, "api"), restart=False, critical=True)
    sup, _ = make_supervisor(api)
    sup.start_all()
    stop = threading.Event()
    ticks = {"n": 0}
    real_sleep = sup.sleep

    def counting_sleep(s):
        ticks["n"] += 1
        if ticks["n"] == 3:
            stop.set()
        real_sleep(s)

    sup.sleep = counting_sleep
    assert sup.run(stop) is None and ticks["n"] == 3
    procs[0].die(9)
    assert sup.run(threading.Event()) == "api exited with code 9"


def test_real_processes_restart_and_terminate():
    """No fakes: a child that keeps crashing is restarted, a long-lived one is stopped on request."""
    flaky = ManagedChild(
        "flaky",
        lambda: up._popen(
            [sys.executable, "-c", "import sys; sys.exit(3)"], env=dict(os.environ), cwd=Path.cwd()
        ),
    )
    steady = ManagedChild(
        "steady",
        lambda: up._popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env=dict(os.environ),
            cwd=Path.cwd(),
        ),
        restart=False,
    )
    sup = Supervisor([steady, flaky], say=lambda _l: None, backoff_cap=0.05, poll_interval=0.02)
    sup.start_all()
    steady_proc = steady.proc
    assert steady_proc is not None
    deadline = time.monotonic() + 15
    while flaky.restarts < 2 and time.monotonic() < deadline:
        sup.step()
        time.sleep(0.02)
    assert flaky.restarts >= 2
    sup.stop_all(grace=10)
    assert steady_proc.poll() is not None  # SIGTERM took it down
    assert flaky.proc is None


# ------------------------------------------------------------------ web freshness


def test_web_status(tmp_path: Path):
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "App.tsx").write_text("x")
    (web / "package.json").write_text("{}")
    assert web_status(web) == "missing"
    dist = web / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>")
    now = time.time()
    os.utime(web / "src" / "App.tsx", (now - 100, now - 100))
    os.utime(web / "package.json", (now - 100, now - 100))
    os.utime(dist / "index.html", (now, now))
    assert web_status(web) == "fresh"
    os.utime(web / "src" / "App.tsx", (now + 50, now + 50))
    assert web_status(web) == "stale"
    (web / "node_modules").mkdir()
    (web / "node_modules" / "ignored.js").write_text("x")
    os.utime(web / "src" / "App.tsx", (now - 100, now - 100))
    assert web_status(web) == "fresh"  # node_modules is not a source
    # a packaged install ships dist without sources: nothing to compare against
    packaged = tmp_path / "pkg" / "dist"
    packaged.mkdir(parents=True)
    (packaged / "index.html").write_text("<html>")
    assert web_status(tmp_path / "pkg", packaged) == "unknown"
    assert web_status(web, tmp_path / "elsewhere") == "missing"


def test_build_web_never_raises(tmp_path: Path, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(up, "_which", lambda name: None)
    assert up.build_web(tmp_path, said.append) is False and "pnpm is not installed" in said[0]
    monkeypatch.setattr(up, "_which", lambda name: "/usr/bin/pnpm")
    (tmp_path / "package.json").write_text("{}")
    calls: list[list[str]] = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, cwd, check):
        calls.append(cmd[1:])
        return R(0)

    assert up.build_web(tmp_path, said.append, run=fake_run) is True
    assert calls == [["install", "--frozen-lockfile"], ["build"]]
    (tmp_path / "node_modules").mkdir()
    calls.clear()
    assert up.build_web(tmp_path, said.append, run=fake_run) is True and calls == [["build"]]
    assert up.build_web(tmp_path, said.append, run=lambda cmd, cwd, check: R(1)) is False

    def boom(cmd, cwd, check):
        raise OSError("nope")

    assert up.build_web(tmp_path, said.append, run=boom) is False


# ------------------------------------------------------------------ environment & access lines


def test_child_env_follows_the_port_and_never_embeds_the_worker(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/x.db")
    env = child_env(
        settings, UpOptions(port=8710), base={"PATH": "/bin", "COMPANY_OS_EMBEDDED_WORKER": "true"}
    )
    assert env["COMPANY_OS_PORT"] == "8710"
    assert env["COMPANY_OS_EMBEDDED_WORKER"] == "false"
    assert env["COMPANY_OS_AGENT_API_URL"] == "http://127.0.0.1:8710/api/agent/v1"
    assert env["COMPANY_OS_PUBLIC_URL"] == "http://localhost:8710"
    assert env["COMPANY_OS_PRINT_LOGIN_URL"] == "0" and env["PATH"] == "/bin"
    keep = child_env(
        settings, UpOptions(port=8710), base={"COMPANY_OS_PUBLIC_URL": "https://corp.example"}
    )
    assert keep["COMPANY_OS_PUBLIC_URL"] == "https://corp.example"  # operator's choice wins
    assert "SECRET" not in "".join(env)


def test_first_run_prints_the_setup_url_and_the_token_only_when_remote_needs_it():
    setup = {"state": "setup_required"}
    local = describe_access(8710, "127.0.0.1", None, setup, "T" * 43, tty=True)
    text = "\n".join(local)
    assert "http://127.0.0.1:8710" in text and "create your account" in text
    assert "T" * 43 not in text  # loopback-only: the setup token is not needed, not printed

    lan_reach = {
        "lan": {
            "addresses": [{"url": "http://192.168.1.20:8710"}],
            "mdns_url": "http://mac.local:8710",
        },
        "tunnel": {},
    }
    remote = "\n".join(describe_access(8710, "0.0.0.0", lan_reach, setup, "T" * 43, tty=True))
    assert "http://192.168.1.20:8710" in remote and "http://mac.local:8710" in remote
    assert "T" * 43 in remote and "not needed on this machine" in remote
    no_tty = "\n".join(describe_access(8710, "0.0.0.0", lan_reach, setup, "T" * 43, tty=False))
    assert "T" * 43 not in no_tty and "owner.token" in no_tty  # never into logs / pipes

    tunnel = {"lan": {"addresses": []}, "tunnel": {"url": "https://x.trycloudflare.com"}}
    via_tunnel = "\n".join(describe_access(8710, "127.0.0.1", tunnel, setup, "T" * 43, tty=True))
    assert "https://x.trycloudflare.com" in via_tunnel and "T" * 43 in via_tunnel

    later = "\n".join(
        describe_access(8710, "127.0.0.1", None, {"state": "unauthenticated"}, "T" * 43, tty=True)
    )
    assert "First run" not in later and "T" * 43 not in later


# ------------------------------------------------------------------ the sequence


@pytest.fixture
def seq(monkeypatch, tmp_path):
    """run_up with every external effect replaced; returns the recorded events."""
    rec = {"spawned": [], "said": [], "migrated": 0, "procs": {}}

    monkeypatch.setattr(
        up, "ensure_migrated", lambda s: rec.__setitem__("migrated", rec["migrated"] + 1)
    )
    monkeypatch.setattr(up, "port_in_use", lambda port, host="127.0.0.1": rec.get("busy", False))
    monkeypatch.setattr(up, "web_status", lambda web_dir, dist=None: rec.get("web", "fresh"))
    monkeypatch.setattr(
        up, "build_web", lambda web_dir, say, run=None: rec.setdefault("built", True)
    )
    monkeypatch.setattr(up, "_print_ready", lambda settings, opts, host, say: say("READY"))
    monkeypatch.setattr(
        "app.serve.decide_host", lambda explicit: (explicit or "127.0.0.1", rec.get("warn"))
    )

    def healthy(port, proc, **kw):
        return rec.get("healthy", True)

    monkeypatch.setattr(up, "wait_healthy", healthy)

    def spawn(cmd, *, env, cwd):
        name = "worker" if "app.engine.cli" in cmd else "web-dev" if cmd[-1] == "dev" else "api"
        proc = FakeProc(name)
        rec["spawned"].append((name, cmd, env))
        rec["procs"][name] = proc
        return proc

    rec["spawn"] = spawn
    rec["settings"] = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/x.db")
    return rec


def run(seq, opts: UpOptions, *, after=None) -> int:
    stop = threading.Event()
    ticks = {"n": 0}
    clock = Clock()

    def sleep(s):
        ticks["n"] += 1
        clock.sleep(max(s, 1.0))  # every supervision pass is a second of fake time
        if after:
            after(ticks["n"], stop)

    return up.run_up(
        opts,
        settings=seq["settings"],
        spawn=seq["spawn"],
        say=seq["said"].append,
        stop=stop,
        install_signals=False,
        sleep=sleep,
        clock=clock,
    )


def test_up_starts_api_then_worker_and_shuts_down_cleanly(seq):
    code = run(seq, UpOptions(port=8710), after=lambda n, stop: stop.set() if n >= 2 else None)
    assert code == 0
    names = [s[0] for s in seq["spawned"]]
    assert names == ["api", "worker"]
    assert seq["migrated"] == 1
    api_cmd = seq["spawned"][0][1]
    assert api_cmd[1:3] == ["-m", "app.serve"] and "--port" in api_cmd and "8710" in api_cmd
    worker_env = seq["spawned"][1][2]
    assert worker_env["COMPANY_OS_PORT"] == "8710"
    assert worker_env["COMPANY_OS_AGENT_API_URL"].endswith(":8710/api/agent/v1")
    assert "READY" in seq["said"] and "\nShutting down..." in seq["said"]
    assert seq["procs"]["api"].calls == ["terminate"] and seq["procs"]["worker"].calls == [
        "terminate"
    ]


def test_up_options_no_worker_host_and_dev(seq):
    run(seq, UpOptions(port=8710, no_worker=True, host="0.0.0.0"), after=lambda n, s: s.set())
    assert [s[0] for s in seq["spawned"]] == ["api"]
    assert seq["spawned"][0][1][-2:] == ["--host", "0.0.0.0"]
    seq["spawned"].clear()
    run(seq, UpOptions(port=8710, dev=True), after=lambda n, s: s.set())
    assert [s[0] for s in seq["spawned"]] == ["api", "worker", "web-dev"]
    assert seq["spawned"][2][2]["API_PROXY_TARGET"] == "http://127.0.0.1:8710"
    assert "built" not in seq  # --dev never builds web/dist


def test_up_builds_a_missing_or_stale_ui_but_continues_if_that_fails(seq):
    seq["web"] = "stale"
    run(seq, UpOptions(port=8710), after=lambda n, s: s.set())
    assert seq["built"] is True
    seq.pop("built")
    seq["web"] = "fresh"
    run(seq, UpOptions(port=8710), after=lambda n, s: s.set())
    assert "built" not in seq


def test_up_refuses_a_busy_port_and_reports_unhealthy_api(seq):
    seq["busy"] = True
    assert run(seq, UpOptions(port=8710)) == 1
    assert seq["spawned"] == [] and seq["migrated"] == 0
    assert any("already in use" in s for s in seq["said"])
    seq["busy"] = False
    seq["healthy"] = False
    assert run(seq, UpOptions(port=8710)) == 1
    assert [s[0] for s in seq["spawned"]] == [
        "api"
    ]  # the worker never starts without a healthy API
    assert seq["procs"]["api"].calls == ["terminate"]  # and the half-started API is cleaned up


def test_up_stops_everything_when_the_api_dies_and_restarts_a_crashed_worker(seq):
    def chaos(n, stop):
        if n == 1:
            seq["procs"]["worker"].die(1)  # crash: supervised, restarted
        if n == 6:
            seq["procs"]["api"].die(7)  # the API is critical

    code = run(seq, UpOptions(port=8710), after=chaos)
    assert code == 1
    assert any("Stopping: api exited with code 7" in s for s in seq["said"])
    assert [s[0] for s in seq["spawned"]].count("worker") >= 2  # restarted at least once
    assert seq["procs"]["worker"].calls == ["terminate"]


def test_up_passes_a_decide_host_warning_through(seq):
    seq["warn"] = "Not binding to 0.0.0.0: sign-in is not set up. Serving on 127.0.0.1 only."
    run(seq, UpOptions(port=8710), after=lambda n, s: s.set())
    assert seq["warn"] in seq["said"]
