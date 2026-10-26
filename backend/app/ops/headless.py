"""Is there a screen to open a browser on? (Over SSH, in a container or on a server: no.)

``foundryos`` must work on a box that is only reachable over SSH. On such a box it must never try to open
a browser: over SSH from a Mac that would open Safari on the remote console, and on a Linux server
``webbrowser`` can launch a text browser (w3m, lynx) that blocks the terminal. Instead it prints the local
address plus how to reach it from the user's own computer.

Rules (first match wins):

* ``FOUNDRYOS_OPEN_BROWSER=1``  -> not headless (the user insists);
* ``FOUNDRYOS_NO_BROWSER`` set  -> headless;
* ``SSH_CONNECTION`` / ``SSH_TTY`` set -> headless;
* Linux without ``DISPLAY`` and ``WAYLAND_DISPLAY`` -> headless;
* ``/.dockerenv`` exists -> headless.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path

from app.core import install

DOCKERENV = Path("/.dockerenv")  # a module constant so tests can point it elsewhere
# Text-mode browsers that `webbrowser` may pick; they would take over the terminal.
CONSOLE_BROWSERS = frozenset({"www-browser", "links", "elinks", "lynx", "w3m", "links2"})
_STDLIB_OPEN = webbrowser.open


def _truthy(value: str | None) -> bool:
    return (value or "").strip() not in ("", "0")


def is_headless(environ: Mapping[str, str] | None = None, system: str | None = None) -> bool:
    env = os.environ if environ is None else environ
    if _truthy(env.get("FOUNDRYOS_OPEN_BROWSER")):
        return False
    if _truthy(env.get("FOUNDRYOS_NO_BROWSER")):
        return True
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return True
    if (system or install.current_os()) == "linux" and not (
        env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
    ):
        return True
    return DOCKERENV.exists()


def remote_hints(port: int) -> list[str]:
    """How to reach this server from the computer in front of the user."""
    return [
        "To use it from your own computer, forward the port over SSH and browse to it locally:",
        f"  ssh -N -L {port}:127.0.0.1:{port} user@host      (user@host: how you SSH into this machine)",
        f"  then open http://localhost:{port}",
        "  Passkeys work there: browsers treat localhost as a secure address.",
    ]


def stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (ValueError, OSError):
        return False


def open_browser(
    url: str,
    opener: Callable[[str], bool] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Open ``url`` in a browser, unless this is a headless box. Never raises; False = not opened."""
    if is_headless(environ):
        return False
    if opener is None and webbrowser.open is _STDLIB_OPEN:  # (tests replace `open`)
        try:
            name = getattr(webbrowser.get(), "name", "") or ""
        except webbrowser.Error:
            return False
        if os.path.basename(name) in CONSOLE_BROWSERS:
            return False
    try:
        return bool((opener or webbrowser.open)(url))
    except webbrowser.Error:
        return False
