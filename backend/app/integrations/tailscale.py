"""Tailscale through its local CLI (there is no secret to store: the daemon is already signed in).

``tailscale status --json`` tells us whether the device is on a tailnet and its public MagicDNS
name; ``tailscale funnel <port>`` (foreground) is run by the tunnel supervisor. Funnel must also be
allowed by the tailnet policy: that is not visible from here, so when it is not, the CLI's own
message is surfaced by the supervisor. Verified only against the CLI's documented JSON, not against
a tailnet with Funnel enabled.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAC_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def find_tailscale(
    configured: Path | None = None, which: Callable[[str], str | None] = shutil.which
):
    if configured is not None:
        return str(configured) if Path(configured).exists() else None
    found = which("tailscale")
    if found:
        return found
    return MAC_APP if Path(MAC_APP).exists() else None


def status(
    binary: str | None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    """``{installed, running, dns_name, tailnet, funnel_capable, version, error}``."""
    out: dict[str, Any] = {
        "installed": binary is not None,
        "running": False,
        "dns_name": None,
        "tailnet": None,
        "funnel_capable": None,
        "version": None,
        "error": None,
    }
    if binary is None:
        out["error"] = "Tailscale is not installed on this device"
        return out
    try:
        proc = run([binary, "status", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        out["error"] = f"tailscale could not be run: {type(exc).__name__}"
        return out
    if proc.returncode != 0 and not proc.stdout.strip():
        out["error"] = (proc.stderr.strip() or "tailscale status failed")[:200]
        return out
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        out["error"] = "tailscale status did not return JSON"
        return out
    state = data.get("BackendState")
    me = data.get("Self") or {}
    out["running"] = state == "Running"
    out["version"] = data.get("Version")
    dns = str(me.get("DNSName") or "").rstrip(".")
    out["dns_name"] = dns or None
    out["tailnet"] = (data.get("CurrentTailnet") or {}).get("Name")
    caps = list((me.get("CapMap") or {}).keys()) + list(me.get("Capabilities") or [])
    if any("funnel" in str(c).lower() for c in caps):
        out["funnel_capable"] = True
    if not out["running"]:
        out["error"] = f"Tailscale is not running (state: {state or 'unknown'})"
    return out
