"""Turn on a public URL from a terminal: the cloudflared download (confirmed), the tunnel, the wait.

``enable_public_url`` is shared by ``foundryos reach public start`` and the setup wizard. It only
talks to the API (the owner's session or token): the exposure gate, the checksum-verified download
and the tunnel supervisor all live in the server, so nothing here can skip them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.cli.context import CliState

# Short names (what a person types) -> the provider kinds of the API.
PROVIDERS = {
    "quick": "cloudflare_quick",
    "named": "cloudflare_named",
    "tailscale": "tailscale_funnel",
    "cloudflare_quick": "cloudflare_quick",
    "cloudflare_named": "cloudflare_named",
    "tailscale_funnel": "tailscale_funnel",
}
NEEDS_CLOUDFLARED = ("cloudflare_quick", "cloudflare_named")
POLL_SECONDS = 1.0

# Tests replace these to run without waiting.
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic


def provider_kind(name: str) -> str | None:
    return PROVIDERS.get(name.strip().lower())


def enable_public_url(
    st: CliState,
    *,
    provider: str,
    hostname: str | None,
    confirm: Callable[[dict], bool],
    say: Callable[[str], None],
    timeout: float = 60,
) -> str | None:
    """Make this install reachable at a public URL and return it, or None when it could not be
    (the reason has been said). ``provider`` is ``quick`` / ``named`` / ``tailscale`` (or the API
    kind). ``confirm(plan)`` is asked before cloudflared is downloaded: ``plan`` has ``version``,
    ``url``, ``sha256`` and ``size_bytes``; declining stops here. API errors are raised as
    ``CliError`` for the caller's error guard."""
    kind = provider_kind(provider)
    if kind is None:
        say(f"Unknown provider {provider!r}: use quick, named or tailscale.")
        return None
    client = st.client()
    reach: dict[str, Any] = client.get("/reachability")
    if reach.get("public_exposure_blocked_reason"):
        say(f"A public URL is not allowed yet: {reach['public_exposure_blocked_reason']}")
        return None
    tunnel = reach.get("tunnel") or {}
    if tunnel.get("state") in ("running", "starting"):
        if tunnel.get("provider") != kind:
            say(
                f"A {tunnel.get('provider')} tunnel is already {tunnel['state']}. "
                "Stop it first: foundryos reach public stop"
            )
            return None
        say("The tunnel is already on.")
    else:
        binary = tunnel.get("binary") or {}
        if kind in NEEDS_CLOUDFLARED and not binary.get("found"):
            plan = client.get("/reachability/tunnel/install-plan")
            if not confirm(plan):
                say("Not downloading cloudflared. Install it yourself, then run this again.")
                return None
            say("Downloading cloudflared and checking its SHA-256...")
            client.post(
                "/reachability/tunnel/install",
                {"confirm": True, "sha256": plan.get("sha256")},
                timeout=300,  # the release is tens of MB
            )
            say("cloudflared installed.")
        body: dict[str, Any] = {"provider": kind}
        if hostname:
            body["hostname"] = hostname
        client.post("/reachability/tunnel/start", body)
        say("Starting the tunnel...")
    return wait_for_url(st, say=say, timeout=timeout)


def wait_for_url(st: CliState, *, say: Callable[[str], None], timeout: float = 60) -> str | None:
    """Poll until the tunnel is running with a URL (returned), fails, or ``timeout`` passes."""
    client = st.client()
    deadline = clock() + timeout
    while True:
        tunnel = client.get("/reachability").get("tunnel") or {}
        if tunnel.get("state") == "running" and tunnel.get("url"):
            return str(tunnel["url"])
        if tunnel.get("state") == "error":
            say(f"The tunnel failed: {tunnel.get('error') or 'unknown error'}")
            return None
        if clock() >= deadline:
            detail = f" ({tunnel['error']})" if tunnel.get("error") else ""
            say(
                f"No public URL after {timeout:g} s{detail}. It keeps trying in the background; "
                "check with: foundryos reach --watch"
            )
            return None
        sleep(min(POLL_SECONDS, max(0.0, deadline - clock())))
