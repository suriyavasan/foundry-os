"""Shared helpers for the Milestone 8 tests: a scripted network (no test touches the real one),
fake binaries for the tunnel supervisor, owner/password setup, connected-account builders."""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
import re
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.time import utcnow
from app.models.user import User
from tests.helpers import api

_COUNTER = itertools.count(1)
SECRET = "tok_live_0123456789abcdef"  # a recognisable provider secret
SECRET2 = "sk_live_fedcba9876543210"


class FakeNetwork:
    """A scripted network. Routes match in order on (method, url regex). A request nobody scripted
    raises, so a test can never reach the real network by accident. Every request is recorded."""

    def __init__(self) -> None:
        self.routes: list[
            tuple[str, re.Pattern[str], Callable[[httpx.Request], httpx.Response]]
        ] = []
        self.requests: list[httpx.Request] = []

    def add(
        self,
        method: str,
        pattern: str,
        json_body: Any = None,
        *,
        status: int = 200,
        text: str | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> FakeNetwork:
        def respond(request: httpx.Request) -> httpx.Response:
            if handler is not None:
                return handler(request)
            if content is not None:
                return httpx.Response(status, content=content, headers=headers)
            if text is not None:
                return httpx.Response(status, text=text, headers=headers)
            return httpx.Response(status, json=json_body, headers=headers)

        self.routes.append((method.upper(), re.compile(pattern), respond))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, pattern, respond in self.routes:
            if method == request.method and pattern.search(str(request.url)):
                return respond(request)
        raise AssertionError(f"unscripted network call: {request.method} {request.url}")

    def calls(self, method: str | None = None, pattern: str = "") -> list[httpx.Request]:
        rx = re.compile(pattern)
        return [
            r
            for r in self.requests
            if (method is None or r.method == method.upper()) and rx.search(str(r.url))
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def everything_sent(self) -> str:
        """All request URLs, headers and bodies as one string (to assert what was NOT sent)."""
        out = []
        for r in self.requests:
            out.append(str(r.url))
            out.append(json.dumps(dict(r.headers)))
            out.append(r.content.decode("utf-8", "replace"))
        return "\n".join(out)


def install_network(app, net: FakeNetwork) -> FakeNetwork:
    app.state.integrations.http.transport = net.transport()
    return net


class FakeResolver:
    def __init__(self, table: dict[tuple[str, str], list[str]] | None = None) -> None:
        self.table = table or {}

    def lookup(self, name: str, type_: str) -> list[str]:
        return list(self.table.get((name.lower().rstrip("."), type_), []))


def fake_run_factory(
    *,
    ifconfig: str = "",
    tailscale: dict | None = None,
    scutil: dict[str, str] | None = None,
):
    """A stand-in for ``subprocess.run`` for ifconfig / scutil / tailscale."""
    scutil = scutil or {"LocalHostName": "test-mac", "ComputerName": "Test Mac"}

    def run(argv, **kwargs):
        name = Path(argv[0]).name.lower()
        if name == "ifconfig":
            return subprocess.CompletedProcess(argv, 0, ifconfig, "")
        if name == "ip":
            return subprocess.CompletedProcess(argv, 1, "", "no ip")
        if name == "scutil":
            value = scutil.get(argv[-1])
            return subprocess.CompletedProcess(argv, 0 if value else 1, (value or "") + "\n", "")
        if name == "tailscale":
            if tailscale is None:
                return subprocess.CompletedProcess(argv, 1, "", "not installed")
            return subprocess.CompletedProcess(argv, 0, json.dumps(tailscale), "")
        return subprocess.CompletedProcess(argv, 1, "", "unknown")

    return run


IFCONFIG = textwrap.dedent(
    """\
    lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    \tinet 127.0.0.1 netmask 0xff000000
    \tinet6 ::1 prefixlen 128
    en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    \tinet6 fe80::1c2b:3d4e:5f60:7182%en0 prefixlen 64 secured scopeid 0xc
    \tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
    \tinet6 2001:db8:abcd:12::20 prefixlen 64 autoconf secured
    utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
    \tinet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
    bridge100: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    \tinet 192.168.64.1 netmask 0xffffff00 broadcast 192.168.64.255
    en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    \tinet 169.254.7.7 netmask 0xffff0000 broadcast 169.254.255.255
    """
)

TAILSCALE_RUNNING = {
    "Version": "1.80.0",
    "BackendState": "Running",
    "Self": {"DNSName": "test-mac.tail1234.ts.net.", "CapMap": {"funnel": None}},
    "CurrentTailnet": {"Name": "tail1234.ts.net"},
}


# ------------------------------------------------------------------ owner & credentials


def claim_owner(app, *, strong: bool = True, totp: bool = False) -> None:
    """Give the placeholder owner a password the way first-run setup would have (no browser)."""
    from sqlalchemy import select

    with app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.is_local_owner.is_(True)))
        assert owner is not None
        owner.password_hash = app.state.auth.hash_password("correct horse battery staple")
        owner.password_changed_at = utcnow() if strong else None
        owner.totp_enabled = totp
        owner.username = owner.username or "founder"
        db.commit()


def store_secret(client, cid: str, name: str, value: str = SECRET) -> str:
    unique = f"{name}-{next(_COUNTER)}"
    r = client.post(
        api(cid, "/credentials"), json={"name": unique, "value": value, "kind": "token"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def connect(
    client,
    cid: str,
    provider: str,
    *,
    label: str | None = None,
    secret: str = SECRET,
    extras: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
    expect: int = 201,
) -> dict:
    """The two-step flow the web does: store the secret, then connect the account."""
    label = label or f"{provider} main"
    body: dict[str, Any] = {"provider": provider, "label": label, "metadata": metadata or {}}
    if provider != "tailscale":
        body["credential_id"] = store_secret(client, cid, f"{provider}-{label}", secret)
    ids = {}
    for key, value in (extras or {}).items():
        ids[key] = store_secret(client, cid, f"{provider}-{label}-{key}", value)
    body["extra_credential_ids"] = ids
    r = client.post(api(cid, "/connected-accounts"), json=body)
    assert r.status_code == expect, r.text
    return r.json()


# ------------------------------------------------------------------ recorded-style responses


def cf(result: Any) -> dict:
    return {"success": True, "errors": [], "messages": [], "result": result}


def cloudflare_routes(net: FakeNetwork, *, zone: str = "example.com", records=None) -> FakeNetwork:
    records = records if records is not None else []
    net.add(
        "GET",
        r"api\.cloudflare\.com/client/v4/user/tokens/verify",
        cf({"id": "t1", "status": "active"}),
    )
    net.add(
        "GET",
        r"api\.cloudflare\.com/client/v4/accounts\?",
        cf([{"id": "acc1", "name": "Acme Cloudflare"}]),
    )
    net.add(
        "GET",
        r"api\.cloudflare\.com/client/v4/zones\?",
        handler=lambda r: httpx.Response(
            200,
            json=cf(
                [{"id": "z1", "name": zone}] if dict(r.url.params).get("name", zone) == zone else []
            ),
        ),
    )
    net.add("GET", r"/accounts/acc1/cfd_tunnel\?", cf([]))
    net.add("GET", r"/zones/z1/dns_records", cf(records))
    return net


def svix_headers(
    secret: str, body: bytes, *, msg_id: str = "msg_1", stamp: int | None = None
) -> dict:
    stamp = int(time.time()) if stamp is None else stamp
    key = base64.b64decode(secret.removeprefix("whsec_"))
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"svix-id": msg_id, "svix-timestamp": str(stamp), "svix-signature": f"v1,{sig}"}


WHSEC = "whsec_" + base64.b64encode(b"super-secret-signing-key-0123456789").decode()


def hmac_header(secret: str, body: bytes) -> dict:
    return {
        "x-company-os-signature": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    }


# ------------------------------------------------------------------ fake binaries


def write_script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def fake_cloudflared(
    path: Path, *, url: str = "https://quiet-river-1234.trycloudflare.com"
) -> Path:
    """Prints the banner cloudflared prints for a quick tunnel, then runs until killed."""
    return write_script(
        path,
        f"""
        import sys, time
        if "--version" in sys.argv:
            print("cloudflared version 2026.10.3 (built test)"); sys.exit(0)
        print("2026-10-24T10:00:00Z INF Thank you for trying Cloudflare Tunnel.", flush=True)
        print("2026-10-24T10:00:01Z INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
        print("2026-10-24T10:00:02Z INF +--------------------------------------------+", flush=True)
        print("2026-10-24T10:00:02Z INF |  Your quick Tunnel has been created! Visit it at:", flush=True)
        print("2026-10-24T10:00:02Z INF |  {url}", flush=True)
        while True:
            time.sleep(0.05)
        """,
    )


def wait_for(predicate, *, timeout: float = 8.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")
