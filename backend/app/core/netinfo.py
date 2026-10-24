"""Where a request came from. Used for the setup-token exemption and the dev-only ``local`` mode.

Loopback is deliberately strict: the TCP peer must be a loopback address AND the request must not
look proxied (forwarding headers) AND the Host header must name a loopback host. A tunnel such as
cloudflared connects from 127.0.0.1 but forwards the public Host and ``X-Forwarded-For``/``CF-*``
headers, so tunnelled requests are never "loopback".
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

_FORWARDING_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "cf-connecting-ip",
    "cf-ray",
    "true-client-ip",
    "x-envoy-external-address",
)
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


def is_loopback_address(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_name(request: Request) -> str:
    raw = request.headers.get("host", "").strip().lower()
    if raw.startswith("["):  # [::1]:8700
        return raw.split("]")[0] + "]"
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def is_loopback_request(request: Request) -> bool:
    if not is_loopback_address(client_ip(request)):
        return False
    if any(h in request.headers for h in _FORWARDING_HEADERS):
        return False
    return host_header_name(request) in _LOOPBACK_NAMES


def is_loopback_bind(host: str) -> bool:
    return is_loopback_address(host.strip().strip("[]"))
