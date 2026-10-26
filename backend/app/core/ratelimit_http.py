"""Generic per-client-IP HTTP rate limit (token buckets, in memory) and an SSE connection cap.

Why: login back-off, per-agent action limits and the mail/wake limits protect specific things;
nothing stopped one client from hammering any other route (or opening unlimited event streams).
Behind a public tunnel that is the first thing a stranger would try.

* Scope: ``/api/*`` only. The built web UI (any path outside ``/api``) is never counted.
* Buckets: ``api`` (default 300/min, burst = one minute's worth) and a stricter ``auth`` bucket
  (default 30/min) for the routes a stranger can reach without an account: state-changing
  ``/api/auth/*`` requests, the login link, ``/api/invitations/*`` and ``/api/webhooks/*``. The
  login back-off in ``services/auth.py`` still applies on top and is not touched here.
* Who is "the client": the address ASGI reports (``scope["client"]``). uvicorn already rewrites
  it from ``X-Forwarded-For`` *only* for peers listed in ``--forwarded-allow-ips`` (default:
  127.0.0.1), so the existing trusted-proxy rules decide whether a forwarded address is
  believed; this module adds no second opinion. IPv6 clients share a bucket per /64. A request
  counts as *loopback* only under the strict rule used elsewhere
  (``netinfo.is_loopback_request``: loopback peer, no proxy headers, loopback Host), so a
  tunnelled request never gets the local allowance. Loopback requests get
  ``http_rate_limit_loopback_multiplier`` times the limit, or no limit when
  ``http_rate_limit_loopback_exempt`` is set.
* SSE: at most ``http_stream_max_per_ip`` concurrent ``GET /api/companies/{id}/stream``
  connections per client (a closed connection frees its slot immediately).
* Refusals are ``429`` with the standard error envelope, a ``Retry-After`` header and
  ``details.retry_after_seconds``.

State is per process and lost on restart (fine for a self-hosted single process; a
multi-process deployment would need a shared store -- noted in docs/security.md).
"""

from __future__ import annotations

import ipaddress
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import envelope
from app.core.netinfo import is_loopback_request

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_STREAM_PATH = re.compile(r"^/api/companies/[^/]+/stream/?$")
_SWEEP_AT = 20_000
_IDLE_SECONDS = 120.0
STREAM_RETRY_SECONDS = 5


def client_key(host: str | None) -> str:
    """Bucket key for a client address (IPv6 grouped by /64)."""
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return host
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(ip)


def bucket_class(method: str, path: str) -> str:
    """``auth`` for routes reachable without an account, otherwise ``api``."""
    if path.startswith(("/api/webhooks/", "/api/invitations/")) or path == "/api/auth/local":
        return "auth"
    if path.startswith("/api/auth/") and method.upper() not in _SAFE_METHODS:
        return "auth"
    return "api"


@dataclass
class _Bucket:
    tokens: float
    updated: float


class HttpRateLimiter:
    """Token buckets keyed by (client, class, loopback). Thread-safe."""

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.clock = clock
        self._buckets: dict[tuple[str, str, bool], _Bucket] = {}
        self._streams: dict[str, int] = {}
        self._lock = threading.Lock()

    # ---- limits
    def limit_for(self, klass: str, loopback: bool) -> int | None:
        """Requests per minute, or None for unlimited."""
        s = self.settings
        base = (
            s.http_rate_limit_auth_per_minute if klass == "auth" else s.http_rate_limit_per_minute
        )
        if loopback:
            if s.http_rate_limit_loopback_exempt:
                return None
            return base * s.http_rate_limit_loopback_multiplier
        return base

    # ---- request tokens
    def take(self, key: str, klass: str, loopback: bool) -> tuple[bool, int, int | None]:
        """(allowed, retry_after_seconds, limit). One token per request."""
        limit = self.limit_for(klass, loopback)
        if limit is None:
            return True, 0, None
        rate = limit / 60.0
        now = self.clock()
        with self._lock:
            if len(self._buckets) > _SWEEP_AT:
                self._sweep(now)
            bucket = self._buckets.get((key, klass, loopback))
            if bucket is None:
                bucket = self._buckets[(key, klass, loopback)] = _Bucket(float(limit), now)
            else:
                bucket.tokens = min(float(limit), bucket.tokens + (now - bucket.updated) * rate)
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0, limit
            wait = max(1, math.ceil((1.0 - bucket.tokens) / rate))
            return False, wait, limit

    def _sweep(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > _IDLE_SECONDS]
        for k in stale:
            del self._buckets[k]
        if (
            len(self._buckets) > _SWEEP_AT
        ):  # a flood of distinct active clients: drop the oldest half
            oldest = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)
            for k, _ in oldest[: len(oldest) // 2]:
                del self._buckets[k]

    # ---- SSE slots
    def open_stream(self, key: str) -> bool:
        with self._lock:
            used = self._streams.get(key, 0)
            if used >= self.settings.http_stream_max_per_ip:
                return False
            self._streams[key] = used + 1
            return True

    def close_stream(self, key: str) -> None:
        with self._lock:
            left = self._streams.get(key, 0) - 1
            if left > 0:
                self._streams[key] = left
            else:
                self._streams.pop(key, None)

    def open_streams(self, key: str) -> int:
        return self._streams.get(key, 0)


class HttpRateLimitMiddleware:
    """Pure ASGI (no response buffering: the SSE stream must not be touched)."""

    def __init__(self, app: ASGIApp, limiter: HttpRateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not self.limiter.settings.http_rate_limit_enabled
            or not (path == "/api" or path.startswith("/api/"))
        ):
            await self.app(scope, receive, send)
            return
        method: str = scope.get("method", "GET")
        client = scope.get("client")
        key = client_key(client[0] if client else None)
        loopback = is_loopback_request(Request(scope))
        allowed, wait, limit = self.limiter.take(key, bucket_class(method, path), loopback)
        if not allowed:
            await self._refuse(
                scope,
                receive,
                send,
                "Too many requests from this address; slow down and retry shortly",
                wait,
                limit,
                "requests",
            )
            return
        if method == "GET" and _STREAM_PATH.match(path) and not self._stream_exempt(loopback):
            if not self.limiter.open_stream(key):
                await self._refuse(
                    scope,
                    receive,
                    send,
                    "Too many open event streams from this address; close one and retry",
                    STREAM_RETRY_SECONDS,
                    self.limiter.settings.http_stream_max_per_ip,
                    "streams",
                )
                return
            try:
                await self.app(scope, receive, send)
            finally:
                self.limiter.close_stream(key)
            return
        await self.app(scope, receive, send)

    def _stream_exempt(self, loopback: bool) -> bool:
        return loopback and self.limiter.settings.http_rate_limit_loopback_exempt

    async def _refuse(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        message: str,
        wait: int,
        limit: int | None,
        what: str,
    ) -> None:
        response = JSONResponse(
            status_code=429,
            content=envelope(
                "rate_limited",
                message,
                {"retry_after_seconds": wait, "limit": limit, "scope": what},
            ),
            headers={"Retry-After": str(wait)},
        )
        await response(scope, receive, send)
