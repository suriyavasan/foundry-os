"""Starting, stopping and opening Foundry AI OS as a background program.

``foundryos`` (no arguments) means "make it work":

* already running  -> open the browser;
* not running      -> start it (through the installed background service when there is one, else as a
  detached ``foundryos serve`` process that writes ``<data dir>/logs/foundry-os.log``), wait for
  ``/api/health``, then open the browser.

Who is running is answered by the health endpoint (is something Foundry listening on our port?) plus
the run files in ``<data dir>/run`` (the supervisor registers itself there). Every outside effect (spawn,
HTTP, signals, browser) is injectable, so the whole flow is tested without real processes.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core import install
from app.core.config import Settings
from app.ops import logs, pidfile, services

START_TIMEOUT = 120.0
STOP_TIMEOUT = 60.0
SUPERVISOR_ROLE = "supervisor"


class LifecycleError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.code = code


@dataclass
class RunState:
    running: bool
    port: int
    url: str
    version: str | None = None
    managed_by: str | None = None  # service | background | foreground | unknown
    supervisor_pid: int | None = None
    service: services.ServiceStatus | None = None
    port_busy_by_other: bool = False
    pids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "url": self.url,
            "port": self.port,
            "version": self.version,
            "managed_by": self.managed_by,
            "supervisor_pid": self.supervisor_pid,
            "service": self.service.as_dict() if self.service else None,
        }


# ------------------------------------------------------------------------------ probing


def probe_health(
    port: int, *, get: Callable[..., Any] = httpx.get, timeout: float = 1.5
) -> dict | None:
    """The health document when something Foundry answers on ``port``, else None."""
    try:
        resp = get(f"http://127.0.0.1:{port}/api/health", timeout=timeout)
        body = resp.json()
    except (httpx.HTTPError, ValueError, OSError):
        return None
    if isinstance(body, dict) and "status" in body and "auth_mode" in body:
        return body
    return None


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def supervisors(settings: Settings) -> list[dict]:
    return [
        p
        for p in pidfile.live_processes(settings.resolved_data_dir)
        if p.get("role") == SUPERVISOR_ROLE
    ]


def run_state(
    settings: Settings,
    *,
    probe: Callable[[int], dict | None] | None = None,
    busy: Callable[[int], bool] | None = None,
) -> RunState:
    port = settings.port
    health = (probe or probe_health)(port)
    sups = supervisors(settings)
    svc_status = None
    svc = services.active_service()
    if svc is not None:
        try:
            svc_status = svc.status()
        except services.ServiceError:
            svc_status = None
    state = RunState(
        running=health is not None,
        port=port,
        url=f"http://127.0.0.1:{port}",
        version=(health or {}).get("version"),
        service=svc_status,
        pids=[int(p["pid"]) for p in sups],
    )
    if sups:
        state.supervisor_pid = int(sups[0]["pid"])
    if health is not None:
        if svc_status is not None and svc_status.running:
            state.managed_by = "service"
        elif sups:
            mode = sups[0].get("mode")
            state.managed_by = (
                "service"
                if mode == "service"
                else ("background" if mode == "background" else "foreground")
            )
        else:
            state.managed_by = "unknown"
    else:
        state.port_busy_by_other = (busy or port_in_use)(port)
    return state


def describe(state: RunState) -> str:
    if not state.running:
        return "Foundry AI OS is not running."
    how = {
        "service": "as the background service",
        "background": "in the background",
        "foreground": "in a terminal (foundryos up / serve)",
        "unknown": "(started by something else)",
    }.get(state.managed_by or "", "")
    pid = f", pid {state.supervisor_pid}" if state.supervisor_pid else ""
    ver = f" v{state.version}" if state.version else ""
    return f"Foundry AI OS{ver} is running {how} at {state.url}{pid}.".replace("  ", " ")


# ------------------------------------------------------------------------------ start


class _ServiceProc:
    """Stands in for a process we did not spawn: it never 'exits' (the service manager owns it)."""

    pid = 0

    def poll(self) -> None:
        return None


def spawn_detached(
    cmd: list[str], *, env: dict[str, str], cwd: Path, log_file: Path
) -> subprocess.Popen:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logs.rotate_if_large(log_file)
    with log_file.open("ab") as out:
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
        )


def background_command(settings: Settings) -> tuple[list[str], Path]:
    cmd = [*install.launcher_command(), "serve", "--port", str(settings.port)]
    source = install.install_kind() == install.SOURCE
    cwd = install.BACKEND_DIR if source else settings.resolved_data_dir
    cwd.mkdir(mode=0o700, parents=True, exist_ok=True)
    return cmd, cwd


def _wait_healthy(
    settings: Settings,
    proc: Any,
    *,
    timeout: float,
    probe: Callable[[int], dict | None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if probe(settings.port) is not None:
            return True
        if proc.poll() is not None:
            return False
        sleep(0.4)
    return False


def log_tail_hint(settings: Settings, count: int = 15) -> str:
    path = logs.pick_log(settings)
    if path is None:
        return "No log was written yet."
    lines = logs.tail_lines(path, count)
    return f"Last lines of {path}:\n" + "\n".join("  " + line for line in lines)


def start(
    settings: Settings,
    say: Callable[[str], None] = print,
    *,
    timeout: float | None = None,
    spawn: Callable[..., Any] | None = None,
    probe: Callable[[int], dict | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RunState:
    """Start it in the background and wait until it answers. Returns the running state."""
    probe_fn = probe or probe_health
    timeout = START_TIMEOUT if timeout is None else timeout
    spawn = spawn or spawn_detached
    state = run_state(settings, probe=probe_fn)
    if state.running:
        return state
    if state.port_busy_by_other:
        raise LifecycleError(
            f"Port {settings.port} is in use by another program, so Foundry AI OS cannot start. "
            "Stop that program, or choose another port: FOUNDRYOS_PORT=8701 foundryos",
            code="port_in_use",
        )
    svc = services.active_service()
    if svc is not None:
        say(f"Starting Foundry AI OS through its background service ({svc.name})...")
        try:
            svc.start()
        except services.ServiceError as exc:
            raise LifecycleError(str(exc), code="service_error") from exc
        proc: Any = _ServiceProc()
    else:
        say("Starting Foundry AI OS in the background...")
        cmd, cwd = background_command(settings)
        env = {**os.environ, "FOUNDRYOS_RUN_MODE": "background", "PYTHONUNBUFFERED": "1"}
        try:
            proc = spawn(cmd, env=env, cwd=cwd, log_file=logs.log_path(settings))
        except OSError as exc:
            raise LifecycleError(f"Could not start {cmd[0]}: {exc}", code="spawn_failed") from exc
    if not _wait_healthy(settings, proc, timeout=timeout, probe=probe_fn, sleep=sleep, clock=clock):
        exited = proc.poll() is not None
        why = "stopped right after starting" if exited else f"did not answer within {timeout:g} s"
        raise LifecycleError(
            f"Foundry AI OS {why}.\n{log_tail_hint(settings)}\n"
            "Run `foundryos logs` for the full log or `foundryos doctor` to check this machine.",
            code="start_failed",
        )
    return run_state(settings, probe=probe_fn)


# ------------------------------------------------------------------------------ stop


def _alive(pid: int) -> bool:
    """Is ``pid`` still running? A child of this very process that has exited is reaped first (an
    unreaped zombie would still answer ``kill(pid, 0)``)."""
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)
    return pidfile._alive(pid)


def _wait_gone(pids: list[int], timeout: float, sleep: Callable[[float], None], clock) -> list[int]:
    deadline = clock() + timeout
    alive = list(pids)
    while alive and clock() < deadline:
        alive = [p for p in alive if _alive(p)]
        if alive:
            sleep(0.2)
    return alive


def stop(
    settings: Settings,
    say: Callable[[str], None] = print,
    *,
    timeout: float | None = None,
    probe: Callable[[int], dict | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    kill: Callable[[int, int], None] = os.kill,
) -> bool:
    """Stop the background copy. Returns True when something was stopped."""
    probe_fn = probe or probe_health
    timeout = STOP_TIMEOUT if timeout is None else timeout
    stopped = False
    svc = services.active_service()
    if svc is not None:
        try:
            st = svc.status()
            if st.running or (st.manager == "launchd" and st.loaded):
                say(f"Stopping the background service ({svc.name})...")
                svc.stop()
                stopped = True
        except services.ServiceError as exc:
            say(f"Could not stop the service: {exc}")
    sups = supervisors(settings)
    pids = [int(p["pid"]) for p in sups]
    if stopped and pids:
        pids = _wait_gone(pids, timeout, sleep, clock)
    for pid in pids:
        say(f"Stopping Foundry AI OS (pid {pid})...")
        with contextlib.suppress(ProcessLookupError, PermissionError):
            kill(pid, signal.SIGTERM)
        stopped = True
    remaining = _wait_gone(pids, timeout, sleep, clock) if pids else []
    if remaining:
        say(f"It did not stop within {timeout:g} s; forcing it.")
        for pid in remaining:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                kill(pid, signal.SIGKILL)
        _wait_gone(remaining, 5.0, sleep, clock)
        # children of a killed supervisor are not stopped by it: ask the registered ones to stop
        for entry in pidfile.live_processes(settings.resolved_data_dir):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                kill(int(entry["pid"]), signal.SIGTERM)
    if not stopped and probe_fn(settings.port) is not None:
        raise LifecycleError(
            f"Something answers on port {settings.port} but it was not started by this install "
            "(no run file, no service). Stop it yourself.",
            code="not_ours",
        )
    return stopped


def restart(settings: Settings, say: Callable[[str], None] = print, **kwargs: Any) -> RunState:
    start_kwargs = {
        k: v for k, v in kwargs.items() if k in ("spawn", "probe", "sleep", "clock", "timeout")
    }
    svc = services.active_service()
    if svc is not None:
        try:
            st = svc.status()
        except services.ServiceError:
            st = None
        if st is not None and (st.running or st.loaded):
            say(f"Restarting the background service ({svc.name})...")
            before = [int(p["pid"]) for p in supervisors(settings)]
            try:
                svc.restart()
            except services.ServiceError as exc:
                raise LifecycleError(str(exc), code="service_error") from exc
            probe_fn = kwargs.get("probe") or probe_health
            # launchd restarts asynchronously: do not mistake the old process's last answers for the new one
            _wait_gone(
                before,
                kwargs.get("timeout") or STOP_TIMEOUT,
                kwargs.get("sleep", time.sleep),
                kwargs.get("clock", time.monotonic),
            )
            if not _wait_healthy(
                settings,
                _ServiceProc(),
                timeout=kwargs.get("timeout", START_TIMEOUT),
                probe=probe_fn,
                sleep=kwargs.get("sleep", time.sleep),
                clock=kwargs.get("clock", time.monotonic),
            ):
                raise LifecycleError(
                    f"Foundry AI OS did not come back.\n{log_tail_hint(settings)}",
                    code="start_failed",
                )
            return run_state(settings, probe=probe_fn)
    stop_kwargs = {
        k: v for k, v in kwargs.items() if k in ("probe", "sleep", "clock", "kill", "timeout")
    }
    stop(settings, say, **stop_kwargs)
    return start(settings, say, **start_kwargs)


# ------------------------------------------------------------------------------ browser


def open_in_browser(url: str, opener: Callable[[str], bool] | None = None) -> bool:
    if os.environ.get("FOUNDRYOS_NO_BROWSER", "").strip() not in ("", "0"):
        return False
    try:
        return bool((opener or webbrowser.open)(url))
    except webbrowser.Error:
        return False


def setup_pending(port: int, get: Callable[..., Any] = httpx.get) -> bool:
    try:
        body = get(f"http://127.0.0.1:{port}/api/auth/session", timeout=2.0).json()
    except (httpx.HTTPError, ValueError, OSError):
        return False
    return isinstance(body, dict) and body.get("state") == "setup_required"
