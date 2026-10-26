"""The public URL of a headless install: every new quick-tunnel URL is remembered, audited, logged and
written to <data>/public-url.json; the tunnel child is registered in run/ and reaped when stale; a
quick tunnel gets an isolated HOME; a tunnelled request is never "loopback". A real cloudflared is
never needed (or run): the binary is a script on a path."""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.integrations.tunnel import (
    TunnelSpec,
    TunnelSupervisor,
    is_ephemeral_host,
    reap_stale_tunnels,
)
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.guardrails import SystemState
from app.ops import pidfile
from app.runtimes.registry import RuntimeRegistry
from app.services import reachability
from tests.m8_helpers import claim_owner, fake_cloudflared, wait_for, write_script

URL1 = "https://a-b.trycloudflare.com"
URL2 = "https://c-d.trycloudflare.com"
ACTION = "reachability.tunnel_url_changed"


def changing_cloudflared(path, tmp_path, urls=(URL1, URL2), first_run_seconds=0.4):
    """Prints urls[n] on its n-th start. The first run ends after a moment (the supervisor restarts
    it); later runs stay up. Records the HOME it was given."""
    return write_script(
        path,
        f"""
        import os, sys, time
        from pathlib import Path
        if "--version" in sys.argv:
            print("cloudflared version 2026.10.3"); sys.exit(0)
        counter = Path({str(tmp_path / "starts")!r})
        n = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(n + 1))
        Path({str(tmp_path / "home.txt")!r}).write_text(os.environ.get("HOME", ""))
        urls = {list(urls)!r}
        print("INF |  " + urls[min(n, len(urls) - 1)], flush=True)
        if n == 0:
            time.sleep({first_run_seconds!r})
            sys.exit(1)
        while True:
            time.sleep(0.05)
        """,
    )


@pytest.fixture
def world(app, client, company, m8, tmp_path):
    """An owner with a strong password, a company, a URL-changing fake cloudflared in data/bin and a
    supervisor that restarts and polls quickly."""
    claim_owner(app)
    m8.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    changing_cloudflared(m8.settings.resolved_bin_dir / "cloudflared", tmp_path)
    sup = m8.ic.supervisor
    sup.base_backoff, sup.max_backoff, sup.poll_interval = 0.02, 0.05, 0.02
    sup._health_check = lambda url: True
    m8.company_id = company["id"]
    m8.data = m8.settings.resolved_data_dir
    yield m8
    sup.stop()


def public_file(m8):
    path = m8.data / "public-url.json"
    return json.loads(path.read_text()) if path.exists() else None


def url_events(app, company_id):
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.company_id == company_id, AuditEvent.action == ACTION)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )


def url_state(app):
    with app.state.session_factory() as db:
        row = db.get(SystemState, reachability.URL_STATE_KEY)
        return dict(row.value) if row else {}


# ------------------------------------------------------------------ recording a URL change


def test_each_url_change_is_recorded_exactly_once(client, app, world, caplog):
    caplog.set_level(logging.INFO, logger="company_os.reachability")
    r = client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    assert r.status_code == 200, r.text
    wait_for(lambda: (public_file(world) or {}).get("url") == URL2, what="the second URL")

    state = url_state(app)
    assert state["last_url"] == URL2 and state["previous_url"] == URL1
    assert [h["url"] for h in state["history"]] == [URL2, URL1] and state["url_changed_at"]
    events = url_events(app, world.company_id)
    assert [e.data["url"] for e in events] == [URL1, URL2]
    assert [e.data["previous_url"] for e in events] == [None, URL1]
    assert all(e.actor_type == "system" and e.target_type == "reachability" for e in events)
    announced = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Public URL")]
    assert announced == [f"Public URL: {URL1}", f"Public URL: {URL2}"]

    body = public_file(world)
    assert body["state"] == "running" and body["provider"] == "cloudflare_quick"
    assert body["previous_url"] == URL1 and body["changed_at"] == state["url_changed_at"]
    path = world.data / "public-url.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent == world.data and not (world.data / "run" / "public-url.json").exists()
    assert not list(world.data.glob(".public-url-*"))  # the temp file is renamed, not left behind

    # the API reports it, and says the address is ephemeral
    tunnel = client.get("/api/reachability").json()["tunnel"]
    assert tunnel["ephemeral"] is True and tunnel["last_url"] == URL2
    assert tunnel["url_changed_at"] == state["url_changed_at"][:19] + "Z"

    # stopping retracts the URL from the file but keeps the history
    assert client.post("/api/reachability/tunnel/stop").status_code == 200
    stopped = public_file(world)
    assert stopped["state"] == "stopped" and stopped["url"] is None
    assert stopped["previous_url"] == URL2
    tunnel = client.get("/api/reachability").json()["tunnel"]
    assert tunnel["state"] == "stopped" and tunnel["last_url"] == URL2 and tunnel["ephemeral"]
    assert len(url_events(app, world.company_id)) == 2  # a stop is not a URL change


def test_an_unchanged_url_writes_nothing(client, app, world, caplog):
    caplog.set_level(logging.INFO, logger="company_os.reachability")
    running = {"state": "running", "url": URL1, "provider": "cloudflare_quick", "healthy": None}
    reachability.on_tunnel_change(app.state.session_factory, world.ic, running)
    before = (
        json.dumps(url_state(app), sort_keys=True),
        len(url_events(app, world.company_id)),
        (world.data / "public-url.json").stat().st_mtime_ns,
        len(caplog.records),
    )
    # a health flip, the same URL again, and a restart that comes back on the same URL
    reachability.on_tunnel_change(app.state.session_factory, world.ic, {**running, "healthy": True})
    reachability.on_tunnel_change(app.state.session_factory, world.ic, running)
    after = (
        json.dumps(url_state(app), sort_keys=True),
        len(url_events(app, world.company_id)),
        (world.data / "public-url.json").stat().st_mtime_ns,
        len(caplog.records),
    )
    assert after == before
    starting = {"state": "starting", "url": None, "provider": "cloudflare_quick"}
    reachability.on_tunnel_change(app.state.session_factory, world.ic, starting)
    assert public_file(world)["state"] == "starting" and public_file(world)["url"] is None
    reachability.on_tunnel_change(app.state.session_factory, world.ic, running)
    assert public_file(world)["url"] == URL1
    assert (
        len(url_events(app, world.company_id)) == 1 and url_state(app)["history"][0]["url"] == URL1
    )
    assert len(url_state(app)["history"]) == 1


def test_only_the_last_five_urls_are_kept(client, app, world):
    for n in range(7):
        status = {"state": "running", "url": f"https://u{n}.trycloudflare.com", "provider": "x"}
        reachability.on_tunnel_change(app.state.session_factory, world.ic, status)
    history = url_state(app)["history"]
    assert [h["url"] for h in history] == [
        f"https://u{n}.trycloudflare.com" for n in range(6, 1, -1)
    ]
    assert len(url_events(app, world.company_id)) == 7


def test_a_stopped_tunnel_that_never_ran_leaves_no_file(client, app, world):
    reachability.on_tunnel_change(
        app.state.session_factory, world.ic, {"state": "stopped", "url": None, "provider": None}
    )
    assert public_file(world) is None and url_state(app) == {}


def test_a_failing_listener_never_raises(app, world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(reachability, "_record_tunnel_change", boom)
    reachability.on_tunnel_change(app.state.session_factory, world.ic, {"state": "running"})


def test_the_url_audit_event_reaches_only_the_owners_companies(client, app, world):
    other = client.post("/api/companies", json={"name": "Second", "mission": "x"}).json()
    reachability.on_tunnel_change(
        app.state.session_factory,
        world.ic,
        {"state": "running", "url": URL1, "provider": "cloudflare_quick"},
    )
    assert len(url_events(app, world.company_id)) == 1 and len(url_events(app, other["id"])) == 1


def test_ephemeral_hosts():
    assert is_ephemeral_host("abc-def.trycloudflare.com")
    assert is_ephemeral_host("ABC.TryCloudflare.com.")
    assert not is_ephemeral_host("app.example.com")
    assert not is_ephemeral_host("trycloudflare.com.evil.example")
    assert not is_ephemeral_host(None)


# ------------------------------------------------------------------ the child process


def test_the_tunnel_pid_is_registered_while_it_runs_and_removed_after(client, app, world):
    run = world.data / "run"
    client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    wait_for(lambda: list(run.glob("tunnel.*.json")), what="the tunnel run file")
    # the first run ends after 0.4 s and is replaced: the registry always names the live child
    wait_for(lambda: (public_file(world) or {}).get("url") == URL2, what="the restart")
    proc = world.ic.supervisor._proc
    files = list(run.glob("tunnel.*.json"))
    assert [f.name for f in files] == [f"tunnel.{proc.pid}.json"]
    info = json.loads(files[0].read_text())
    assert info["role"] == "tunnel" and info["pid"] == proc.pid and info["binary"] == "cloudflared"
    assert info["parent"] == os.getpid() and info["provider"] == "cloudflare_quick"
    assert any(p["pid"] == proc.pid for p in pidfile.live_processes(world.data))
    client.post("/api/reachability/tunnel/stop")
    assert not list(run.glob("tunnel.*.json"))


def test_the_quick_tunnel_child_gets_an_isolated_home(client, app, world, tmp_path):
    client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    wait_for(lambda: (tmp_path / "home.txt").exists(), what="the child to start")
    home = world.data / "run" / "cloudflared-home"
    assert (tmp_path / "home.txt").read_text() == str(home)
    assert home.is_dir() and stat.S_IMODE(home.stat().st_mode) == 0o700
    assert str(home) != os.environ.get("HOME")
    # the glob that finds pid files must not trip over it
    assert pidfile.live_processes(world.data) is not None


def spawn_fake_tunnel(tmp_path, name="cloudflared"):
    (tmp_path / "stale").mkdir(exist_ok=True)
    script = fake_cloudflared(tmp_path / "stale" / name, url=URL1)
    return subprocess.Popen(
        [str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def register_stale(world, proc_pid, *, binary="cloudflared", parent=None):
    return pidfile.register(
        world.data,
        "tunnel",
        pid=proc_pid,
        extra={
            "provider": "cloudflare_quick",
            "binary": binary,
            "parent": dead_pid() if parent is None else parent,
        },
    )


def test_a_stale_registered_tunnel_is_killed_before_a_new_one_starts(client, app, world, tmp_path):
    orphan = spawn_fake_tunnel(tmp_path)
    try:
        file = register_stale(world, orphan.pid)
        assert file is not None and orphan.poll() is None
        r = client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
        assert r.status_code == 200, r.text
        orphan.wait(timeout=10)  # SIGTERM'd by the start
        assert not file.exists()
        wait_for(
            lambda: (public_file(world) or {}).get("state") == "running", what="the new tunnel"
        )
    finally:
        if orphan.poll() is None:
            orphan.kill()
        orphan.wait()


def test_resume_also_reaps_a_stale_tunnel(client, app, world, tmp_path):
    orphan = spawn_fake_tunnel(tmp_path)
    try:
        register_stale(world, orphan.pid)
        with app.state.session_factory() as db:
            reachability.save_state(
                db, tunnel={"provider": "cloudflare_quick", "hostname": None, "desired": True}
            )
            db.commit()
        assert reachability.resume_tunnel(app.state.session_factory, world.ic) is True
        orphan.wait(timeout=10)
    finally:
        if orphan.poll() is None:
            orphan.kill()
        orphan.wait()


def test_a_registered_pid_that_is_not_cloudflared_any_more_is_left_alone(world, tmp_path):
    bystander = subprocess.Popen(["sleep", "30"])  # a recycled pid: some other program
    try:
        file = register_stale(world, bystander.pid)
        assert reap_stale_tunnels(world.data) == []
        assert bystander.poll() is None  # not signalled
        assert not file.exists()  # the stale record is dropped
    finally:
        bystander.kill()
        bystander.wait()


def test_a_tunnel_owned_by_another_live_process_is_left_alone(world, tmp_path):
    other_owner = subprocess.Popen(["sleep", "30"])
    orphan = spawn_fake_tunnel(tmp_path)
    try:
        file = register_stale(world, orphan.pid, parent=other_owner.pid)
        assert reap_stale_tunnels(world.data) == []
        assert orphan.poll() is None and file.exists()
        # ... but not the one this process itself is running
        assert reap_stale_tunnels(world.data, keep=frozenset({orphan.pid})) == []
    finally:
        for p in (orphan, other_owner):
            p.kill()
            p.wait()


def test_a_dead_record_is_just_removed(world):
    file = register_stale(world, dead_pid())
    assert reap_stale_tunnels(world.data) == [] and not file.exists()
    junk = world.data / "run" / "tunnel.1.json"
    junk.write_text("not json")
    assert reap_stale_tunnels(world.data) == [] and not junk.exists()


def test_a_supervisor_without_a_data_dir_registers_nothing(tmp_path):
    sup = TunnelSupervisor(poll_interval=0.02, health_check=lambda url: True)
    binary = fake_cloudflared(tmp_path / "cloudflared", url=URL1)
    sup.start(TunnelSpec("cloudflare_quick", [str(binary)]))
    try:
        wait_for(lambda: sup.status()["state"] == "running", what="running")
        assert not (tmp_path / "run").exists()
    finally:
        sup.stop()


def test_pidfile_register_can_name_a_child_pid(tmp_path):
    path = pidfile.register(tmp_path, "tunnel", pid=os.getppid(), extra={"binary": "x"})
    assert path is not None and path.name == f"tunnel.{os.getppid()}.json"
    info = json.loads(path.read_text())
    assert info["pid"] == os.getppid() and info["role"] == "tunnel"
    mine = pidfile.register(tmp_path, "api", port=1)
    assert mine is not None and mine.name == f"api.{os.getpid()}.json"
    pidfile.unregister(path)
    pidfile.unregister(mine)


# ------------------------------------------------------------------ a tunnelled request is not local


def test_a_request_through_the_tunnel_gets_its_own_rate_limit_bucket(make_settings):
    app = create_app(
        make_settings(
            http_rate_limit_enabled=True,
            http_rate_limit_per_minute=2,
            http_rate_limit_loopback_multiplier=5,
        ),
        runtime_registry=RuntimeRegistry(),
    )
    # cloudflared dials 127.0.0.1 and adds X-Forwarded-For with the visitor's address
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    via_tunnel = {"X-Forwarded-For": "203.0.113.9", "Host": "a-b.trycloudflare.com"}
    other_visitor = {"X-Forwarded-For": "203.0.113.10", "Host": "a-b.trycloudflare.com"}
    with TestClient(wrapped, client=("127.0.0.1", 40000)) as tunnel:
        codes = [tunnel.get("/api/health", headers=via_tunnel).status_code for _ in range(3)]
        assert codes == [200, 200, 429]  # 2 per minute: not the 10 a local request gets
        assert tunnel.get("/api/health", headers=other_visitor).status_code == 200
    # the same peer without the header, asking for localhost, is this machine: its own, bigger bucket
    with TestClient(
        wrapped, base_url="http://localhost:8700", client=("127.0.0.1", 40001)
    ) as local:
        assert [local.get("/api/health").status_code for _ in range(10)] == [200] * 10
    # and with no trusted proxy rewriting the address, the header alone already makes it non-local
    with TestClient(app, client=("127.0.0.1", 40002)) as raw:
        codes = [raw.get("/api/health", headers=via_tunnel).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
