"""The background service: a macOS LaunchAgent or a Linux ``systemd --user`` unit.

``foundryos service install`` writes ``~/Library/LaunchAgents/ai.foundry-os.plist`` (RunAtLoad,
KeepAlive) or ``~/.config/systemd/user/foundry-os.service`` and loads it. The service runs
``foundryos serve`` through the *stable* launcher path (``~/.foundry-os/current/bin/foundryos``), so an
update only needs a restart. Homebrew installs have their own service (``brew services``); this module
never creates a second one next to it.

Every call to ``launchctl`` / ``systemctl`` / ``loginctl`` / ``brew`` goes through :func:`run_tool`,
which finds the program on ``PATH`` (tests put fakes first).
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.core import install
from app.core.config import Settings
from app.ops import headless, logs

RUNTIME_ROOT = Path("/run/user")  # per-user runtime dirs on Linux (a constant so tests can move it)
BUS_FAILURE_MARKERS = ("failed to connect to bus", "dbus_session_bus_address", "xdg_runtime_dir")
PATH_EXTRAS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
PASS_THROUGH_ENV = ("FOUNDRYOS_DATA_DIR", "FOUNDRYOS_PORT", "FOUNDRYOS_UPDATE_CHECK")


class ServiceError(Exception):
    pass


@dataclass
class ServiceSpec:
    command: list[str]  # e.g. [".../foundryos", "serve"]
    log_file: Path
    env: dict[str, str] = field(default_factory=dict)
    working_dir: Path | None = None


@dataclass
class ServiceStatus:
    manager: str  # launchd | systemd | brew | none
    installed: bool
    loaded: bool = False  # loaded into launchd / enabled in systemd
    running: bool = False
    path: Path | None = None
    pid: int | None = None
    detail: str = ""
    domain: str = ""  # launchd only: the domain it is loaded in (gui/<uid> or user/<uid>)

    def as_dict(self) -> dict:
        return {
            "manager": self.manager,
            "installed": self.installed,
            "loaded": self.loaded,
            "running": self.running,
            "path": str(self.path) if self.path else None,
            "pid": self.pid,
            "detail": self.detail,
            "domain": self.domain or None,
        }


def run_tool(
    args: list[str], *, timeout: float = 60.0, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    exe = shutil.which(args[0])
    if exe is None:
        raise ServiceError(f"`{args[0]}` was not found on your PATH.")
    try:
        return subprocess.run(
            [exe, *args[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=None if env is None else {**os.environ, **env},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(f"`{' '.join(args)}` failed: {exc}") from exc


def service_path() -> str:
    """PATH for the service: the caller's, plus the usual places (launchd's default is minimal)."""
    entries: list[str] = []
    for part in [
        *os.environ.get("PATH", "").split(os.pathsep),
        str(install.home_dir() / ".local" / "bin"),
        *PATH_EXTRAS,
    ]:
        if part and part not in entries and os.path.isabs(part):
            entries.append(part)
    return os.pathsep.join(entries)


def build_spec(settings: Settings) -> ServiceSpec:
    env = {"PATH": service_path(), "FOUNDRYOS_RUN_MODE": "service"}
    for name in PASS_THROUGH_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            env[name] = value
    command = [*install.launcher_command(), "serve"]
    source = install.install_kind() == install.SOURCE
    return ServiceSpec(
        command=command,
        log_file=logs.log_path(settings),
        env=env,
        working_dir=install.BACKEND_DIR if source else None,
    )


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


# ------------------------------------------------------------------------------ linger (Linux)


def _login_user(user: str | None) -> str:
    return user or getpass.getuser()


def linger_state(user: str | None = None) -> bool | None:
    """Does systemd keep ``user``'s services running after the last logout? None = unknown (not Linux,
    no ``loginctl``, or it failed)."""
    if install.current_os() != "linux":
        return None
    try:
        proc = run_tool(["loginctl", "show-user", _login_user(user), "-p", "Linger"], timeout=10)
    except ServiceError:
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        key, _, value = line.strip().partition("=")
        if key == "Linger":
            return {"yes": True, "no": False}.get(value.strip().lower())
    return None


def enable_linger(user: str | None = None) -> tuple[bool, str]:
    """Run ``loginctl enable-linger`` for ``user`` (never with sudo). Returns (done, message); on
    failure the message says what to run with sudo instead."""
    name = _login_user(user)
    hint = f"Run this yourself with administrator rights:  sudo loginctl enable-linger {name}"
    try:
        proc = run_tool(["loginctl", "enable-linger", name], timeout=30)
    except ServiceError as exc:
        return False, f"Could not enable linger ({exc}). {hint}"
    if proc.returncode == 0:
        return True, f"Linger is on for {name}: it also runs while you are logged out."
    why = (proc.stderr or proc.stdout).strip().splitlines()
    return False, f"Could not enable linger{': ' + why[-1] if why else ''}. {hint}"


# ------------------------------------------------------------------------------ rendering


def render_plist(spec: ServiceSpec, *, user_name: str | None = None) -> bytes:
    """The launchd job. With ``user_name`` it is the system-wide LaunchDaemon variant (runs at boot as
    that user; ``HOME`` is set because launchd does not do it for daemons)."""
    env = dict(spec.env)
    if user_name:
        env.setdefault("HOME", str(install.home_dir()))
    body: dict = {
        "Label": install.LAUNCHD_LABEL,
        "ProgramArguments": list(spec.command),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(spec.log_file),
        "StandardErrorPath": str(spec.log_file),
    }
    if spec.working_dir is not None:
        body["WorkingDirectory"] = str(spec.working_dir)
    if user_name:
        body["UserName"] = user_name
    return plistlib.dumps(body, sort_keys=False)


def _systemd_quote(value: str) -> str:
    if value and not any(c in value for c in " \t\"'\\%$;"):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def render_unit(spec: ServiceSpec) -> str:
    lines = [
        "# Foundry AI OS (written by `foundryos service install`; remove it with `foundryos service uninstall`)",
        "[Unit]",
        "Description=Foundry AI OS",
        "Documentation=https://github.com/suriyavasan/foundry-os",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + " ".join(_systemd_quote(a) for a in spec.command),
    ]
    if spec.working_dir is not None:
        lines.append(f"WorkingDirectory={_systemd_quote(str(spec.working_dir))}")
    for key, value in spec.env.items():
        lines.append("Environment=" + _systemd_quote(f"{key}={value}"))
    lines += [
        "Restart=on-failure",
        "RestartSec=5",
        # `serve` stops its worker, then the API, giving each up to 20 s
        "TimeoutStopSec=60",
        f"StandardOutput=append:{spec.log_file}",
        f"StandardError=append:{spec.log_file}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ managers


class ServiceManager:
    name = "none"
    label = ""

    def path(self) -> Path:  # pragma: no cover - overridden
        raise NotImplementedError

    def installed(self) -> bool:
        return self.path().is_file()

    def status(self) -> ServiceStatus:  # pragma: no cover - overridden
        raise NotImplementedError

    def install(self, spec: ServiceSpec, *, load: bool = True, linger: bool = False) -> list[str]:
        raise ServiceError("This service manager cannot be installed by Foundry AI OS.")

    def uninstall(self) -> list[str]:
        raise ServiceError("This service manager cannot be removed by Foundry AI OS.")

    def start(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def restart(self) -> None:
        self.stop()
        self.start()


class LaunchdService(ServiceManager):
    """A LaunchAgent. It is bootstrapped into ``gui/<uid>`` (needs a graphical login); when that is
    refused (a Mac reached only over SSH) into ``user/<uid>``. The domain that worked is recorded in
    ``ai.foundry-os.domain`` next to the plist, and status / stop / uninstall look in both."""

    name = "launchd"
    label = install.LAUNCHD_LABEL

    def path(self) -> Path:
        return install.home_dir() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def domain_file(self) -> Path:
        return self.path().with_suffix(".domain")

    @property
    def gui_domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def user_domain(self) -> str:
        return f"user/{os.getuid()}"

    def recorded_domain(self) -> str | None:
        try:
            value = self.domain_file().read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value if value in (self.gui_domain, self.user_domain) else None

    def candidate_domains(self) -> list[str]:
        out: list[str] = []
        for d in (self.recorded_domain(), self.gui_domain, self.user_domain):
            if d and d not in out:
                out.append(d)
        return out

    @property
    def domain(self) -> str:
        return self.recorded_domain() or self.gui_domain

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.label}"

    def _print(self, domain: str) -> subprocess.CompletedProcess | None:
        proc = run_tool(["launchctl", "print", f"{domain}/{self.label}"])
        return proc if proc.returncode == 0 else None

    def _loaded(self) -> tuple[str, subprocess.CompletedProcess] | None:
        for domain in self.candidate_domains():
            proc = self._print(domain)
            if proc is not None:
                return domain, proc
        return None

    def status(self) -> ServiceStatus:
        path = self.path()
        if not path.is_file():
            return ServiceStatus(self.name, False, detail="not installed")
        found = self._loaded()
        loaded = found is not None
        pid = None
        running = False
        domain = found[0] if found else ""
        if found:
            for raw in found[1].stdout.splitlines():
                line = raw.strip()
                if line.startswith("state =") and "running" in line:
                    running = True
                if line.startswith("pid ="):
                    with contextlib.suppress(ValueError):
                        pid = int(line.split("=", 1)[1].strip())
        detail = (
            "running" if running else "loaded, not running" if loaded else "installed, not loaded"
        )
        if loaded and domain != self.gui_domain:
            detail += f" (in {domain}: no graphical login needed, but it does not start at boot)"
        return ServiceStatus(self.name, True, loaded, running, path, pid, detail, domain)

    def _bootstrap(self) -> str | None:
        """Load the agent: ``gui/<uid>`` first, ``user/<uid>`` when that is refused. Returns the domain
        it was loaded into (and records it), or None."""
        for domain in (self.gui_domain, self.user_domain):
            if run_tool(["launchctl", "bootstrap", domain, str(self.path())]).returncode == 0:
                with contextlib.suppress(OSError):
                    self.domain_file().write_text(domain + "\n", encoding="utf-8")
                return domain
        return None

    def _bootout(self) -> None:
        """Unload it from every domain it is loaded in ("not loaded" is not an error)."""
        loaded = [d for d in self.candidate_domains() if self._print(d) is not None]
        for domain in loaded:
            run_tool(["launchctl", "bootout", f"{domain}/{self.label}"])

    def boot_notes(self, spec: ServiceSpec) -> list[str]:
        """Starting at boot, with nobody logged in, needs auto-login or a LaunchDaemon (root-owned)."""
        user = getpass.getuser()
        daemon_src = spec.log_file.parent.parent / f"{self.label}.daemon.plist"
        dest = f"/Library/LaunchDaemons/{self.label}.plist"
        notes = [
            "Starting at boot with nobody logged in needs automatic login (System Settings > Users & "
            "Groups) or a LaunchDaemon."
        ]
        try:
            _atomic_write(daemon_src, render_plist(spec, user_name=user))
        except OSError:
            return notes
        notes += [
            f"To use a LaunchDaemon that runs as {user}, run these yourself (they need administrator "
            "rights; Foundry AI OS never runs sudo):",
            "  foundryos service uninstall      # first remove the per-user agent so two copies do not "
            "fight over the port",
            f"  sudo cp '{daemon_src}' {dest}",
            f"  sudo chown root:wheel {dest}",
            f"  sudo chmod 644 {dest}",
            f"  sudo launchctl bootstrap system {dest}",
        ]
        return notes

    def install(self, spec: ServiceSpec, *, load: bool = True, linger: bool = False) -> list[str]:
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        was_loaded = self.installed() and self.status().loaded
        _atomic_write(self.path(), render_plist(spec))
        notes = [f"Wrote {self.path()}"]
        if was_loaded:
            self._bootout()
        with contextlib.suppress(OSError):
            self.domain_file().unlink()
        if load:
            domain = self._bootstrap()
            if domain == self.gui_domain:
                notes.append(
                    "Loaded it: Foundry AI OS now starts at login and restarts if it stops."
                )
            elif domain is not None:
                notes.append(
                    f"There is no graphical login on this Mac, so it was loaded into {domain} instead: "
                    "it runs now and restarts if it stops, but it does not start by itself after a reboot."
                )
                notes += self.boot_notes(spec)
            else:
                notes.append(
                    "It is installed but launchctl would not load it (neither gui/ nor user/ domain). "
                    "It starts at your next graphical login; on a Mac you only reach over SSH that may "
                    "be never. Use `foundryos start` to run it now."
                )
                notes += self.boot_notes(spec)
        else:
            notes.append("Not loaded yet; it will start at your next graphical login.")
        return notes

    def uninstall(self) -> list[str]:
        notes = []
        if self.path().is_file():
            with contextlib.suppress(ServiceError):
                self._bootout()
            self.path().unlink()
            notes.append(f"Removed {self.path()}")
        else:
            notes.append("The background service was not installed.")
        with contextlib.suppress(OSError):
            self.domain_file().unlink()
        return notes

    def start(self) -> None:
        if not self.installed():
            raise ServiceError(
                "The background service is not installed (foundryos service install)."
            )
        found = self._loaded()
        if found:
            run_tool(["launchctl", "kickstart", f"{found[0]}/{self.label}"])
        elif self._bootstrap() is None:
            raise ServiceError(
                "launchctl could not load the service (neither gui/ nor user/ domain). "
                "See `foundryos service status`."
            )

    def stop(self) -> None:
        self._bootout()  # unloading is what really stops a KeepAlive job

    def restart(self) -> None:
        found = self._loaded()
        if found:
            run_tool(["launchctl", "kickstart", "-k", f"{found[0]}/{self.label}"])
        else:
            self.start()


def _bus_missing(proc: subprocess.CompletedProcess) -> bool:
    text = (proc.stderr or "").lower()
    return any(marker in text for marker in BUS_FAILURE_MARKERS)


def user_bus_env() -> dict[str, str] | None:
    """The environment that reaches this user's systemd bus, when /run/user/<uid> exists and the current
    environment does not already point there."""
    runtime = RUNTIME_ROOT / str(os.getuid())
    if not runtime.is_dir():
        return None
    env = {
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }
    if all(os.environ.get(k) == v for k, v in env.items()):
        return None
    return env


class SystemdService(ServiceManager):
    name = "systemd"
    label = install.SYSTEMD_UNIT

    def path(self) -> Path:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(xdg) if xdg and os.path.isabs(xdg) else install.home_dir() / ".config"
        return base / "systemd" / "user" / self.label

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        """``systemctl --user``. In a shell with no user bus (su, sudo, some SSH setups) it fails with
        "Failed to connect to bus"; when /run/user/<uid> exists, retry pointing at that bus."""
        cmd = ["systemctl", "--user", *args]
        proc = run_tool(cmd)
        if proc.returncode != 0 and _bus_missing(proc):
            bus_env = user_bus_env()
            if bus_env is not None:
                return run_tool(cmd, env=bus_env)
        return proc

    def available(self) -> bool:
        try:
            return self._ctl("show-environment").returncode == 0
        except ServiceError:
            return False

    def status(self) -> ServiceStatus:
        path = self.path()
        if not path.is_file():
            return ServiceStatus(self.name, False, detail="not installed")
        active = self._ctl("is-active", self.label).stdout.strip() == "active"
        enabled = self._ctl("is-enabled", self.label).stdout.strip() == "enabled"
        pid = None
        if active:
            out = self._ctl("show", "-p", "MainPID", "--value", self.label).stdout.strip()
            with contextlib.suppress(ValueError):
                pid = int(out) or None
        detail = ("running" if active else "not running") + (", starts at login" if enabled else "")
        return ServiceStatus(self.name, True, enabled, active, path, pid, detail)

    def install(self, spec: ServiceSpec, *, load: bool = True, linger: bool = False) -> list[str]:
        if not self.available():
            raise ServiceError(
                "systemd's user session is not available here (is this a container or an SSH session "
                "without a user bus?). Log in directly as this user rather than through su/sudo, or "
                "export XDG_RUNTIME_DIR=/run/user/$(id -u) first. Or use `foundryos start`, or run "
                "it under your own supervisor."
            )
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path(), render_unit(spec).encode("utf-8"))
        notes = [f"Wrote {self.path()}"]
        self._ctl("daemon-reload")
        proc = self._ctl("enable", "--now", self.label) if load else self._ctl("enable", self.label)
        if proc.returncode != 0:
            raise ServiceError(
                f"systemctl could not enable the service: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        notes.append("Enabled it: Foundry AI OS starts when you log in and restarts if it fails.")
        if linger:
            notes.append(enable_linger()[1])
        elif headless.is_headless():
            if linger_state() is True:
                notes.append("Linger is already on: it keeps running after you log out.")
            else:
                notes.append(
                    "Warning: this looks like a remote or headless session. Without linger the service "
                    "stops when you log out of it and does not start at boot. Keep it running with "
                    "`foundryos service install --linger` (or `sudo loginctl enable-linger $USER`)."
                )
        else:
            notes.append(
                "It runs while you are logged in. To keep it running when you log out, run "
                "`foundryos service install --linger` (needs permission to use loginctl)."
            )
        return notes

    def uninstall(self) -> list[str]:
        if not self.path().is_file():
            return ["The background service was not installed."]
        with contextlib.suppress(ServiceError):
            self._ctl("disable", "--now", self.label)
        self.path().unlink()
        with contextlib.suppress(ServiceError):
            self._ctl("daemon-reload")
        return [f"Removed {self.path()}"]

    def start(self) -> None:
        proc = self._ctl("start", self.label)
        if proc.returncode != 0:
            raise ServiceError(f"systemctl could not start the service: {proc.stderr.strip()}")

    def stop(self) -> None:
        self._ctl("stop", self.label)

    def restart(self) -> None:
        proc = self._ctl("restart", self.label)
        if proc.returncode != 0:
            raise ServiceError(f"systemctl could not restart the service: {proc.stderr.strip()}")


class BrewService(ServiceManager):
    """The service Homebrew creates (``brew services start foundry-os``). We only drive it."""

    name = "brew"
    label = "homebrew.mxcl.foundry-os"

    def path(self) -> Path:
        if install.current_os() == "darwin":
            return install.home_dir() / "Library" / "LaunchAgents" / f"{self.label}.plist"
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(xdg) if xdg and os.path.isabs(xdg) else install.home_dir() / ".config"
        return base / "systemd" / "user" / "homebrew.foundry-os.service"

    def status(self) -> ServiceStatus:
        path = self.path()
        if not path.is_file():
            return ServiceStatus(self.name, False, detail="not installed")
        running = False
        try:
            proc = run_tool(
                ["brew", "services", "info", install.BREW_FORMULA, "--json"], timeout=30
            )
            if proc.returncode == 0:
                info = json.loads(proc.stdout or "[]")
                running = bool(info and info[0].get("running"))
        except (ServiceError, ValueError, AttributeError, IndexError):
            pass
        detail = "running" if running else "not running"
        return ServiceStatus(
            self.name, True, True, running, path, None, f"{detail} (managed by Homebrew)"
        )

    def _brew(self, verb: str) -> None:
        proc = run_tool(["brew", "services", verb, install.BREW_FORMULA])
        if proc.returncode != 0:
            raise ServiceError(
                f"`brew services {verb} {install.BREW_FORMULA}` failed: "
                f"{(proc.stderr or proc.stdout).strip()}"
            )

    def start(self) -> None:
        self._brew("start")

    def stop(self) -> None:
        self._brew("stop")

    def restart(self) -> None:
        self._brew("restart")


def native_manager() -> ServiceManager | None:
    system = install.current_os()
    if system == "darwin":
        return LaunchdService()
    if system == "linux":
        return SystemdService()
    return None


def brew_manager() -> BrewService:
    return BrewService()


def active_service() -> ServiceManager | None:
    """The background service that is installed on this machine for Foundry AI OS (if any)."""
    brew = brew_manager()
    if brew.installed():
        return brew
    native = native_manager()
    if native is not None and native.installed():
        return native
    return None


BREW_MESSAGE = (
    "Homebrew manages the background service for this install, so Foundry AI OS does not create a "
    "second one.\n"
    "  Start it now and at every login:  brew services start foundry-os\n"
    "  Stop it and remove the autostart: brew services stop foundry-os"
)
