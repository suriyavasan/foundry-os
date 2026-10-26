"""``foundryos doctor``: is this machine ready to run Foundry AI OS, and if not, what to do about it."""

from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.core import install
from app.core.config import Settings, sqlite_file_path
from app.core.owner_token import OWNER_TOKEN_FILENAME
from app.ops import headless, pidfile, services

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MIN_CONTEXT = 32768
LOW_DISK_WARN = 2 * 1024**3
LOW_DISK_FAIL = 300 * 1024**2


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def ollama_base() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def _run(
    cmd: list[str], timeout: float = 5.0, cwd: Path | None = None
) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 12)
    uv = shutil.which("uv")
    detail = f"Python {v.major}.{v.minor}.{v.micro}; uv {'found' if uv else 'not found'}"
    if ok and install.bundle_root() is not None:
        return Check("python", OK, f"{detail.split(';')[0]} (bundled with Foundry AI OS)")
    if not ok:
        return Check(
            "python",
            FAIL,
            detail,
            "Foundry AI OS needs Python 3.12 or newer (uv python install 3.12).",
        )
    if not uv:
        return Check(
            "python",
            WARN,
            detail,
            "Install uv (https://docs.astral.sh/uv/) to manage the backend environment.",
        )
    return Check("python", OK, detail)


def check_node(settings: Settings | None = None) -> Check:
    pnpm, node = shutil.which("pnpm"), shutil.which("node")
    dist = (settings or Settings()).resolved_web_dist / "index.html"
    if install.bundle_root() is not None:
        if dist.is_file():
            return Check("web ui", OK, f"built ({dist.parent}), shipped with this release")
        return Check(
            "web ui",
            FAIL,
            f"missing ({dist.parent})",
            "Reinstall Foundry AI OS: this release is incomplete.",
        )
    if dist.is_file():
        return Check(
            "web ui",
            OK,
            f"built ({dist.parent}); pnpm {'found' if pnpm else 'not found (only needed to rebuild)'}",
        )
    if pnpm and node:
        return Check("web ui", WARN, "not built yet; `foundryos up` will build it", None)
    return Check(
        "web ui",
        WARN,
        "not built and pnpm/node are missing: the API would run without the UI",
        "Install Node >= 20 and pnpm, then run `foundryos up`.",
    )


def check_database(settings: Settings) -> Check:
    from sqlalchemy import text

    from app.db.session import build_engine

    url = settings.resolved_database_url
    path = sqlite_file_path(url)
    if path is not None and not path.exists():
        return Check(
            "database",
            WARN,
            f"{path} does not exist yet",
            "`foundryos up` creates and migrates it.",
        )
    engine = build_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                current = None
    except Exception as exc:
        return Check(
            "database",
            FAIL,
            f"cannot connect: {exc.__class__.__name__}: {exc}",
            "Check COMPANY_OS_DATABASE_URL.",
        )
    finally:
        engine.dispose()
    from app.ops.backup import _head_revision

    head = _head_revision()
    where = str(path) if path else url.split("@")[-1]
    if current is None:
        return Check(
            "database",
            WARN,
            f"{where}: no migrations applied",
            "Run `uv run alembic upgrade head` (or `foundryos up`).",
        )
    if head and current != head:
        return Check(
            "database",
            WARN,
            f"{where}: at {current}, code expects {head}",
            "Run `uv run alembic upgrade head` (or `foundryos up`).",
        )
    return Check("database", OK, f"{where}: migrations at head ({current})")


def check_runtime(settings: Settings) -> Check:
    python = settings.resolved_runtime_python
    if not python.exists():
        return Check(
            "agent runtime",
            WARN,
            f"runtime venv not found ({python})",
            "cd runtime && uv sync   (agents cannot run without it; skills from GitHub cannot be scanned)",
        )
    # Same as the worker's launch: the runtime package is importable from runtime/ (cwd).
    result = _run(
        [str(python), "-c", "import company_agent; print('ok')"],
        timeout=60,
        cwd=settings.resolved_runtime_dir if settings.resolved_runtime_dir.is_dir() else None,
    )
    if result is None or result.returncode != 0:
        tail = (result.stderr.strip().splitlines() or [""])[-1] if result else "timed out"
        return Check(
            "agent runtime",
            FAIL,
            f"`import company_agent` failed: {tail}",
            "Rebuild it: cd runtime && uv sync",
        )
    return Check("agent runtime", OK, f"{python} imports company_agent")


def check_ollama(fetch: Callable[[str], dict | None] | None = None) -> Check:
    base = ollama_base()

    def _get(path: str) -> dict | None:
        try:
            r = httpx.get(base + path, timeout=3.0)
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    get = fetch or _get
    tags = get("/api/tags")
    if tags is None:
        return Check(
            "ollama",
            WARN,
            f"not reachable at {base}",
            "Install Ollama and start it with: OLLAMA_CONTEXT_LENGTH=32768 ollama serve  (or use a cloud provider)",
        )
    models = [m.get("name") for m in tags.get("models", [])]
    ps = get("/api/ps") or {}
    loaded = ps.get("models", [])
    contexts = [m.get("context_length") for m in loaded if m.get("context_length")]
    note = f"{len(models)} model(s) installed"
    if contexts:
        low = [c for c in contexts if c < MIN_CONTEXT]
        if low:
            return Check(
                "ollama",
                WARN,
                f"{note}; serving a context of {min(low)} tokens (< {MIN_CONTEXT})",
                "Restart Ollama with OLLAMA_CONTEXT_LENGTH=32768 (its OpenAI endpoint ignores num_ctx), "
                "otherwise long agent prompts are silently truncated.",
            )
        return Check("ollama", OK, f"{note}; serving context {min(contexts)}")
    return Check(
        "ollama",
        OK if models else WARN,
        f"{note}; no model loaded, so the served context is unknown until one runs",
        None if models else "ollama pull qwen2.5:7b",
    )


def check_docker() -> Check:
    docker = shutil.which("docker")
    if not docker:
        return Check(
            "docker",
            INFO,
            "not installed: agents run commands on the host (no sandbox)",
            "Install Docker to sandbox agent terminals.",
        )
    result = _run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=8)
    if result is None or result.returncode != 0:
        return Check(
            "docker",
            INFO,
            "installed, daemon not running: agent terminals use the host fallback",
            "Start Docker Desktop / the docker service to sandbox them.",
        )
    return Check("docker", OK, f"daemon {result.stdout.strip()}")


def check_tunnel_tools(settings: Settings) -> Check:
    cloudflared = shutil.which("cloudflared") or (
        str(p) if (p := settings.resolved_bin_dir / "cloudflared").exists() else None
    )
    tailscale = shutil.which("tailscale")
    return Check(
        "tunnel tools",
        INFO,
        f"cloudflared: {cloudflared or 'not found'}; tailscale: {tailscale or 'not found'}",
        None
        if (cloudflared or tailscale)
        else "Only needed for a public URL (Reachability in the web app).",
    )


def check_disk(settings: Settings) -> Check:
    data = settings.resolved_data_dir
    probe = data if data.exists() else data.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    gb = free / 1024**3
    if free < LOW_DISK_FAIL:
        return Check(
            "disk",
            FAIL,
            f"{gb:.2f} GB free near {data}",
            "Free some space; SQLite and artifacts need room to write.",
        )
    if free < LOW_DISK_WARN:
        return Check(
            "disk",
            WARN,
            f"{gb:.1f} GB free near {data}",
            "Low on space: keep an eye on artifacts and backups.",
        )
    return Check("disk", OK, f"{gb:.1f} GB free")


def check_permissions(settings: Settings) -> Check:
    data = settings.resolved_data_dir
    problems, found = [], []
    for name in (OWNER_TOKEN_FILENAME, "secret.key"):
        path = data / name
        if not path.exists():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        found.append(name)
        if mode & 0o077:
            problems.append(f"{name} is mode {mode:04o}")
    if problems:
        return Check(
            "secret files",
            FAIL,
            "; ".join(problems),
            f"chmod 600 {data}/owner.token {data}/secret.key",
        )
    if not found:
        return Check(
            "secret files", INFO, "no owner.token / secret.key yet (created at first start)"
        )
    return Check("secret files", OK, f"{', '.join(found)} readable by this user only (0600)")


def check_auth_gate(settings: Settings) -> Check:
    from sqlalchemy import inspect

    from app.db.session import build_engine, build_session_factory
    from app.services import reachability

    mode = settings.auth_mode
    engine = build_engine(settings.resolved_database_url)
    try:
        if "system_state" not in inspect(engine).get_table_names():
            return Check("auth", INFO, f"mode {mode}; database not initialised yet")
        with build_session_factory(engine)() as db:
            block = reachability.exposure_block(db, settings)
    except Exception as exc:
        return Check(
            "auth", WARN, f"mode {mode}; exposure gate could not be read ({exc.__class__.__name__})"
        )
    finally:
        engine.dispose()
    if mode == "local":
        return Check(
            "auth",
            WARN,
            "mode local (development only): network exposure is refused",
            "Use the default password mode for real use.",
        )
    if block:
        return Check(
            "auth", INFO, f"mode {mode}; network/public exposure is blocked: {block['reason']}"
        )
    return Check("auth", OK, f"mode {mode}; exposure gate open (password sign-in is set up)")


def check_install(settings: Settings) -> Check:
    kind = install.install_kind()
    where = {
        install.SOURCE: "source checkout",
        install.INSTALLER: "installer",
        install.HOMEBREW: "Homebrew",
        install.BUNDLE: "release bundle (unpacked by hand)",
        install.DOCKER: "Docker",
    }[kind]
    return Check(
        "install",
        INFO,
        f"Foundry AI OS {install.APP_VERSION} ({where}); data in {settings.resolved_data_dir}",
    )


def _why_headless() -> str:
    env = os.environ
    if env.get("FOUNDRYOS_NO_BROWSER", "").strip() not in ("", "0"):
        return "FOUNDRYOS_NO_BROWSER is set"
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return "this is an SSH session"
    if install.current_os() == "linux" and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return "no display (DISPLAY / WAYLAND_DISPLAY unset)"
    return "this looks like a container"


def check_headless() -> Check:
    if headless.is_headless():
        return Check(
            "headless",
            INFO,
            f"yes ({_why_headless()}): the browser is not opened; addresses are printed instead",
            "Reach it from your computer with: ssh -N -L 8700:127.0.0.1:8700 user@host "
            "(set FOUNDRYOS_OPEN_BROWSER=1 to force opening a browser here).",
        )
    return Check("headless", INFO, "no: there is a screen, so a browser can be opened")


def check_linger() -> Check | None:
    """Linux only: without linger, systemd stops this user's services at the last logout."""
    if install.current_os() != "linux":
        return None
    state = services.linger_state()
    if state is None:
        return Check("linger", INFO, "unknown (loginctl is not available or did not answer)")
    if state:
        return Check("linger", OK, "on: the background service keeps running after you log out")
    if headless.is_headless():
        return Check(
            "linger",
            WARN,
            "off: the background service stops when you log out of this session",
            "foundryos service install --linger   (or: sudo loginctl enable-linger $USER)",
        )
    return Check("linger", INFO, "off: user services stop at your last logout")


def check_service_survives_logout() -> Check | None:
    native = services.native_manager()
    if native is None or not native.installed():
        return None
    remote = headless.is_headless()
    if isinstance(native, services.SystemdService):
        lingering = services.linger_state()
        if lingering:
            return Check("service lifetime", OK, "survives logout (linger is on)")
        return Check(
            "service lifetime",
            WARN if remote else INFO,
            "stops at logout" if lingering is False else "unknown (linger state unavailable)",
            "foundryos service install --linger   (or: sudo loginctl enable-linger $USER)",
        )
    if isinstance(native, services.LaunchdService):
        domain = native.recorded_domain()
        if domain == native.user_domain:
            return Check(
                "service lifetime",
                WARN if remote else INFO,
                f"loaded in {domain} (no graphical login): runs now, but does not start after a "
                "reboot until someone logs in",
                "Turn on automatic login, or use a LaunchDaemon: `foundryos service install` "
                "prints the exact sudo commands.",
            )
        return Check(
            "service lifetime",
            WARN if remote else INFO,
            f"loaded in {domain or native.gui_domain}: runs while you are logged in to the Mac; starts "
            "at boot only with automatic login",
            "For a Mac that is only reached over SSH, use a LaunchDaemon: "
            "`foundryos service install` prints the exact sudo commands.",
        )
    return None


def check_updates(settings: Settings) -> Check:
    """What the last (cached, at most daily) look at GitHub found. Never touches the network."""
    from app.ops import updatecheck

    info = updatecheck.status(settings, refresh="never")
    if not info["enabled"]:
        return Check("updates", INFO, "the daily update check is switched off")
    if info["update_available"]:
        return Check(
            "updates",
            WARN,
            f"{info['latest_version']} is available (you have {info['current_version']})",
            f"To update: {info['update_command']}",
        )
    if info["latest_version"]:
        return Check("updates", OK, f"up to date ({info['current_version']})")
    if info["checked_at"] is None:
        return Check("updates", INFO, "not checked yet (`foundryos update --check` looks now)")
    return Check("updates", INFO, "no newer release is known")


def check_running(settings: Settings) -> Check:
    live = pidfile.live_processes(settings.resolved_data_dir)
    if live:
        return Check("processes", INFO, ", ".join(f"{p['role']} pid {p['pid']}" for p in live))
    port = settings.port
    with socket.socket() as s:
        s.settimeout(0.3)
        busy = s.connect_ex(("127.0.0.1", port)) == 0
    return Check(
        "processes",
        INFO,
        f"none registered for this data dir; port {port} is {'in use' if busy else 'free'}",
    )


def run_checks(settings: Settings | None = None) -> list[Check]:
    settings = settings or Settings()
    steps = [
        check_python,
        lambda: check_node(settings),
        lambda: check_database(settings),
        lambda: check_runtime(settings),
        check_ollama,
        check_docker,
        lambda: check_tunnel_tools(settings),
        lambda: check_disk(settings),
        lambda: check_permissions(settings),
        lambda: check_auth_gate(settings),
        lambda: check_running(settings),
        lambda: check_install(settings),
        check_headless,
        check_linger,
        check_service_survives_logout,
        lambda: check_updates(settings),
    ]
    out: list[Check] = []
    for step in steps:
        try:
            result = step()
            if result is not None:  # a check that does not apply to this system returns None
                out.append(result)
        except Exception as exc:  # a broken check must not hide the others
            out.append(
                Check(
                    getattr(step, "__name__", "check"),
                    WARN,
                    f"check crashed: {exc.__class__.__name__}: {exc}",
                )
            )
    return out
