"""Generic per-IP HTTP rate limit and SSE connection cap (docs/security.md G4)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core import ratelimit_http
from app.core.config import Settings
from app.core.ratelimit_http import (
    HttpRateLimiter,
    HttpRateLimitMiddleware,
    bucket_class,
    client_key,
)
from app.main import create_app
from app.runtimes.registry import RuntimeRegistry


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(make_settings: Callable[..., Settings], **overrides):
    overrides.setdefault("http_rate_limit_enabled", True)
    app = create_app(make_settings(**overrides), runtime_registry=RuntimeRegistry())
    clock = Clock()
    app.state.http_rate_limiter.clock = clock
    return app, clock


def hammer(client: TestClient, path: str, n: int, method: str = "GET") -> list[int]:
    return [client.request(method, path).status_code for _ in range(n)]


# ------------------------------------------------------------------ defaults and helpers


def test_defaults_are_on_and_generous_for_a_person_and_strict_for_strangers():
    s = Settings(_env_file=None)
    assert s.http_rate_limit_enabled is True
    assert (s.http_rate_limit_per_minute, s.http_rate_limit_auth_per_minute) == (300, 30)
    assert s.http_stream_max_per_ip == 10
    assert s.http_rate_limit_loopback_exempt is False and s.http_rate_limit_loopback_multiplier > 1


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("COMPANY_OS_HTTP_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("COMPANY_OS_HTTP_RATE_LIMIT_PER_MINUTE", "42")
    s = Settings(_env_file=None)
    assert s.http_rate_limit_enabled is False and s.http_rate_limit_per_minute == 42


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/auth/login", "auth"),
        ("POST", "/api/auth/setup", "auth"),
        ("POST", "/api/auth/passkeys/login/begin", "auth"),
        ("GET", "/api/auth/local", "auth"),
        ("POST", "/api/invitations/abc/accept", "auth"),
        ("POST", "/api/webhooks/email/c1/agentmail", "auth"),
        ("GET", "/api/auth/session", "api"),
        ("GET", "/api/auth/tokens", "api"),
        ("GET", "/api/health", "api"),
        ("POST", "/api/companies", "api"),
    ],
)
def test_bucket_classes(method, path, expected):
    assert bucket_class(method, path) == expected


def test_clients_are_keyed_by_address_ipv6_by_64():
    assert client_key("203.0.113.9") == "203.0.113.9"
    assert client_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert client_key("2001:db8:1:2:aaaa::1") == client_key("2001:db8:1:2:bbbb::2")
    assert client_key("2001:db8:1:2::1") != client_key("2001:db8:1:3::1")
    assert client_key(None) == "unknown" and client_key("testclient") == "testclient"


# ------------------------------------------------------------------ the HTTP limit


def test_over_the_limit_is_a_429_with_the_standard_envelope_and_retry_after(make_settings):
    app, clock = build(make_settings, http_rate_limit_per_minute=5)
    with TestClient(app) as c:
        codes = hammer(c, "/api/health", 7)
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
        r = c.get("/api/health")
        body = r.json()["error"]
        assert body["code"] == "rate_limited" and body["message"]
        assert body["details"]["retry_after_seconds"] == 12  # 5/min = one token per 12 s
        assert r.headers["retry-after"] == "12"
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["x-content-type-options"] == "nosniff"  # security headers still wrap it
        clock.now += 11
        assert c.get("/api/health").status_code == 429
        clock.now += 1
        assert c.get("/api/health").status_code == 200  # a token came back
        assert c.get("/api/health").status_code == 429


def test_a_429_is_readable_by_a_cross_origin_ui(make_settings):
    app, _ = build(make_settings, http_rate_limit_per_minute=1)
    with TestClient(app) as c:
        origin = {"Origin": "http://localhost:5173"}
        c.get("/api/health", headers=origin)
        r = c.get("/api/health", headers=origin)
        assert r.status_code == 429
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_each_client_address_has_its_own_budget(make_settings):
    app, _ = build(make_settings, http_rate_limit_per_minute=3)
    with (
        TestClient(app, client=("203.0.113.1", 4000)) as a,
        TestClient(app, client=("203.0.113.2", 4000)) as b,
    ):
        assert hammer(a, "/api/health", 4) == [200, 200, 200, 429]
        assert hammer(b, "/api/health", 3) == [200, 200, 200]  # untouched by a's flood
    # same address, different port: still one client
    with TestClient(app, client=("203.0.113.1", 5555)) as a2:
        assert a2.get("/api/health").status_code == 429


def test_stranger_routes_have_the_stricter_bucket(make_settings):
    app, _ = build(make_settings, http_rate_limit_per_minute=100, http_rate_limit_auth_per_minute=3)
    with TestClient(app) as c:
        login = {"username": "nobody", "password": "wrong-password-123"}
        codes = [c.post("/api/auth/login", json=login).status_code for _ in range(5)]
        assert codes[3:] == [429, 429] and 429 not in codes[:3]
        body = c.post("/api/auth/login", json=login).json()["error"]
        assert body["code"] == "rate_limited" and body["details"]["scope"] == "requests"
        # webhooks and invitations draw from the same strict budget
        assert c.post("/api/webhooks/email/x/y", json={}).status_code == 429
        assert c.post("/api/invitations/tok/accept", json={}).status_code == 429
        # ordinary routes are unaffected
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/auth/session").status_code == 200


def test_the_static_web_ui_is_never_counted(make_settings, tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>ui</body></html>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    app, _ = build(make_settings, http_rate_limit_per_minute=2, web_dist_dir=dist)
    with TestClient(app) as c:
        assert set(hammer(c, "/", 30)) == {200}
        assert set(hammer(c, "/assets/app-abc123.js", 30)) == {200}
        assert set(hammer(c, "/some/deep/link", 30)) == {200}  # SPA fallback
        assert hammer(c, "/api/health", 3) == [200, 200, 429]


def test_it_can_be_switched_off(make_settings):
    app, _ = build(make_settings, http_rate_limit_enabled=False, http_rate_limit_per_minute=1)
    with TestClient(app) as c:
        assert set(hammer(c, "/api/health", 20)) == {200}


# ------------------------------------------------------------------ proxies and loopback


def test_a_forwarded_address_counts_only_when_the_proxy_is_trusted(make_settings):
    """uvicorn rewrites the client from X-Forwarded-For only for trusted peers; the limiter uses
    whatever it ends up with, so spoofing the header from anywhere else buys no fresh budget."""
    app, _ = build(make_settings, http_rate_limit_per_minute=2)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    with TestClient(wrapped, client=("127.0.0.1", 9)) as proxy:
        a = {"X-Forwarded-For": "198.51.100.7"}
        b = {"X-Forwarded-For": "198.51.100.8"}
        assert [proxy.get("/api/health", headers=a).status_code for _ in range(3)] == [
            200,
            200,
            429,
        ]
        assert proxy.get("/api/health", headers=b).status_code == 200  # a different real client
    with TestClient(wrapped, client=("203.0.113.50", 9)) as stranger:
        codes = [
            stranger.get("/api/health", headers={"X-Forwarded-For": f"10.9.9.{i}"}).status_code
            for i in range(4)
        ]
        assert codes == [200, 200, 429, 429]  # the header is ignored: still 203.0.113.50


def test_a_request_from_this_machine_gets_a_bigger_allowance_not_a_free_pass(make_settings):
    app, _ = build(
        make_settings, http_rate_limit_per_minute=2, http_rate_limit_loopback_multiplier=5
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1)) as local:
        codes = hammer(local, "/api/health", 12)
        assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
        # a tunnelled request (loopback peer, but proxy headers) is NOT local
        tunnelled = {"X-Forwarded-For": "203.0.113.77", "Host": "os.example.com"}
        fresh, _ = build(
            make_settings, http_rate_limit_per_minute=2, http_rate_limit_loopback_multiplier=5
        )
        with TestClient(fresh, client=("127.0.0.1", 1)) as t:
            codes = [t.get("/api/health", headers=tunnelled).status_code for _ in range(3)]
            assert codes == [200, 200, 429]


def test_loopback_can_be_exempted_by_setting(make_settings):
    app, _ = build(
        make_settings, http_rate_limit_per_minute=1, http_rate_limit_loopback_exempt=True
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1)) as local:
        assert set(hammer(local, "/api/health", 25)) == {200}
    with TestClient(app, client=("203.0.113.3", 1)) as remote:
        assert hammer(remote, "/api/health", 2) == [200, 429]  # others are still limited


# ------------------------------------------------------------------ SSE cap


def asgi_scope(path: str, client: str = "203.0.113.5", method: str = "GET") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", b"os.example.com")],
        "client": (client, 40000),
        "server": ("os.example.com", 443),
        "scheme": "https",
        "http_version": "1.1",
    }


def run_streams(limiter: HttpRateLimiter, opens: list[tuple[str, str]], fail: bool = False):
    """Open all streams concurrently against a blocking app; return their status codes (200 for
    the ones that got in, 429 for refusals) and the limiter's open count afterwards."""

    async def main():
        release = asyncio.Event()
        entered = 0

        async def app(scope, receive, send):
            nonlocal entered
            entered += 1
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await release.wait()
            if fail:
                raise RuntimeError("stream crashed")
            await send({"type": "http.response.body", "body": b""})

        middleware = HttpRateLimitMiddleware(app, limiter)
        statuses: list[int] = []

        async def one(path: str, client: str):
            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            with contextlib.suppress(RuntimeError):
                await middleware(asgi_scope(path, client), receive, send)

        tasks = [asyncio.create_task(one(p, c)) for p, c in opens]
        await asyncio.sleep(0.05)
        during = {c: limiter.open_streams(ratelimit_http.client_key(c)) for _, c in opens}
        release.set()
        await asyncio.gather(*tasks)
        return statuses, during

    return asyncio.run(main())


def test_event_streams_are_capped_per_client_and_freed_when_they_end(make_settings):
    settings = make_settings(http_rate_limit_enabled=True, http_stream_max_per_ip=3)
    limiter = HttpRateLimiter(settings)
    path = "/api/companies/c1/stream"
    statuses, during = run_streams(limiter, [(path, "203.0.113.5")] * 5)
    assert sorted(statuses) == [200, 200, 200, 429, 429] and during["203.0.113.5"] == 3
    assert limiter.open_streams("203.0.113.5") == 0  # every slot came back
    # another client is not affected by the first one's streams
    statuses, _ = run_streams(limiter, [(path, "203.0.113.5")] * 3 + [(path, "203.0.113.6")] * 3)
    assert statuses.count(200) == 6
    # and a stream that crashes still frees its slot
    run_streams(limiter, [(path, "203.0.113.5")], fail=True)
    assert limiter.open_streams("203.0.113.5") == 0


def test_the_stream_refusal_is_a_standard_429(make_settings):
    settings = make_settings(http_rate_limit_enabled=True, http_stream_max_per_ip=1)
    limiter = HttpRateLimiter(settings)
    assert limiter.open_stream("203.0.113.5") is True

    sent: list[dict] = []

    async def go():
        async def app(scope, receive, send):  # pragma: no cover - must not be reached
            raise AssertionError("refused streams never reach the app")

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await HttpRateLimitMiddleware(app, limiter)(
            asgi_scope("/api/companies/c1/stream"), receive, send
        )

    asyncio.run(go())
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert start["status"] == 429 and int(headers["retry-after"]) >= 1
    assert b'"rate_limited"' in body and b"retry_after_seconds" in body


def test_other_get_routes_are_not_treated_as_streams(make_settings):
    settings = make_settings(http_rate_limit_enabled=True, http_stream_max_per_ip=1)
    limiter = HttpRateLimiter(settings)
    statuses, during = run_streams(limiter, [("/api/companies/c1/tasks", "203.0.113.5")] * 3)
    assert statuses == [200, 200, 200] and during["203.0.113.5"] == 0


# ------------------------------------------------------------------ memory


def test_idle_buckets_are_swept(make_settings, monkeypatch):
    monkeypatch.setattr(ratelimit_http, "_SWEEP_AT", 10)
    clock = Clock()
    limiter = HttpRateLimiter(make_settings(), clock=clock)
    for i in range(11):
        limiter.take(f"198.51.100.{i}", "api", False)
    clock.now += 300  # everyone has been idle for 5 minutes
    limiter.take("198.51.100.200", "api", False)
    assert len(limiter._buckets) == 1
    # a flood of distinct *active* clients is bounded too
    for i in range(30):
        limiter.take(f"203.0.113.{i}", "api", False)
    assert len(limiter._buckets) <= 12
