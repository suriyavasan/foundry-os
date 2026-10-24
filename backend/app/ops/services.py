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
from app.ops import logs

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

    def as_dict(self) -> dict:
        return {
            "manager": self.manager,
            "installed": self.installed,
            "loaded": self.loaded,
            "running": self.running,
            "path": str(self.path) if self.path else None,
            "pid": self.pid,
            "detail": self.detail,
        }


def run_tool(args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    exe = shutil.which(args[0])
    if exe is None:
        raise ServiceError(f"`{args[0]}` was not found on your PATH.")
    try:
        return subprocess.run(
            [exe, *args[1:]], capture_output=True, text=True, timeout=timeout, check=False
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


# ------------------------------------------------------------------------------ rendering


def render_plist(spec: ServiceSpec) -> bytes:
    body: dict = {
        "Label": install.LAUNCHD_LABEL,
        "ProgramArguments": list(spec.command),
        "EnvironmentVariables": dict(spec.env),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(spec.log_file),
        "StandardErrorPath": str(spec.log_file),
    }
    if spec.working_dir is not None:
        body["WorkingDirectory"] = str(spec.working_dir)
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
    name = "launchd"
    label = install.LAUNCHD_LABEL

    def path(self) -> Path:
        return install.home_dir() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.label}"

    def status(self) -> ServiceStatus:
        path = self.path()
        if not path.is_file():
            return ServiceStatus(self.name, False, detail="not installed")
        proc = run_tool(["launchctl", "print", self.target])
        loaded = proc.returncode == 0
        pid = None
        running = False
        if loaded:
            for raw in proc.stdout.splitlines():
                line = raw.strip()
                if line.startswith("state =") and "running" in line:
                    running = True
                if line.startswith("pid ="):
                    with contextlib.suppress(ValueError):
                        pid = int(line.split("=", 1)[1].strip())
        detail = (
            "running" if running else "loaded, not running" if loaded else "installed, not loaded"
        )
        return ServiceStatus(self.name, True, loaded, running, path, pid, detail)

    def _bootstrap(self) -> bool:
        return run_tool(["launchctl", "bootstrap", self.domain, str(self.path())]).returncode == 0

    def _bootout(self) -> None:
        run_tool(["launchctl", "bootout", self.target])  # "not loaded" is not an error here

    def install(self, spec: ServiceSpec, *, load: bool = True, linger: bool = False) -> list[str]:
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        was_loaded = self.installed() and self.status().loaded
        _atomic_write(self.path(), render_plist(spec))
        notes = [f"Wrote {self.path()}"]
        if was_loaded:
            self._bootout()
        if load:
            if self._bootstrap():
                notes.append(
                    "Loaded it: Foundry AI OS now starts at login and restarts if it stops."
                )
            else:
                notes.append(
                    "It is installed but could not be loaded right now (no graphical login session?). "
                    "It will start at your next login."
                )
        else:
            notes.append("Not loaded yet; it will start at your next login.")
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
        return notes

    def start(self) -> None:
        if not self.installed():
            raise ServiceError(
                "The background service is not installed (foundryos service install)."
            )
        if self.status().loaded:
            run_tool(["launchctl", "kickstart", self.target])
        elif not self._bootstrap():
            raise ServiceError(
                "launchctl could not load the service. See `foundryos service status`."
            )

    def stop(self) -> None:
        self._bootout()  # unloading is what really stops a KeepAlive job

    def restart(self) -> None:
        if self.status().loaded:
            run_tool(["launchctl", "kickstart", "-k", self.target])
        else:
            self.start()


class SystemdService(ServiceManager):
    name = "systemd"
    label = install.SYSTEMD_UNIT

    def path(self) -> Path:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(xdg) if xdg and os.path.isabs(xdg) else install.home_dir() / ".config"
        return base / "systemd" / "user" / self.label

    def _ctl(self, *args: str) -> subprocess.CompletedProcess:
        return run_tool(["systemctl", "--user", *args])

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
                "without a user bus?). Use `foundryos start` instead, or run it under your own supervisor."
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
            user = getpass.getuser()
            lingered = run_tool(["loginctl", "enable-linger", user])
            if lingered.returncode == 0:
                notes.append(f"Linger is on for {user}: it also runs while you are logged out.")
            else:
                notes.append(f"Could not enable linger: {lingered.stderr.strip()}")
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
