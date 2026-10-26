"""``foundryos up``: migrate, build the UI if needed, start the API and the worker, supervise them.

The pieces that matter are separated so they can be tested without real processes:
:func:`web_status` (is ``web/dist`` fresh?), :class:`Supervisor` (restart-with-backoff, critical
children, ordered shutdown) and :func:`run_up` (the sequence, with injectable ``spawn``/``sleep``).
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core import install
from app.core.config import BACKEND_DIR, REPO_DIR, Settings
from app.core.netinfo import is_loopback_bind
from app.core.owner_token import OWNER_TOKEN_FILENAME
from app.ops import headless, logs, pidfile, upgrade

HEALTH_TIMEOUT = 90.0
WEB_INPUTS = (
    "src",
    "public",
    "index.html",
    "package.json",
    "pnpm-lock.yaml",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
)


class ProcLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


# ------------------------------------------------------------------------------ web build


def _newest_mtime(web_dir: Path) -> float:
    newest = 0.0
    for name in WEB_INPUTS:
        path = web_dir / name
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d != "node_modules"]
                for f in files:
                    with contextlib.suppress(OSError):
                        newest = max(newest, (Path(root) / f).stat().st_mtime)
    return newest


def web_status(web_dir: Path, dist_dir: Path | None = None) -> str:
    """``missing`` (no index.html), ``stale`` (a source file is newer than the build) or ``fresh``.
    ``unknown`` when there is a build but no source tree (a packaged install: nothing to compare)."""
    dist = dist_dir or web_dir / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return "missing"
    if not (web_dir / "src").is_dir():
        return "unknown"
    return "stale" if _newest_mtime(web_dir) > index.stat().st_mtime else "fresh"


def build_web(web_dir: Path, say: Callable[[str], None], run=subprocess.run) -> bool:
    """``pnpm install`` (if needed) + ``pnpm build``. False when it cannot or fails; never raises."""
    pnpm = _which("pnpm")
    if pnpm is None or not (web_dir / "package.json").is_file():
        say("pnpm is not installed (or web/ is missing): not building the web UI.")
        return False
    try:
        if not (web_dir / "node_modules").is_dir():
            say("Installing web dependencies (pnpm install --frozen-lockfile)...")
            if (
                run([pnpm, "install", "--frozen-lockfile"], cwd=web_dir, check=False).returncode
                != 0
            ):
                say("pnpm install failed.")
                return False
        say("Building the web UI (pnpm build)...")
        if run([pnpm, "build"], cwd=web_dir, check=False).returncode != 0:
            say("pnpm build failed.")
            return False
    except OSError as exc:
        say(f"Could not run pnpm: {exc}")
        return False
    return True


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


# ------------------------------------------------------------------------------ migrations


def ensure_migrated(settings: Settings) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    cfg.attributes["configure_logger"] = False
    settings.prepare_data_dir()
    command.upgrade(cfg, "head")


# ------------------------------------------------------------------------------ supervisor


@dataclass
class ManagedChild:
    name: str
    start: Callable[[], ProcLike]
    restart: bool = True  # restart (with backoff) when it exits
    critical: bool = False  # its exit stops everything
    proc: ProcLike | None = None
    started_at: float = 0.0
    failures: int = 0
    next_start_at: float | None = None
    restarts: int = 0


@dataclass
class Supervisor:
    children: list[ManagedChild]
    say: Callable[[str], None] = print
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    backoff_cap: float = 60.0
    stable_after: float = 60.0  # running this long resets the failure count
    poll_interval: float = 0.5
    events: list[str] = field(default_factory=list)

    def _log(self, text: str) -> None:
        self.events.append(text)
        self.say(text)

    def start(self, child: ManagedChild) -> None:
        child.proc = child.start()
        child.started_at = self.clock()
        child.next_start_at = None

    def start_all(self) -> None:
        for child in self.children:
            if child.proc is None:
                self.start(child)

    @staticmethod
    def delay_for(failures: int, cap: float) -> float:
        return min(cap, float(2 ** max(0, failures - 1)))

    def step(self) -> str | None:
        """One supervision pass. Returns why the whole thing must stop, else None."""
        now = self.clock()
        for child in self.children:
            if child.proc is not None:
                code = child.proc.poll()
                if code is None:
                    if child.failures and now - child.started_at >= self.stable_after:
                        child.failures = 0
                    continue
                ran = now - child.started_at
                child.proc = None
                if child.critical:
                    return f"{child.name} exited with code {code}"
                if not child.restart:
                    self._log(f"{child.name} exited with code {code}")
                    continue
                child.failures = 1 if ran >= self.stable_after else child.failures + 1
                delay = self.delay_for(child.failures, self.backoff_cap)
                child.next_start_at = now + delay
                self._log(f"{child.name} exited with code {code}; restarting in {delay:g}s")
            elif child.next_start_at is not None and now >= child.next_start_at:
                child.restarts += 1
                self.start(child)
                self._log(f"{child.name} restarted (restart #{child.restarts})")
        return None

    def run(self, stop: threading.Event) -> str | None:
        """Supervise until ``stop`` is set (returns None) or a critical child dies (returns why)."""
        while not stop.is_set():
            reason = self.step()
            if reason:
                return reason
            if self.sleep is time.sleep:
                stop.wait(self.poll_interval)  # wakes immediately when a signal sets `stop`
            else:
                self.sleep(self.poll_interval)
        return None

    def stop_all(self, grace: float = 20.0) -> None:
        """Ask the children to stop in reverse start order (UI, worker, API), then force."""
        for child in reversed(self.children):
            proc = child.proc
            if proc is None or proc.poll() is not None:
                child.proc = None
                continue
            proc.terminate()
            deadline = self.clock() + grace
            while proc.poll() is None and self.clock() < deadline:
                self.sleep(0.1)
            if proc.poll() is None:
                self._log(f"{child.name} did not stop in {grace:g}s; killing it")
                proc.kill()
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    self.sleep(0.1)
            child.proc = None
            self._log(f"{child.name} stopped")


# ------------------------------------------------------------------------------ the sequence


@dataclass
class UpOptions:
    port: int = 8700
    host: str | None = None
    no_worker: bool = False
    open_browser: bool = False
    dev: bool = False
    build: bool = True  # build/refresh web/dist when missing or stale


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def child_env(
    settings: Settings, opts: UpOptions, base: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["COMPANY_OS_PORT"] = str(opts.port)
    env["COMPANY_OS_EMBEDDED_WORKER"] = "false"  # the worker is its own supervised process
    env.setdefault("COMPANY_OS_PUBLIC_URL", f"http://localhost:{opts.port}")
    env.setdefault("COMPANY_OS_AGENT_API_URL", f"http://127.0.0.1:{opts.port}/api/agent/v1")
    env.setdefault("COMPANY_OS_PRINT_LOGIN_URL", "0")  # `up` prints the setup instructions itself
    return env


def wait_healthy(
    port: int, proc: ProcLike, *, timeout: float, sleep=time.sleep, clock=time.monotonic
) -> bool:
    deadline = clock() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while clock() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        sleep(0.4)
    return False


def read_owner_token(settings: Settings) -> str | None:
    try:
        return (settings.resolved_data_dir / OWNER_TOKEN_FILENAME).read_text(
            encoding="utf-8"
        ).strip() or None
    except OSError:
        return None


def describe_access(
    port: int,
    bind_host: str,
    reach: dict | None,
    session: dict | None,
    token: str | None,
    tty: bool,
    *,
    remote: bool = False,
    public_url: str | None = None,
) -> list[str]:
    """The lines `up` prints once everything is running. ``remote``: there is no screen on this box
    (SSH, container, server), so say how to reach it from the user's own computer."""
    lines = ["", "Foundry AI OS is running.", f"  Local:    http://127.0.0.1:{port}"]
    if reach:
        for addr in (reach.get("lan") or {}).get("addresses", []):
            if addr.get("url") and not is_loopback_bind(bind_host):
                lines.append(f"  Network:  {addr['url']}")
        mdns = (reach.get("lan") or {}).get("mdns_url")
        if mdns and not is_loopback_bind(bind_host):
            lines.append(f"  Network:  {mdns}")
        url = (reach.get("tunnel") or {}).get("url")
        if url:
            lines.append(f"  Public:   {url}")
            public_url = None if public_url == url else public_url
    elif not is_loopback_bind(bind_host):
        lines.append(f"  Network:  bound to {bind_host}:{port}")
    if public_url:
        lines.append(f"  Public:   {public_url}")
    if is_loopback_bind(bind_host):
        lines.append(
            "  (this machine only; switch on network access in the web app under Reachability)"
        )
    if remote:
        lines += ["", *headless.remote_hints(port)]
    if session and session.get("state") == "setup_required":
        if remote:
            lines += [
                "",
                "First run: finish setup in a terminal with `foundryos setup`, or open the app through the SSH forward above.",
                f"  Setup:    http://127.0.0.1:{port}",
            ]
        else:
            lines += [
                "",
                "First run: open the app and create your account (or run `foundryos setup`).",
                f"  Setup:    http://127.0.0.1:{port}",
            ]
        remote = not is_loopback_bind(bind_host) or bool((reach or {}).get("tunnel", {}).get("url"))
        if remote:
            if tty and token:
                lines += [
                    "  From another device you also need this setup token (not needed on this machine):",
                    f"            {token}",
                ]
            else:
                lines.append(
                    "  From another device you also need the setup token in <data dir>/owner.token."
                )
    if os.environ.get("FOUNDRYOS_RUN_MODE"):
        lines += ["", "Stop it with: foundryos stop"]
    else:
        lines += ["", "Ctrl-C stops everything."]
    return lines


def run_up(
    opts: UpOptions,
    *,
    settings: Settings | None = None,
    spawn: Callable[..., ProcLike] | None = None,
    say: Callable[[str], None] = print,
    stop: threading.Event | None = None,
    install_signals: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    from app.serve import decide_host

    settings = settings or Settings()
    web_dir = REPO_DIR / "web"
    spawn = spawn or _popen
    stop = stop or threading.Event()

    if port_in_use(opts.port):
        say(
            f"Port {opts.port} is already in use (is Foundry AI OS already running? try `foundryos status` or `foundryos doctor`)."
        )
        return 1

    say("Checking the database...")
    if not upgrade.upgrade_database(settings, say, ensure_migrated):
        return 1

    dist = settings.resolved_web_dist
    if not opts.dev and opts.build and not install.bundle_root():
        state = web_status(web_dir, dist)
        if state in ("missing", "stale"):
            say(f"Web UI is {state}.")
            if not build_web(web_dir, say):
                say(
                    "Continuing without a fresh web UI."
                    if state == "stale"
                    else "Continuing: the API will run without the web UI."
                )

    host, warning = decide_host(opts.host)
    if warning:
        say(warning)
    env = child_env(settings, opts)
    backend_cmd = [sys.executable, "-m", "app.serve", "--port", str(opts.port)]
    if opts.host:
        backend_cmd += ["--host", opts.host]

    children = [
        ManagedChild(
            "api",
            lambda: spawn(backend_cmd, env=env, cwd=BACKEND_DIR),
            restart=False,
            critical=True,
        )
    ]
    if not opts.no_worker:
        worker_cmd = [sys.executable, "-m", "app.engine.cli"]
        children.append(ManagedChild("worker", lambda: spawn(worker_cmd, env=env, cwd=BACKEND_DIR)))
    if opts.dev:
        dev_env = {**env, "API_PROXY_TARGET": f"http://127.0.0.1:{opts.port}"}
        pnpm = _which("pnpm") or "pnpm"
        children.append(
            ManagedChild("web-dev", lambda: spawn([pnpm, "dev"], env=dev_env, cwd=web_dir))
        )

    sup = Supervisor(children, say=say, sleep=sleep, clock=clock)

    def _signal(signum, _frame) -> None:
        stop.set()

    previous: dict[int, Any] = {}
    if install_signals and threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, _signal)

    exit_code = 0
    run_file = pidfile.register(
        settings.resolved_data_dir,
        "supervisor",
        port=opts.port,
        extra={"mode": os.environ.get("FOUNDRYOS_RUN_MODE") or "foreground"},
    )
    rotator = None
    if os.environ.get("FOUNDRYOS_RUN_MODE") in ("background", "service") or os.environ.get(
        "FOUNDRYOS_LOG_FILE"
    ):
        rotator = logs.Rotator(logs.log_path(settings)).start()
    try:
        api = children[0]
        sup.start(api)
        say("Starting the API...")
        assert api.proc is not None
        if not wait_healthy(opts.port, api.proc, timeout=HEALTH_TIMEOUT, sleep=sleep):
            say("The API did not become healthy (see its output above).")
            return 1
        for child in children[1:]:
            sup.start(child)
        _print_ready(settings, opts, host, say)
        if opts.open_browser:
            target = f"http://127.0.0.1:{5173 if opts.dev else opts.port}"
            if not headless.open_browser(target):
                say(f"Not opening a browser here (no screen detected). Address: {target}")
        reason = sup.run(stop)
        if reason:
            say(f"Stopping: {reason}.")
            exit_code = 1
        else:
            say("\nShutting down...")
    finally:
        sup.stop_all()
        if rotator is not None:
            rotator.stop()
        pidfile.unregister(run_file)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return exit_code


def _print_ready(
    settings: Settings, opts: UpOptions, host: str, say: Callable[[str], None]
) -> None:
    token = read_owner_token(settings)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = f"http://127.0.0.1:{opts.port}/api"
    reach = session = None
    try:
        session = httpx.get(f"{base}/auth/session", timeout=3.0).json()
        resp = httpx.get(f"{base}/reachability", headers=headers, timeout=5.0)
        reach = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        pass
    from app.ops import lifecycle

    public = lifecycle.read_public_url(settings)
    lines = describe_access(
        opts.port,
        host,
        reach,
        session,
        token,
        sys.stdout.isatty(),
        remote=headless.is_headless(),
        public_url=public["url"] if public else None,
    )
    for line in lines:
        say(line)
    if opts.dev:
        say("  Web dev server: http://localhost:5173 (Vite prints the port it actually used)")


def _popen(cmd: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(cmd, env=env, cwd=cwd, start_new_session=True)
