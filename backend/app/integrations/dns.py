"""DNS and TLS verification (read-only lookups; nothing here changes a record).

``DohResolver`` asks a public DNS-over-HTTPS resolver (Cloudflare's JSON endpoint) so CNAME / TXT /
MX are available without a DNS library; tests inject a fake resolver. ``verify_records`` compares
what the company wants with what the world sees.
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Callable
from typing import Any, Protocol

DOH_URL = "https://cloudflare-dns.com/dns-query"
TYPE_CODES = {"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}


class DnsResolver(Protocol):
    def lookup(self, name: str, type_: str) -> list[str]: ...


def norm_value(type_: str, value: Any) -> str:
    text = str(value or "").strip()
    if type_ in ("CNAME", "NS"):
        return text.rstrip(".").lower()
    if type_ == "TXT":
        return text.strip('"')
    if type_ == "MX":  # "10 mail.example.com." -> "mail.example.com" (priority is compared apart)
        return text.split()[-1].rstrip(".").lower() if text else ""
    return text.lower()


class DohResolver:
    def __init__(self, http, url: str = DOH_URL) -> None:
        self._http = http
        self._url = url

    def lookup(self, name: str, type_: str) -> list[str]:
        try:
            resp = self._http.get(
                self._url,
                params={"name": name, "type": type_},
                headers={"Accept": "application/dns-json"},
            )
            body = resp.json() if resp.status_code == 200 else {}
        except Exception:
            return []
        want = TYPE_CODES.get(type_)
        return [
            norm_value(type_, a.get("data"))
            for a in body.get("Answer", []) or []
            if want is None or a.get("type") == want
        ]


def verify_records(
    desired: list[dict[str, Any]], resolver: DnsResolver
) -> tuple[list[dict[str, Any]], str]:
    """Each desired record with ``status`` in ok | missing | mismatch, and an overall ``ok`` |
    ``pending`` (something missing or different)."""
    out: list[dict[str, Any]] = []
    for rec in desired:
        type_, name = rec["type"].upper(), rec["name"].rstrip(".").lower()
        want = norm_value(type_, rec["value"])
        got = resolver.lookup(name, type_)
        if want in got:
            status = "ok"
        elif not got and type_ == "CNAME" and rec.get("proxied"):
            # a proxied (orange-cloud) CNAME is answered with Cloudflare's own addresses
            status = (
                "ok" if resolver.lookup(name, "A") or resolver.lookup(name, "AAAA") else "missing"
            )
        elif not got:
            status = "missing"
        else:
            status = "mismatch"
        out.append({**rec, "status": status})
    overall = "ok" if out and all(r["status"] == "ok" for r in out) else "pending"
    return out, overall


TlsCheck = Callable[[str], tuple[str, str | None]]


def check_tls(domain: str, timeout: float = 8.0) -> tuple[str, str | None]:
    """('ok'|'pending'|'error', detail): a verified TLS handshake on port 443. A name that does not
    resolve or refuses the connection is ``pending`` (not set up yet); a bad certificate is an
    ``error``."""
    ctx = ssl.create_default_context()
    try:
        with (
            socket.create_connection((domain, 443), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=domain),
        ):
            return "ok", None
    except ssl.SSLCertVerificationError as exc:
        return "error", f"certificate not valid: {exc.verify_message}"
    except ssl.SSLError as exc:
        return "error", f"TLS error: {exc.reason or exc}"
    except OSError as exc:
        return "pending", f"not reachable yet: {exc.strerror or exc}"
