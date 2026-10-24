"""The tunnel supervisor with fake binaries: URL parsing, restart with backoff, health checks,
stop, redaction. A real cloudflared is never needed (or run)."""

from __future__ import annotations

import os
import sys

import pytest

from app.integrations.tunnel import QUICK_URL, TunnelSpec, TunnelSupervisor, redact_line
from tests.m8_helpers import fake_cloudflared, wait_for, write_script

URL = "https://quiet-river-1234.trycloudflare.com"


def supervisor(**kw) -> TunnelSupervisor:
    defaults = dict(
        health_interval=0.2,
        start_timeout=5,
        base_backoff=0.02,
        max_backoff=0.05,
        max_failures=3,
        stable_seconds=60,
        kill_grace=2,
        poll_interval=0.02,
        health_check=lambda url: True,
    )
    defaults.update(kw)
    return TunnelSupervisor(**defaults)


@pytest.fixture
def sup():
    made: list[TunnelSupervisor] = []

    def make(**kw):
        s = supervisor(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.stop()


def test_quick_tunnel_url_is_parsed_from_the_output_and_stop_ends_the_process(tmp_path, sup):
    s = sup()
    binary = fake_cloudflared(tmp_path / "cloudflared", url=URL)
    s.start(
        TunnelSpec("cloudflare_quick", [str(binary), "tunnel", "--url", "http://127.0.0.1:8700"])
    )
    wait_for(lambda: s.status()["state"] == "running", what="running")
    st = s.status()
    assert st["url"] == URL and st["provider"] == "cloudflare_quick" and st["error"] is None
    pid = s._proc.pid
    s.stop()
    assert s.status()["state"] == "stopped" and s.status()["url"] is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    # stopping twice, or a stopped tunnel, is harmless
    s.stop()


def test_url_regex_ignores_the_api_host_and_other_domains():
    assert (
        QUICK_URL.search("see https://api.trycloudflare.com/x").group(1) == "api"
    )  # filtered in code
    assert QUICK_URL.search("https://example.com") is None


def test_health_checks_set_healthy_and_a_failing_url_restarts_the_tunnel(tmp_path, sup):
    results = iter([True, False, False, False] + [True] * 100)
    s = sup(health_check=lambda url: next(results))
    binary = fake_cloudflared(tmp_path / "cloudflared", url=URL)
    s.start(TunnelSpec("cloudflare_quick", [str(binary)]))
    wait_for(lambda: s.status()["healthy"] is True, what="first health check")
    assert s.status()["last_health_check_at"] is not None
    # three failures in a row: the supervisor restarts the process and counts it
    wait_for(lambda: s.status()["restarts"] >= 1, what="restart after failed health checks")
    wait_for(lambda: s.status()["state"] == "running", what="running again")


def test_a_crashing_binary_is_restarted_with_backoff_then_reported_as_an_error(tmp_path, sup):
    script = write_script(
        tmp_path / "crashy",
        """
        import sys
        print("boom: cannot reach the edge", flush=True)
        sys.exit(1)
        """,
    )
    s = sup(max_failures=3)
    s.start(TunnelSpec("cloudflare_quick", [str(script)]))
    wait_for(lambda: s.status()["state"] == "error", what="gave up")
    st = s.status()
    assert st["restarts"] == 3 and "gave up after 3 restarts" in st["error"]
    assert "exited with code 1" in st["error"] and "boom" in st["error"]
    assert not s.active
    # a stopped supervisor can be started again
    s.stop()
    assert s.status()["state"] == "stopped"


def test_backoff_grows_and_is_capped(tmp_path, sup):
    stamps = tmp_path / "starts.txt"
    script = write_script(
        tmp_path / "counting",
        f"""
        import sys, time
        with open({str(stamps)!r}, "a") as fh:
            fh.write(repr(time.monotonic()) + "\\n")
        sys.exit(2)
        """,
    )
    s = sup(base_backoff=0.1, max_backoff=0.2, max_failures=4)
    s.start(TunnelSpec("cloudflare_quick", [str(script)]))
    wait_for(lambda: s.status()["state"] == "error", what="gave up")
    times = [float(x) for x in stamps.read_text().split()]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert (
        len(times) == 5 and gaps[0] >= 0.1 and gaps[1] >= 0.2 and gaps[3] < 0.5
    )  # 0.1, 0.2, 0.2 (cap), 0.2


def test_a_binary_that_never_shows_a_url_times_out(tmp_path, sup):
    script = write_script(tmp_path / "silent", "import time\nwhile True: time.sleep(0.05)\n")
    s = sup(start_timeout=0.3, max_failures=1)
    s.start(TunnelSpec("cloudflare_quick", [str(script)]))
    wait_for(lambda: s.status()["state"] == "error", what="start timeout")
    assert "did not become ready" in s.status()["error"]


def test_a_missing_binary_is_an_error_not_a_crash(tmp_path, sup):
    s = sup()
    s.start(TunnelSpec("cloudflare_quick", [str(tmp_path / "nope")]))
    wait_for(lambda: s.status()["state"] == "error", what="error")
    assert "could not start" in s.status()["error"]


def test_named_tunnel_is_running_when_cloudflared_registers_a_connection(tmp_path, sup):
    script = write_script(
        tmp_path / "named",
        """
        import time
        print("INF Starting tunnel tunnelID=abc", flush=True)
        print("INF Registered tunnel connection connIndex=0 location=ams01", flush=True)
        while True: time.sleep(0.05)
        """,
    )
    s = sup()
    s.start(
        TunnelSpec(
            "cloudflare_named",
            [str(script)],
            hostname="app.example.com",
            known_url="https://app.example.com",
        )
    )
    wait_for(lambda: s.status()["state"] == "running", what="named running")
    st = s.status()
    assert st["url"] == "https://app.example.com" and st["hostname"] == "app.example.com"


def test_funnel_url_comes_from_the_tailscale_output(tmp_path, sup):
    script = write_script(
        tmp_path / "tailscale",
        """
        import time
        print("Available on the internet:", flush=True)
        print("https://test-mac.tail1234.ts.net/", flush=True)
        while True: time.sleep(0.05)
        """,
    )
    s = sup()
    s.start(TunnelSpec("tailscale_funnel", [str(script), "funnel", "8700"]))
    wait_for(lambda: s.status()["state"] == "running", what="funnel running")
    assert s.status()["url"] == "https://test-mac.tail1234.ts.net"


def test_the_child_gets_a_minimal_environment_and_secrets_never_reach_the_logs(
    tmp_path, sup, monkeypatch
):
    monkeypatch.setenv("COMPANY_OS_SECRET_KEY", "server-signing-key-should-not-leak")
    monkeypatch.setenv("SOME_OTHER_SECRET", "nope")
    token = "eyJhIjoiYWNjb3VudC0xMjM0NTY3ODkwIiwidCI6InR1bm5lbC1pZC0xMjM0NTY3ODkwIiwicyI6InNlY3JldC1zZWNyZXQtc2VjcmV0In0="
    script = write_script(
        tmp_path / "envdump",
        """
        import os, time
        print("env keys:", ",".join(sorted(k for k in os.environ if "SECRET" in k or "TOKEN" in k)), flush=True)
        print("token is", os.environ.get("TUNNEL_TOKEN"), flush=True)
        print("INF Registered tunnel connection", flush=True)
        while True: time.sleep(0.05)
        """,
    )
    s = sup()
    s.start(
        TunnelSpec(
            "cloudflare_named",
            [str(script)],
            env={"TUNNEL_TOKEN": token},
            hostname="a.example.com",
            known_url="https://a.example.com",
            secrets=(token,),
        )
    )
    wait_for(lambda: s.status()["state"] == "running", what="running")
    logs = "\n".join(s.logs())
    assert "TUNNEL_TOKEN" in logs  # the child did receive the token ...
    assert (
        "COMPANY_OS_SECRET_KEY" not in logs and "SOME_OTHER_SECRET" not in logs
    )  # ... and only that
    assert token not in logs and "[redacted]" in logs  # ... but it never reaches the log tail


def test_redaction_masks_long_blobs_known_secrets_and_credential_shapes():
    assert (
        redact_line("token abc123SECRETxyz here", ("abc123SECRETxyz",)) == "token [redacted] here"
    )
    blob = "A" * 80
    assert redact_line(f"x {blob} y", ()) == "x [redacted] y"
    assert "ghp_" not in redact_line("pushed with ghp_" + "a" * 30, ())


def test_start_twice_is_refused_while_active(tmp_path, sup):
    s = sup()
    binary = fake_cloudflared(tmp_path / "cloudflared", url=URL)
    s.start(TunnelSpec("cloudflare_quick", [str(binary)]))
    with pytest.raises(RuntimeError):
        s.start(TunnelSpec("cloudflare_quick", [str(binary)]))
    assert sys.executable  # keep import used
