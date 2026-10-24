"""Serving the built web UI from the API port, and the HTTP security headers (M9).

Two pure-ASGI middlewares (no ``BaseHTTPMiddleware``: it buffers and breaks the SSE stream):

* :class:`SecurityHeadersMiddleware` -- on every response: ``X-Content-Type-Options``, ``Referrer-Policy``,
  ``X-Frame-Options``/``frame-ancestors``, ``Permissions-Policy``, ``Cross-Origin-Opener-Policy``; HSTS only
  when the request arrived over https. The SPA's HTML gets a strict Content-Security-Policy; ``/api``
  answers get a "nothing may load" policy (except the Swagger page, which loads its UI from a CDN).
* :class:`WebUiMiddleware` -- ``GET``/``HEAD`` for anything outside ``/api`` is answered from
  ``web/dist`` (``COMPANY_OS_WEB_DIST``): hashed ``/assets/*`` are immutable for a year, everything else
  (``index.html``, ``theme-init.js``, the favicon) must be revalidated. A path without a file extension that
  matches no file is a client-side route and gets ``index.html``; a missing file *with* an extension is a
  real 404. ``/api`` is never answered here, so an unknown API path stays a JSON 404 and the SPA fallback
  can never shadow an endpoint. If ``index.html`` does not exist (UI not built) the app answers as before.
"""

from __future__ import annotations

import hashlib
from email.utils import formatdate
from pathlib import Path
from urllib.parse import unquote

from starlette.datastructures import MutableHeaders
from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# `style-src 'unsafe-inline'`: Radix (react-remove-scroll) injects a <style> element at runtime. Scripts are
# strictly same-origin (the theme bootstrap is /theme-init.js, not an inline script), nothing is loaded from
# another origin, and the page cannot be framed.
SPA_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"
# Artifact bytes are user/agent supplied: even if a browser renders them they are inert.
CONTENT_CSP = "default-src 'none'; frame-ancestors 'none'; sandbox"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
HSTS = "max-age=15552000"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _not_modified(scope: Scope, etag: str) -> bool:
    for key, value in scope.get("headers", []):
        if key == b"if-none-match":
            wanted = [v.strip() for v in value.decode("latin-1").split(",")]
            return (
                "*" in wanted or etag in wanted or any(w.removeprefix("W/") == etag for w in wanted)
            )
    return False


def _is_https(scope: Scope) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    for key, value in scope.get("headers", []):
        if key == b"x-forwarded-proto":
            return value.split(b",")[0].strip().lower() == b"https"
    return False


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        https = _is_https(scope)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
                content_type = headers.get("content-type", "")
                if _is_api(path):
                    if path != "/api/docs":
                        csp = CONTENT_CSP if path.endswith("/content") else API_CSP
                        headers.setdefault("Content-Security-Policy", csp)
                    if content_type.startswith("application/json"):
                        headers.setdefault("Cache-Control", "no-store")
                elif content_type.startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", SPA_CSP)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class WebUiMiddleware:
    def __init__(self, app: ASGIApp, dist_dir_getter) -> None:
        self.app = app
        self._dist = dist_dir_getter  # a callable: the setting may point somewhere built later

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if _is_api(path):
            await self.app(scope, receive, send)
            return
        dist: Path = self._dist()
        index = dist / "index.html"
        if not index.is_file():
            await self.app(scope, receive, send)
            return
        target = self._resolve(dist, path)
        if target is None:
            # A missing file with an extension is a real 404 (do not hand HTML to a <script> tag).
            await self.app(scope, receive, send)
            return
        immutable = path.startswith("/assets/") and target != index
        stat = target.stat()
        etag = (
            '"'
            + hashlib.md5(
                f"{stat.st_mtime}-{stat.st_size}".encode(), usedforsecurity=False
            ).hexdigest()
            + '"'
        )
        headers = {
            "Cache-Control": IMMUTABLE if immutable else REVALIDATE,
            "ETag": etag,
            "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        }
        if _not_modified(scope, etag):
            await Response(status_code=304, headers=headers)(scope, receive, send)
            return
        await FileResponse(target, headers=headers)(scope, receive, send)

    @staticmethod
    def _resolve(dist: Path, path: str) -> Path | None:
        rel = unquote(path).lstrip("/")
        index = dist / "index.html"
        if not rel:
            return index
        parts = rel.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") or "\x00" in p for p in parts):
            return None if "." in parts[-1] else index
        root = dist.resolve()
        try:
            candidate = (dist / rel).resolve()
            candidate.relative_to(root)
        except (ValueError, OSError):
            return None
        if candidate.is_file():
            return candidate
        if "." in parts[-1]:  # looks like a file
            return None
        return index
