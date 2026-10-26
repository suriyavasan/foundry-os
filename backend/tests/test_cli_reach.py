"""`foundryos reach public start|stop`, `reach --watch` and the Public URL line of `status`, against
an in-process app with a fake cloudflared (downloaded from a faked GitHub release) -- no real tunnel
is opened and nothing is downloaded from the real network."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli import context, tunnel_flow
from app.cli.commands import company as company_cmd
from app.cli.main import app as cli_app
from tests.m8_helpers import claim_owner, fake_cloudflared, wait_for
from tests.test_m8_cloudflared_install import API, ASSET, BASE, release, tgz
from tests.test_public_url_announce import URL1, URL2, changing_cloudflared, public_file


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self):
        return json.loads(self.out)


@pytest.fixture
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cfg"
    monkeypatch.setenv("COMPANYOS_CONFIG_DIR", str(d))
    for name in ("COMPANYOS_TOKEN", "COMPANYOS_API_URL", "COMPANYOS_COMPANY"):
        monkeypatch.delenv(name, raising=False)
    return d


@pytest.fixture
def cli(
    app, client: TestClient, cfg_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Result]:
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.setenv("COMPANYOS_TOKEN", app.state.owner_token.get())
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    monkeypatch.setattr(tunnel_flow, "POLL_SECONDS", 0.05)
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None, expect: int | None = 0) -> Result:
        r = runner.invoke(cli_app, list(args), input=input)
        res = Result(r.exit_code, r.stdout, r.stderr)
        if expect is not None:
            assert res.code == expect, (
                f"exit {res.code} (wanted {expect})\nout: {res.out}\nerr: {res.err}"
            )
        return res

    return invoke


@pytest.fixture
def gated(app, m8):
    """An owner with a strong password (the exposure gate is open); a quick supervisor."""
    claim_owner(app)
    m8.data = m8.settings.resolved_data_dir
    sup = m8.ic.supervisor
    sup.base_backoff, sup.max_backoff, sup.poll_interval = 0.02, 0.05, 0.02
    sup._health_check = lambda url: True
    yield m8
    sup.stop()


@pytest.fixture
def release_server(m8, tmp_path, monkeypatch):
    """GitHub says there is a cloudflared release; its download is a fake cloudflared."""
    monkeypatch.setattr("app.integrations.cloudflared.platform_asset", lambda *a: ASSET)
    payload = fake_cloudflared(tmp_path / "payload", url=URL1).read_bytes()
    blob = tgz(payload)
    m8.net.add("GET", API, release(ASSET, blob))
    m8.net.add("GET", BASE + ASSET, content=blob)
    return blob


# ------------------------------------------------------------------ reach public start / stop


def test_start_yes_installs_cloudflared_and_opens_a_quick_tunnel(cli, gated, release_server):
    import hashlib  # noqa: PLC0415

    sha = hashlib.sha256(release_server).hexdigest()
    r = cli("reach", "public", "start", "quick", "--yes")
    # the download plan is shown before it is accepted
    assert sha in r.out and "2026.10.3" in r.out and BASE + ASSET in r.out
    assert f"Public URL: {URL1}" in r.out and "changes every time" in r.out
    assert len(gated.net.calls("GET", "releases/download")) == 1
    assert (gated.settings.resolved_bin_dir / "cloudflared").is_file()
    tunnel = gated.ic.supervisor.status()
    assert tunnel["state"] == "running" and tunnel["url"] == URL1
    wait_for(lambda: (public_file(gated) or {}).get("url") == URL1, what="public-url.json")
    # asking again changes nothing
    again = cli("reach", "public", "start")
    assert "already on" in again.out and URL1 in again.out
    assert len(gated.net.calls("GET", "releases/download")) == 1


def test_start_without_yes_asks_first_and_no_downloads_nothing(cli, gated, release_server):
    r = cli("reach", "public", "start", input="n\n", expect=1)
    assert "Download and install it?" in r.out and "Not downloading" in r.out
    assert gated.net.calls("GET", "releases/download") == []
    assert not (gated.settings.resolved_bin_dir / "cloudflared").exists()
    assert gated.ic.supervisor.status()["state"] == "stopped"
    r = cli("reach", "public", "start", input="y\n")
    assert f"Public URL: {URL1}" in r.out
    assert len(gated.net.calls("GET", "releases/download")) == 1


def test_start_does_not_download_when_cloudflared_is_already_there(cli, gated, tmp_path):
    gated.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    changing_cloudflared(gated.settings.resolved_bin_dir / "cloudflared", tmp_path, (URL2, URL2))
    r = cli("reach", "public", "start", "--json")
    assert r.json() == {"url": URL2, "running": True}
    assert gated.net.requests == []


def test_start_is_refused_while_the_exposure_gate_is_closed(cli, app, m8, release_server):
    # a fresh install: the owner has no password yet
    r = cli("reach", "public", "start", "--yes", expect=1)
    assert "not allowed yet" in r.out and "password" in r.out
    assert m8.net.requests == []
    assert m8.ic.supervisor.status()["state"] == "stopped"


def test_named_needs_a_hostname_and_unknown_kinds_are_usage_errors(cli, gated):
    r = cli("reach", "public", "start", "named", expect=2)
    assert "--hostname" in r.err
    r = cli("reach", "public", "start", "carrier-pigeon", expect=2)
    assert "quick, named or tailscale" in r.err


def test_stop_turns_the_tunnel_off_and_reach_says_so(cli, gated, tmp_path):
    gated.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    changing_cloudflared(gated.settings.resolved_bin_dir / "cloudflared", tmp_path, (URL2, URL2))
    cli("reach", "public", "start")
    out = cli("reach").out
    assert URL2 in out and "changes on restart" in out
    assert "The public URL is off." in cli("reach", "public", "stop").out
    assert gated.ic.supervisor.status()["state"] == "stopped"
    out = cli("reach").out
    assert "no tunnel" in out and f"last URL was {URL2}" in out
    assert cli("reach", "--json").json()["tunnel"]["last_url"] == URL2


def test_reach_with_no_arguments_and_the_group_help_still_work(cli):
    assert "Mode:" in cli("reach").out
    out = cli("reach", "--help").out
    assert "--watch" in out and "public" in out
    assert "start" in cli("reach", "public", "--help").out


# ------------------------------------------------------------------ enable_public_url


class StubClient:
    def __init__(self, reach):
        self.reach = reach
        self.posts = []

    def get(self, path, **kw):
        return self.reach() if callable(self.reach) else self.reach

    def post(self, path, body=None, **kw):
        self.posts.append((path, body))
        return {}


class StubState:
    def __init__(self, client):
        self._client = client

    def client(self):
        return self._client


def tunnel_reach(state="stopped", provider=None, url=None, found=True, blocked=None, error=None):
    return {
        "public_exposure_blocked_reason": blocked,
        "tunnel": {
            "state": state,
            "provider": provider,
            "url": url,
            "error": error,
            "binary": {"found": found},
        },
    }


def test_enable_public_url_flow_with_stubs(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(tunnel_flow, "sleep", lambda s: None)
    # polling: starting, starting, running
    seq = iter([tunnel_reach(), tunnel_reach("starting")] + [tunnel_reach("running", url=URL1)] * 5)
    c = StubClient(lambda: next(seq))
    url = tunnel_flow.enable_public_url(
        StubState(c), provider="quick", hostname=None, confirm=lambda p: True, say=said.append
    )
    assert url == URL1 and c.posts == [
        ("/reachability/tunnel/start", {"provider": "cloudflare_quick"})
    ]

    # the other provider already runs
    c = StubClient(tunnel_reach("running", "tailscale_funnel", "https://x.ts.net"))
    assert (
        tunnel_flow.enable_public_url(
            StubState(c), provider="quick", hostname=None, confirm=lambda p: True, say=said.append
        )
        is None
    )
    assert c.posts == [] and "Stop it first" in said[-1]

    # the tunnel gives up
    c = StubClient(lambda: tunnel_reach("error", error="boom") if c.posts else tunnel_reach())
    assert (
        tunnel_flow.enable_public_url(
            StubState(c), provider="quick", hostname=None, confirm=lambda p: True, say=said.append
        )
        is None
    )
    assert "boom" in said[-1]


def test_enable_public_url_times_out_politely(monkeypatch):
    said: list[str] = []
    now = [0.0]
    monkeypatch.setattr(tunnel_flow, "clock", lambda: now[0])
    monkeypatch.setattr(tunnel_flow, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    c = StubClient(tunnel_reach("starting", "cloudflare_quick"))
    url = tunnel_flow.enable_public_url(
        StubState(c), provider="cloudflare_quick", hostname=None, confirm=lambda p: True,
        say=said.append, timeout=5,
    )  # fmt: skip
    assert url is None and "No public URL after 5 s" in said[-1]


def test_enable_public_url_names_the_download_in_the_plan(monkeypatch):
    plans: list[dict] = []

    class C(StubClient):
        def get(self, path, **kw):
            if path.endswith("install-plan"):
                return {"version": "v1", "url": "u", "sha256": "ab" * 32, "size_bytes": 5}
            return tunnel_reach(found=False)

    c = C(None)
    declined = tunnel_flow.enable_public_url(
        StubState(c), provider="quick", hostname=None,
        confirm=lambda plan: plans.append(plan) or False, say=lambda t: None,
    )  # fmt: skip
    assert declined is None and plans[0]["sha256"] == "ab" * 32 and c.posts == []


# ------------------------------------------------------------------ reach --watch, status


def test_watch_prints_each_new_url_and_ends_on_ctrl_c(cli, gated, tmp_path, monkeypatch):
    gated.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    changing_cloudflared(
        gated.settings.resolved_bin_dir / "cloudflared", tmp_path, first_run_seconds=1.0
    )
    seen = {"url2": False}

    def sleep(_seconds):
        time.sleep(0.05)
        if seen["url2"]:
            raise KeyboardInterrupt  # the poll after the new URL was printed: the user hits Ctrl-C
        seen["url2"] = (public_file(gated) or {}).get("url") == URL2

    monkeypatch.setattr(company_cmd.watch_hooks, "sleep", sleep)
    monkeypatch.setattr(company_cmd.watch_hooks, "max_iterations", 400)  # never hangs a test run
    cli("reach", "public", "start", "--timeout", "20")
    r = cli("reach", "--watch")
    lines = [line for line in r.out.splitlines() if "Public URL" in line]
    assert any(URL1 in line for line in lines)  # the one it started with
    assert lines[-1].startswith("Public URL changed:") and URL2 in lines[-1]
    assert f"was {URL1}" in lines[-1]
    assert sum(URL2 in line for line in lines) == 1  # printed once, not on every poll


def test_watch_is_bounded_and_can_print_json(cli, gated, monkeypatch):
    monkeypatch.setattr(company_cmd.watch_hooks, "sleep", lambda s: None)
    monkeypatch.setattr(company_cmd.watch_hooks, "max_iterations", 3)
    r = cli("reach", "--watch", "--json")
    first = json.loads(r.out)  # one object: nothing changed over the three polls
    assert first == {"state": "stopped", "url": None, "previous_url": None}


def test_watch_waits_out_a_server_that_is_down(cli, gated, monkeypatch):
    from app.cli.client import EXIT_UNREACHABLE, CliError  # noqa: PLC0415

    calls = {"n": 0}

    class Down:
        json = False

        def client(self):
            return self

        def get(self, path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise CliError("down", exit_code=EXIT_UNREACHABLE, code="unreachable")
            return {"tunnel": {"state": "running", "url": URL2}}

    monkeypatch.setattr(company_cmd.watch_hooks, "sleep", lambda s: None)
    monkeypatch.setattr(company_cmd.watch_hooks, "max_iterations", 4)
    printed: list[str] = []
    monkeypatch.setattr(company_cmd.render, "say", lambda text, **kw: printed.append(text))
    company_cmd._watch_public_url(Down())
    assert any("not answering" in line for line in printed)
    assert printed[-1] == f"Public URL: {URL2}"


def test_status_shows_the_public_url_near_the_top(cli, gated, company, tmp_path):
    out = cli("status").out
    lines = out.splitlines()
    assert any(line.startswith("Public URL: none") for line in lines)
    gated.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    changing_cloudflared(gated.settings.resolved_bin_dir / "cloudflared", tmp_path, (URL2, URL2))
    cli("reach", "public", "start")
    out = cli("status").out
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("Public URL:"))
    assert URL2 in lines[at] and "(changes on restart)" in lines[at]
    assert at <= 2 and at < next(i for i, line in enumerate(lines) if "AI Co-founder" in line)
    data = cli("status", "--json").json()["reachability"]
    assert data["public_url"] == URL2 and data["public_url_ephemeral"] is True
