"""Headless boxes (SSH, containers, servers): no browser is ever opened, the user gets the local address,
how to forward the port over SSH and the public URL; setup can be finished in the terminal."""

from __future__ import annotations

import os
import platform
import select
import subprocess
import sys
import time
import types
import webbrowser
from pathlib import Path

import pytest

from app.core import install
from app.core.config import Settings
from app.ops import headless, lifecycle
from tests.m11_helpers import (  # noqa: F401
    NoBrowser,
    calls,
    env,
    fake_tool,
    invoke,
    make_release,
    write_public_url,
)

REPO = Path(__file__).resolve().parents[2]
HEALTH = {"status": "ok", "database": "ok", "version": "0.1.0", "auth_mode": "password"}


# ------------------------------------------------------------------ is_headless truth table


@pytest.fixture
def no_dockerenv(tmp_path, monkeypatch):
    monkeypatch.setattr(headless, "DOCKERENV", tmp_path / "no-such-.dockerenv")


@pytest.mark.parametrize(
    ("environ", "system", "expected"),
    [
        ({}, "darwin", False),
        ({"DISPLAY": ":0"}, "linux", False),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", False),
        ({}, "linux", True),  # a Linux box with no display
        ({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, "darwin", True),
        ({"SSH_TTY": "/dev/pts/3"}, "darwin", True),
        (
            {"SSH_CONNECTION": "1 2 3 4", "DISPLAY": ":0"},
            "linux",
            True,
        ),  # X forwarding is not a screen
        ({"FOUNDRYOS_NO_BROWSER": "1"}, "darwin", True),
        ({"FOUNDRYOS_NO_BROWSER": "0"}, "darwin", False),
        ({"FOUNDRYOS_NO_BROWSER": ""}, "darwin", False),
        # FOUNDRYOS_OPEN_BROWSER=1 beats everything else
        ({"FOUNDRYOS_OPEN_BROWSER": "1", "SSH_CONNECTION": "1 2 3 4"}, "linux", False),
        ({"FOUNDRYOS_OPEN_BROWSER": "1", "FOUNDRYOS_NO_BROWSER": "1"}, "darwin", False),
        ({"FOUNDRYOS_OPEN_BROWSER": "0", "SSH_TTY": "/dev/pts/1"}, "darwin", True),
    ],
)
def test_is_headless_truth_table(no_dockerenv, environ, system, expected):
    assert headless.is_headless(environ, system) is expected


def test_dockerenv_means_headless_unless_overridden(tmp_path, monkeypatch):
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr(headless, "DOCKERENV", marker)
    assert headless.is_headless({"DISPLAY": ":0"}, "linux") is True
    assert headless.is_headless({}, "darwin") is True
    assert headless.is_headless({"FOUNDRYOS_OPEN_BROWSER": "1"}, "linux") is False


def test_is_headless_reads_the_process_environment_by_default(no_dockerenv, monkeypatch):
    for name in ("SSH_CONNECTION", "SSH_TTY", "FOUNDRYOS_NO_BROWSER", "FOUNDRYOS_OPEN_BROWSER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    assert headless.is_headless() is False
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    assert headless.is_headless() is True


def test_remote_hints_name_the_forward_and_passkeys():
    text = "\n".join(headless.remote_hints(8712))
    assert "ssh -N -L 8712:127.0.0.1:8712 user@host" in text
    assert "http://localhost:8712" in text and "asskeys" in text


def test_open_browser_never_calls_the_opener_when_headless(no_dockerenv):
    op = NoBrowser()
    assert headless.open_browser("http://x", op, {"SSH_TTY": "/dev/pts/1"}) is False
    assert op.urls == []
    assert headless.open_browser("http://x", op, {"FOUNDRYOS_OPEN_BROWSER": "1"}) is True
    assert op.urls == ["http://x"]


# ------------------------------------------------------------------ the public URL file


def test_read_public_url_reads_the_data_dir_file(env):  # noqa: F811
    settings = Settings(_env_file=None)
    assert lifecycle.read_public_url(settings) is None
    write_public_url(env.data, "https://a.example", previous_url="https://old.example")
    info = lifecycle.read_public_url(settings)
    assert info and info["url"] == "https://a.example" and info["provider"] == "cloudflare-quick"
    assert info["previous_url"] == "https://old.example" and info["state"] == "up"
    assert "run" not in str(env.data / "public-url.json")  # next to the data, not under run/


@pytest.mark.parametrize("text", ["{not json", "[]", '{"url": 5}', '{"url": "ftp://x"}', "{}"])
def test_read_public_url_ignores_invalid_files(env, text):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "public-url.json").write_text(text)
    assert lifecycle.read_public_url(Settings(_env_file=None)) is None


# ------------------------------------------------------------------ foundryos / start / open


class FakeProc:
    pid = 1234

    def poll(self):
        return None


@pytest.fixture
def world(env, monkeypatch, no_dockerenv):  # noqa: F811
    """Not running at first; spawning makes the fake server healthy. A webbrowser that records calls."""
    state = {"up": False, "spawned": 0, "pending": True}
    monkeypatch.setattr(
        lifecycle, "probe_health", lambda port, **k: HEALTH if state["up"] else None
    )
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(lifecycle, "setup_pending", lambda port, **k: state["pending"])

    def spawn(cmd, *, env, cwd, log_file):
        state["spawned"] += 1
        state["up"] = True
        return FakeProc()

    monkeypatch.setattr(lifecycle, "spawn_detached", spawn)
    browser = NoBrowser()
    monkeypatch.setattr(webbrowser, "open", browser)
    state["browser"] = browser
    monkeypatch.delenv("FOUNDRYOS_NO_BROWSER", raising=False)
    monkeypatch.setattr(headless, "stdin_is_tty", lambda: False)
    return state


def go_headless(monkeypatch, how: str) -> None:
    if how == "ssh":
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.9 22")
    elif how == "ssh_tty":
        monkeypatch.setenv("SSH_TTY", "/dev/pts/2")
    elif how == "linux_no_display":
        monkeypatch.setattr(install, "current_os", lambda: "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    else:  # explicit
        monkeypatch.setenv("FOUNDRYOS_NO_BROWSER", "1")


@pytest.mark.parametrize("how", ["ssh", "ssh_tty", "linux_no_display", "explicit"])
def test_no_arguments_on_a_headless_box_never_opens_a_browser(world, env, monkeypatch, how):
    go_headless(monkeypatch, how)
    write_public_url(env.data, "https://demo.trycloudflare.com")
    port = os.environ["FOUNDRYOS_PORT"]
    r = invoke()
    assert r.exit_code == 0, r.output
    assert world["browser"].urls == []  # never
    assert f"Local URL:  http://127.0.0.1:{port}" in r.stdout
    assert f"ssh -N -L {port}:127.0.0.1:{port} user@host" in r.stdout
    assert f"http://localhost:{port}" in r.stdout
    assert "Public URL: https://demo.trycloudflare.com" in r.stdout
    assert "Opened it in your browser" not in r.stdout and "in the browser" not in r.stdout
    assert "Finish setup: foundryos setup" in r.stdout  # setup pending, no terminal to ask in


def test_a_headless_box_without_a_public_url_just_omits_it(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    r = invoke()
    assert "Public URL" not in r.stdout and "Local URL" in r.stdout


def test_setup_that_is_not_pending_prints_no_wizard_hint(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    world["pending"] = False
    r = invoke()
    assert "Finish setup" not in r.stdout and "Set it up here" not in r.stdout


def install_wizard(monkeypatch, result=True):
    seen: list[object] = []
    mod = types.ModuleType("app.cli.commands.onboard")

    def offer_wizard(st):
        seen.append(st)
        return result

    mod.offer_wizard = offer_wizard  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.cli.commands.onboard", mod)
    return seen


def test_pending_setup_with_a_terminal_offers_the_wizard(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    monkeypatch.setattr(headless, "stdin_is_tty", lambda: True)
    seen = install_wizard(monkeypatch)
    r = invoke(input="\n")  # the default answer is yes
    assert r.exit_code == 0, r.output
    assert "Set it up here in the terminal now? [Y/n]" in r.stdout
    assert len(seen) == 1 and "Finish setup" not in r.stdout
    assert world["browser"].urls == []


def test_declining_the_wizard_prints_the_hint(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    monkeypatch.setattr(headless, "stdin_is_tty", lambda: True)
    seen = install_wizard(monkeypatch)
    r = invoke(input="n\n")
    assert seen == [] and "Finish setup: foundryos setup" in r.stdout


def test_without_a_terminal_the_wizard_is_never_asked_for(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    seen = install_wizard(monkeypatch)
    r = invoke()
    assert seen == [] and "Set it up here" not in r.stdout
    assert "Finish setup: foundryos setup" in r.stdout


def test_a_missing_wizard_falls_back_to_the_hint(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    monkeypatch.setattr(headless, "stdin_is_tty", lambda: True)
    monkeypatch.setitem(sys.modules, "app.cli.commands.onboard", None)  # import raises ImportError
    r = invoke(input="y\n")
    assert r.exit_code == 0 and "Finish setup: foundryos setup" in r.stdout


def test_a_wizard_that_declines_to_run_leaves_the_hint(world, monkeypatch):
    go_headless(monkeypatch, "ssh")
    monkeypatch.setattr(headless, "stdin_is_tty", lambda: True)
    install_wizard(monkeypatch, result=False)
    r = invoke(input="y\n")
    assert "Finish setup: foundryos setup" in r.stdout


def test_a_desktop_keeps_opening_the_browser_with_the_new_wording(world, monkeypatch):
    monkeypatch.setenv("FOUNDRYOS_OPEN_BROWSER", "1")
    r = invoke()
    assert r.exit_code == 0, r.output
    assert world["browser"].urls == [f"http://127.0.0.1:{os.environ['FOUNDRYOS_PORT']}"]
    assert "create your account in the browser or run `foundryos setup`" in r.stdout
    assert "Local URL" not in r.stdout and "ssh -N" not in r.stdout


def test_start_on_a_headless_box_prints_the_address_and_hints(world, env, monkeypatch):
    go_headless(monkeypatch, "ssh")
    write_public_url(env.data)
    r = invoke("start")
    assert r.exit_code == 0, r.output
    assert world["browser"].urls == [] and "ssh -N -L" in r.stdout
    assert "Public URL: https://demo.trycloudflare.com" in r.stdout
    assert "Finish setup: foundryos setup" in r.stdout


def test_open_on_a_headless_box_prints_instead_of_opening(world, env, monkeypatch):
    world["up"] = True
    go_headless(monkeypatch, "ssh")
    write_public_url(env.data)
    r = invoke("open")
    assert r.exit_code == 0, r.output
    assert world["browser"].urls == []
    assert "Local URL" in r.stdout and "ssh -N -L" in r.stdout and "Public URL" in r.stdout


def test_open_on_a_desktop_opens(world, monkeypatch):
    world["up"] = True
    monkeypatch.setenv("FOUNDRYOS_OPEN_BROWSER", "1")
    r = invoke("open")
    assert r.exit_code == 0 and len(world["browser"].urls) == 1


# ------------------------------------------------------------------ ensure_running


def test_ensure_running_without_a_service_starts_a_background_process_once(world, env, monkeypatch):
    settings = Settings(_env_file=None)
    out: list[str] = []
    st = lifecycle.ensure_running(settings, out.append, service=False, linger=False)
    assert st.running and world["spawned"] == 1
    again = lifecycle.ensure_running(settings, out.append, service=False, linger=False)
    assert again.running and world["spawned"] == 1  # idempotent


def test_ensure_running_installs_the_service_and_enables_linger(world, env, monkeypatch):
    monkeypatch.setattr(install, "current_os", lambda: "linux")
    fake_tool(
        env.bin,
        "systemctl",
        env.log,
        f'''
        case "$2" in
          is-active) [ -f "{env.home}/active" ] && echo active || echo inactive ;;
          is-enabled) [ -f "{env.home}/enabled" ] && echo enabled || echo disabled ;;
          enable) touch "{env.home}/enabled" "{env.home}/active"; touch "{env.home}/up" ;;
        esac
    ''',
    )
    fake_tool(env.bin, "loginctl", env.log)
    monkeypatch.setattr(
        lifecycle, "probe_health", lambda port, **k: HEALTH if (env.home / "up").exists() else None
    )
    settings = Settings(_env_file=None)
    out: list[str] = []
    st = lifecycle.ensure_running(settings, out.append, service=True, linger=True)
    assert st.running and world["spawned"] == 0
    log = calls(env.log)
    assert "systemctl --user enable --now foundry-os.service" in log
    assert any(c.startswith("loginctl enable-linger") for c in log)
    n = len(log)
    lifecycle.ensure_running(settings, out.append, service=True, linger=True)
    assert "systemctl --user enable --now foundry-os.service" not in calls(env.log)[n:]


def test_ensure_running_falls_back_when_launchd_refuses_both_domains(world, env, monkeypatch):
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    fake_tool(
        env.bin,
        "launchctl",
        env.log,
        """
        case "$1" in
          bootstrap) echo "Bootstrap failed: 5: Input/output error" >&2; exit 5 ;;
          print) exit 113 ;;
        esac
    """,
    )
    settings = Settings(_env_file=None)
    out: list[str] = []
    st = lifecycle.ensure_running(settings, out.append, service=True, linger=False)
    assert st.running and world["spawned"] == 1  # still running, as a background process
    text = "\n".join(out)
    assert "sudo launchctl bootstrap system /Library/LaunchDaemons/ai.foundry-os.plist" in text
    assert "background process" in text


def test_start_falls_back_to_a_process_when_the_installed_service_cannot_load(
    world, env, monkeypatch
):
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    fake_tool(
        env.bin,
        "launchctl",
        env.log,
        """
        case "$1" in
          bootstrap) exit 5 ;;
          print) exit 113 ;;
        esac
    """,
    )
    plist = env.home / "Library" / "LaunchAgents" / "ai.foundry-os.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")
    r = invoke("start")
    assert r.exit_code == 0, r.output
    assert world["spawned"] == 1 and "could not be started here" in r.stdout


# ------------------------------------------------------------------ install.sh must never hang


def platform_tag() -> str | None:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(
        platform.machine()
    )
    if system is None or arch is None or (system, arch) == ("darwin", "x86_64"):
        return None
    return f"{system}-{arch}"


STUB_LAUNCHER = b"""#!/bin/sh
echo "foundryos $*" >> "$STUB_LOG"
if [ "${1:-}" = serve ]; then sleep 30; fi
exit 0
"""


@pytest.fixture
def installer(tmp_path):
    tag = platform_tag()
    if tag is None or (REPO / "install.sh").exists() is False:
        pytest.skip("install.sh does not support this platform")
    release = make_release(tmp_path / "release", "9.9.9", platform=tag, launcher=STUB_LAUNCHER)
    (tmp_path / "release" / "SHA256SUMS").write_bytes(release.sums)
    log = tmp_path / "stub.log"
    home = tmp_path / "ihome"
    home.mkdir()

    def run(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        e = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "FOUNDRYOS_DOWNLOAD_BASE": f"file://{tmp_path / 'release'}",
            "FOUNDRYOS_DATA_DIR": str(tmp_path / "idata"),
            "FOUNDRYOS_PORT": "1",  # nothing answers there: "setup pending" is unknowable
            "STUB_LOG": str(log),
            "TMPDIR": str(tmp_path),
            **(extra_env or {}),
        }
        return subprocess.run(
            [
                "sh",
                str(REPO / "install.sh"),
                "--prefix",
                str(tmp_path / "px"),
                "--version",
                "9.9.9",
                "--no-modify-path",
                *args,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env=e,
            start_new_session=True,  # no controlling terminal: /dev/tty cannot be opened
            check=False,
        )

    run.log = log  # type: ignore[attr-defined]
    return run


def stub_calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_install_sh_does_not_hang_without_a_terminal_over_ssh(installer):
    r = installer(extra_env={"SSH_CONNECTION": "10.0.0.2 1 10.0.0.9 22"})
    assert r.returncode == 0, r.stdout + r.stderr
    log = stub_calls(installer.log)
    assert "foundryos service install" in log and "foundryos " in log  # started, bare `foundryos`
    assert not any(c.startswith("foundryos setup") for c in log)  # no terminal: no wizard
    assert "Next: foundryos setup" in r.stdout


def test_install_sh_passes_linger_to_the_service(installer):
    r = installer("--linger", extra_env={"SSH_CONNECTION": "10.0.0.2 1 10.0.0.9 22"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "foundryos service install --linger" in stub_calls(installer.log)


def test_install_sh_has_no_linger_unless_asked(installer):
    r = installer()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "foundryos service install" in stub_calls(installer.log)
    assert not any("--linger" in c for c in stub_calls(installer.log))


def test_install_sh_no_start_never_runs_the_program(installer):
    r = installer("--no-start")
    assert r.returncode == 0 and stub_calls(installer.log) == []


def test_install_sh_finishes_setup_in_the_terminal_on_a_headless_box(installer, tmp_path):
    """A pseudo-terminal gives the installer a /dev/tty; a fake curl says the server awaits its owner."""
    import fcntl
    import pty
    import termios

    fakebin = tmp_path / "fakecurl"
    fake_tool(
        fakebin, "curl", tmp_path / "curl.log", 'echo \'{"state": "setup_required"}\'; exit 0'
    )
    master, slave = pty.openpty()

    def controlling_tty() -> None:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    env = {
        "PATH": f"{fakebin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path / "ihome"),
        "FOUNDRYOS_DOWNLOAD_BASE": f"file://{tmp_path / 'release'}",
        "FOUNDRYOS_DATA_DIR": str(tmp_path / "idata"),
        "STUB_LOG": str(installer.log),
        "SSH_CONNECTION": "10.0.0.2 1 10.0.0.9 22",
        "TMPDIR": str(tmp_path),
    }
    proc = subprocess.Popen(
        [
            "sh",
            str(REPO / "install.sh"),
            "--prefix",
            str(tmp_path / "px"),
            "--version",
            "9.9.9",
            "--no-modify-path",
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
        preexec_fn=controlling_tty,  # noqa: PLW1509
    )
    os.close(slave)
    output = b""
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:  # drain the terminal: a full pty would block the child
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # the child closed its side
                    chunk = b""
                if not chunk:
                    break
                output += chunk
            elif proc.poll() is not None:
                break
        assert proc.wait(timeout=30) == 0, output.decode(errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    assert "foundryos setup" in stub_calls(installer.log)
