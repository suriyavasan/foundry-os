"""Test kit for the M16 radar: no real network, ever.

* ``no_radar_network`` (autouse for the WHOLE suite, imported by ``tests/conftest.py``): the radar
  fetcher's default transport refuses every request, so a test that triggers a radar fetch by
  accident (a scheduler tick in an unrelated test) fails offline instead of reaching a real site.
* ``block_sockets`` (autouse here): any ``socket.connect`` to a non-loopback address, and any DNS
  lookup of a real name, fails the test.
* ``web``: a fake internet. A ``FakeWeb`` answers by ``Host`` + path through ``httpx.MockTransport``
  under the real ``GuardedTransport`` (so the SSRF guard runs for real), with a fake DNS resolver
  that maps names to documentation-style *public* addresses, plus names that resolve to private,
  metadata and rebinding answers. One integration test uses a real ``http.server`` on 127.0.0.1.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core import netguard
from app.db.session import build_engine, build_session_factory
from app.radar import OFFLINE_ENV, fetcher

PUBLIC_IP = "93.184.216.34"  # a public address no test ever connects to (MockTransport answers)


@pytest.fixture(autouse=True)
def no_radar_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whole suite: the radar is offline. ``COMPANY_OS_RADAR_OFFLINE`` is inherited by every process
    a test spawns (a real worker, ``companyos up``): companies are seeded with 100 sources and the
    radar is on for a new company, so such a process would otherwise fetch real sites. In-process,
    the default transport refuses as well."""
    monkeypatch.setenv(OFFLINE_ENV, "1")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline in tests (radar)", request=request)

    monkeypatch.setattr(fetcher, "transport_factory", lambda: httpx.MockTransport(refuse))


def _is_local(address: Any) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    return isinstance(host, str) and (
        host in ("127.0.0.1", "::1", "localhost") or host.startswith("/")
    )


@pytest.fixture(autouse=True)
def radar_tests_run_online_against_the_fake_web(monkeypatch: pytest.MonkeyPatch) -> None:
    """The radar's own tests exercise the fetcher through ``web`` (a fake transport): lift the
    suite-wide offline switch for them; the sockets stay blocked (``block_sockets``)."""
    monkeypatch.delenv(OFFLINE_ENV, raising=False)


@pytest.fixture(autouse=True)
def block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Radar tests may only open sockets to loopback; a DNS lookup of a real name fails."""
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if not _is_local(address):
            raise AssertionError(f"radar tests must not open a connection to {address!r}")
        return original_connect(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost", ""):
            try:
                socket.inet_pton(socket.AF_INET, host)
            except OSError:
                raise AssertionError(f"radar tests must not resolve {host!r}") from None
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@dataclass
class Seen:
    host: str
    path: str  # path?query
    headers: dict[str, str]

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.path}"


@dataclass
class _Route:
    responses: list[Callable[[httpx.Request], httpx.Response]]
    hits: int = 0


@dataclass
class FakeWeb:
    """Routes are keyed by (host, path-with-query) and fall back to (host, path)."""

    routes: dict[tuple[str, str], _Route] = field(default_factory=dict)
    seen: list[Seen] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    dns: dict[str, list[str]] = field(default_factory=dict)
    resolutions: list[str] = field(default_factory=list)

    # -- building the fake internet

    def add(
        self,
        url: str,
        body: bytes | str = b"",
        *,
        status: int = 200,
        content_type: str | None = "application/rss+xml",
        headers: dict[str, str] | None = None,
        then: list[dict[str, Any]] | None = None,
    ) -> None:
        """Answer ``url``. ``then`` lists further answers for the next requests (the last repeats)."""
        specs = [dict(body=body, status=status, content_type=content_type, headers=headers)]
        specs += then or []

        def make(spec: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
            hdrs = dict(spec.get("headers") or {})
            if spec.get("content_type"):
                hdrs.setdefault("content-type", spec["content_type"])
            content = spec["body"]
            content = content.encode() if isinstance(content, str) else content
            return lambda _req: httpx.Response(spec["status"], content=content, headers=hdrs)

        parts = urlsplit(url)
        key = (parts.netloc.lower(), parts.path + (f"?{parts.query}" if parts.query else ""))
        self.routes[key] = _Route([make(spec) for spec in specs])

    def add_handler(self, url: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        parts = urlsplit(url)
        key = (parts.netloc.lower(), parts.path + (f"?{parts.query}" if parts.query else ""))
        self.routes[key] = _Route([handler])

    def robots(self, host: str, text: str | None, *, status: int = 200) -> None:
        """``text=None`` and ``status=404`` means no robots.txt."""
        self.add(
            f"https://{host}/robots.txt",
            text or "",
            status=status,
            content_type="text/plain",
        )

    def site(self, host: str, robots: str | None = "User-agent: *\nAllow: /\n") -> None:
        """Give ``host`` a permissive robots.txt (or none: ``robots=None`` answers 404)."""
        if robots is None:
            self.robots(host, None, status=404)
        else:
            self.robots(host, robots)

    # -- DNS

    def resolve_to(self, host: str, *answers: str) -> None:
        """``host`` resolves to these addresses in order, one per lookup (the last repeats)."""
        self.dns[host] = list(answers)

    def resolver(self, host: str, port: int) -> list[str]:
        self.resolutions.append(host)
        answers = self.dns.get(host)
        if answers is None:
            return [PUBLIC_IP]
        current = answers[0]
        if len(answers) > 1:
            answers.pop(0)
        return [current]

    # -- the transport

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host).lower()
        path = request.url.raw_path.decode()
        self.seen.append(Seen(host, path, dict(request.headers)))
        route = self.routes.get((host, path)) or self.routes.get((host, path.split("?")[0]))
        if route is None:
            return httpx.Response(404, content=b"not found", headers={"content-type": "text/plain"})
        index = min(route.hits, len(route.responses) - 1)
        route.hits += 1
        return route.responses[index](request)

    # -- assertions

    def count(self, host: str, path_prefix: str = "") -> int:
        return sum(1 for s in self.seen if s.host == host and s.path.startswith(path_prefix))

    def hits(self, host: str) -> list[str]:
        return [s.path for s in self.seen if s.host == host]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    """A fake internet behind the real SSRF guard, a fake resolver and a recording ``sleep``."""
    fake = FakeWeb()
    # Asking for the fake internet means "online against the fake": lift the suite-wide offline
    # switch, also for tests outside tests/radar (the end-to-end test).
    monkeypatch.delenv(OFFLINE_ENV, raising=False)
    monkeypatch.setattr(netguard, "_resolver", fake.resolver)
    monkeypatch.setattr(
        fetcher,
        "transport_factory",
        lambda: netguard.GuardedTransport(
            netguard.PUBLIC_ONLY, inner=httpx.MockTransport(fake.handler)
        ),
    )
    monkeypatch.setattr(fetcher, "sleep", fake.sleeps.append)
    return fake


@pytest.fixture
def sf(db_url: str) -> Iterator[sessionmaker[Session]]:
    """A session factory on a freshly migrated database."""
    engine = build_engine(db_url)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()
