"""The tunnel supervisor: one managed child process that makes this device reachable publicly.

* ``cloudflare_quick``: ``cloudflared tunnel --url http://127.0.0.1:<port>``; the random
  ``*.trycloudflare.com`` URL is parsed from the process output (no account, ephemeral).
* ``cloudflare_named``: ``cloudflared tunnel run`` with a token for a tunnel created through the
  connected Cloudflare account; the hostname is known up front, "running" once cloudflared reports a
  registered connection.
* ``tailscale_funnel``: ``tailscale funnel <port>`` in the foreground; the ``*.ts.net`` URL comes
  from its output.

The supervisor keeps the process alive: when it exits it is restarted with exponential backoff
(1 s, 2 s, 4 s ... capped), a streak of failures ends in an ``error`` state instead of a loop, and a
periodic health check of ``<url>/api/health`` restarts a tunnel that is up but not serving. The
child gets a minimal environment (never the server's), its output is kept in a bounded, redacted
tail. The exposure rules (password auth) are enforced by the caller before ``start``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.time import utcnow
from app.services.secrets_scan import scrub

logger = logging.getLogger("company_os.tunnel")

QUICK_URL = re.compile(r"https://([a-z0-9][a-z0-9-]*)\.trycloudflare\.com", re.IGNORECASE)
TS_URL = re.compile(r"https://[a-z0-9][a-z0-9.-]*\.ts\.net", re.IGNORECASE)
NAMED_READY = re.compile(r"registered tunnel connection|connection [0-9a-f-]+ registered", re.I)
LONG_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{60,}")
LOG_LINES = 200
UNHEALTHY_RESTART_AFTER = 3


@dataclass
class TunnelSpec:
    provider: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    hostname: str | None = None
    known_url: str | None = None  # named tunnel: set up front
    secrets: tuple[str, ...] = ()


def redact_line(line: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret and secret in line:
            line = line.replace(secret, "[redacted]")
    line = LONG_BLOB.sub("[redacted]", line)
    return (scrub(line) or "").rstrip()[:500]


def default_health_check(url: str) -> bool:
    import httpx

    try:
        resp = httpx.get(url.rstrip("/") + "/api/health", timeout=10, follow_redirects=False)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class TunnelSupervisor:
    def __init__(
        self,
        *,
        health_interval: float = 30.0,
        start_timeout: float = 45.0,
        max_backoff: float = 60.0,
        base_backoff: float = 1.0,
        max_failures: int = 8,
        stable_seconds: float = 60.0,
        kill_grace: float = 5.0,
        poll_interval: float = 0.1,
        health_check: Callable[[str], bool] = default_health_check,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.health_interval = health_interval
        self.start_timeout = start_timeout
        self.max_backoff = max_backoff
        self.base_backoff = base_backoff
        self.max_failures = max_failures
        self.stable_seconds = stable_seconds
        self.kill_grace = kill_grace
        self.poll_interval = poll_interval
        self._health_check = health_check
        self._popen = popen
        self._on_change = on_change
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._spec: TunnelSpec | None = None
        self._logs: deque[str] = deque(maxlen=LOG_LINES)
        self._reset_state()

    # ------------------------------------------------------------------ state

    def _reset_state(self) -> None:
        self.state = "stopped"
        self.url: str | None = None
        self.error: str | None = None
        self.healthy: bool | None = None
        self.last_health_check_at: datetime | None = None
        self.restarts = 0
        self.started_at: datetime | None = None

    def _set(self, **changes: Any) -> None:
        with self._lock:
            before = (self.state, self.url, self.error, self.healthy)
            for key, value in changes.items():
                setattr(self, key, value)
            after = (self.state, self.url, self.error, self.healthy)
        if before != after and self._on_change:
            try:
                self._on_change(self.status())
            except Exception:  # a listener must never break the supervisor
                logger.exception("tunnel change listener failed")

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            spec = self._spec
            return {
                "provider": spec.provider if spec else None,
                "state": self.state,
                "url": self.url,
                "hostname": spec.hostname if spec else None,
                "last_health_check_at": self.last_health_check_at,
                "healthy": self.healthy,
                "restarts": self.restarts,
                "started_at": self.started_at,
                "error": self.error,
            }

    def logs(self, limit: int = 100) -> list[str]:
        with self._lock:
            return list(self._logs)[-limit:]

    # ------------------------------------------------------------------ control

    def start(self, spec: TunnelSpec) -> None:
        with self._lock:
            if self.active:
                raise RuntimeError("the tunnel is already running")
            self._stop = threading.Event()
            self._spec = spec
            self._logs.clear()
            self._reset_state()
            self.state = "starting"
            self.started_at = utcnow()
            self._thread = threading.Thread(target=self._run, name="tunnel-supervisor", daemon=True)
            self._thread.start()
        self._set()

    def stop(self) -> None:
        with self._lock:
            thread, stop = self._thread, self._stop
        stop.set()
        self._kill()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.kill_grace + 5)
        with self._lock:
            self._thread = None
            self.state, self.url, self.healthy, self.error = "stopped", None, None, None
        self._set()

    def _kill(self, proc: subprocess.Popen | None = None) -> None:
        if proc is None:
            with self._lock:
                proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=self.kill_grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except OSError:
            pass

    # ------------------------------------------------------------------ the loop

    def _child_env(self, spec: TunnelSpec) -> dict[str, str]:
        env = {k: v for k in ("PATH", "HOME", "LANG", "TMPDIR") if (v := os.environ.get(k))}
        env.update(spec.env)
        return env

    def _spawn(self, spec: TunnelSpec) -> subprocess.Popen:
        proc = self._popen(
            spec.argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=self._child_env(spec),
            start_new_session=True,
        )
        with self._lock:
            self._proc = proc  # before the reader starts: it only observes the current process
        threading.Thread(target=self._read, args=(proc, spec), daemon=True).start()
        return proc

    def _read(self, proc: subprocess.Popen, spec: TunnelSpec) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                line = redact_line(raw, spec.secrets)
                if not line:
                    continue
                with self._lock:
                    self._logs.append(line)
                    if proc is not self._proc:
                        continue
                self._observe(line, spec)
        except (OSError, ValueError):
            pass

    def _observe(self, line: str, spec: TunnelSpec) -> None:
        if spec.provider == "cloudflare_quick":
            for match in QUICK_URL.finditer(line):
                if match.group(1).lower() != "api":
                    self._set(state="running", url=match.group(0).lower(), error=None)
                    return
        elif spec.provider == "cloudflare_named":
            if self.state == "starting" and NAMED_READY.search(line):
                self._set(state="running", url=spec.known_url, error=None)
        elif spec.provider == "tailscale_funnel":
            match = TS_URL.search(line)
            if match:
                self._set(state="running", url=match.group(0).lower(), error=None)

    def _run(self) -> None:
        spec = self._spec
        assert spec is not None
        failures = 0
        while not self._stop.is_set():
            try:
                proc = self._spawn(spec)
            except OSError as exc:
                self._set(
                    state="error", error=f"could not start {spec.argv[0]}: {exc.strerror or exc}"
                )
                return
            began = time.monotonic()
            reached_running, note = self._watch(proc, spec)
            if self._stop.is_set():
                self._kill(proc)
                return
            code = proc.poll()
            lived = time.monotonic() - began
            if reached_running and lived >= self.stable_seconds:
                failures = 0
            failures += 1
            if note:
                reason = note
            elif code is not None:
                reason = f"{os.path.basename(spec.argv[0])} exited with code {code}"
            else:
                reason = "the tunnel process ended"
            tail = [ln for ln in self.logs(5) if ln]
            if tail:
                reason += f" ({tail[-1][:160]})"
            if failures > self.max_failures:
                self._set(
                    state="error",
                    url=None,
                    healthy=None,
                    error=f"gave up after {failures - 1} restarts: {reason}",
                )
                return
            delay = min(self.base_backoff * (2 ** (failures - 1)), self.max_backoff)
            with self._lock:
                self.restarts += 1
            self._set(
                state="starting",
                url=None,
                healthy=None,
                error=f"{reason}; restarting in {delay:g}s",
            )
            if self._stop.wait(delay):
                return

    def _watch(self, proc: subprocess.Popen, spec: TunnelSpec) -> tuple[bool, str | None]:
        """Block while ``proc`` lives. Returns (whether it ever reached ``running``, why it was
        killed or None). Kills it when it never becomes ready or stays unhealthy."""
        reached = False
        launched = time.monotonic()
        # the first health check comes shortly after the URL appears, then every interval
        first_delay = min(self.health_interval, 5.0)
        last_health = time.monotonic() - (self.health_interval - first_delay)
        bad_checks = 0
        while not self._stop.is_set():
            if proc.poll() is not None:
                return reached, None
            with self._lock:
                state, url = self.state, self.url
            if state == "running":
                reached = True
                if time.monotonic() - last_health >= self.health_interval and url:
                    last_health = time.monotonic()
                    ok = False
                    try:
                        ok = bool(self._health_check(url))
                    except Exception:
                        ok = False
                    bad_checks = 0 if ok else bad_checks + 1
                    self._set(healthy=ok, last_health_check_at=utcnow())
                    if bad_checks >= UNHEALTHY_RESTART_AFTER:
                        self._kill(proc)
                        return reached, "the public URL stopped answering health checks"

            elif time.monotonic() - launched > self.start_timeout:
                self._kill(proc)
                return reached, f"the tunnel did not become ready within {self.start_timeout:g}s"
            self._stop.wait(self.poll_interval)
        return reached, None
