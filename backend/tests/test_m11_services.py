"""M11: the background service (LaunchAgent / systemd user unit), against fake launchctl/systemctl/brew."""

from __future__ import annotations

import os
import plistlib
import shlex
from pathlib import Path

import pytest

from app.core import install
from app.core.config import Settings
from app.ops import lifecycle, services
from tests.m11_helpers import (  # noqa: F401
    Env,
    calls,
    env,
    fake_tool,
    installer_layout,
    invoke,
    write_bundle,
)

LAUNCHD_PRINT = "state = running\n\tpid = 4242\n"


def healthy_when(flag: Path):
    """The fake server 'answers' exactly while the fake service manager says the service is up."""

    def probe(port, **kwargs):
        return (
            {"status": "ok", "auth_mode": "password", "version": "0.1.0"} if flag.exists() else None
        )

    return probe


FAKE_LAUNCHCTL = """
domain=${2%/*}
case "$1" in
  print)
    [ -f "@HOME@/loaded" ] && [ "$(cat "@HOME@/loaded")" = "$domain" ] || exit 113
    printf '@PRINT@'
    ;;
  bootstrap)
    domain=$2
    case "$domain" in
      gui/*) [ -z "$FAKE_GUI_FAIL" ] || { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; } ;;
      user/*) [ -z "$FAKE_USER_FAIL" ] || { echo "Bootstrap failed: 125: Domain does not support specified action" >&2; exit 125; } ;;
    esac
    echo "$domain" > "@HOME@/loaded"
    ;;
  bootout)
    [ -f "@HOME@/loaded" ] && [ "$(cat "@HOME@/loaded")" = "$domain" ] && rm -f "@HOME@/loaded"
    ;;
esac
"""


@pytest.fixture
def mac(env, monkeypatch):
    """A launchctl that loads a job into exactly one domain (FAKE_GUI_FAIL / FAKE_USER_FAIL refuse one)."""
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    fake_tool(
        env.bin,
        "launchctl",
        env.log,
        FAKE_LAUNCHCTL.replace("@HOME@", str(env.home)).replace(
            "@PRINT@", LAUNCHD_PRINT.replace("\n", "\\n").replace("\t", "\\t")
        ),
    )
    monkeypatch.setattr(lifecycle, "probe_health", healthy_when(env.home / "loaded"))
    return env


@pytest.fixture
def linux(env, monkeypatch):
    monkeypatch.setattr(install, "current_os", lambda: "linux")
    fake_tool(
        env.bin,
        "systemctl",
        env.log,
        f"""
        case "$2" in
          is-active) [ -f "{env.home}/active" ] && echo active || echo inactive ;;
          is-enabled) [ -f "{env.home}/enabled" ] && echo enabled || echo disabled ;;
          enable) touch "{env.home}/enabled" "{env.home}/active" ;;
          disable) rm -f "{env.home}/enabled" "{env.home}/active" ;;
          start|restart) touch "{env.home}/active" ;;
          stop) rm -f "{env.home}/active" ;;
          show) echo 777 ;;
        esac
        """,
    )
    fake_tool(env.bin, "loginctl", env.log)
    monkeypatch.setattr(lifecycle, "probe_health", healthy_when(env.home / "active"))
    return env


def plist_path(env: Env) -> Path:
    return env.home / "Library" / "LaunchAgents" / "ai.foundry-os.plist"


def unit_path(env: Env) -> Path:
    return env.home / ".config" / "systemd" / "user" / "foundry-os.service"


# ------------------------------------------------------------------ rendering


def spec(tmp_path: Path, **kw) -> services.ServiceSpec:
    return services.ServiceSpec(
        command=["/home/u/.foundry-os/current/bin/foundryos", "serve"],
        log_file=tmp_path / "logs" / "foundry-os.log",
        env={"PATH": "/usr/bin:/bin", "FOUNDRYOS_RUN_MODE": "service"},
        **kw,
    )


def test_plist_has_runatload_keepalive_and_logs_in_the_data_dir(tmp_path):
    body = plistlib.loads(services.render_plist(spec(tmp_path)))
    assert body["Label"] == "ai.foundry-os"
    assert body["ProgramArguments"] == ["/home/u/.foundry-os/current/bin/foundryos", "serve"]
    assert body["RunAtLoad"] is True and body["KeepAlive"] is True
    assert (
        body["StandardOutPath"]
        == body["StandardErrorPath"]
        == str(tmp_path / "logs" / "foundry-os.log")
    )
    assert body["EnvironmentVariables"]["FOUNDRYOS_RUN_MODE"] == "service"


def test_plist_matches_the_packaging_template():
    template = install.REPO_DIR / "packaging" / "service" / "ai.foundry-os.plist.tmpl"
    if not template.exists():
        pytest.skip("packaging templates are not part of this checkout")
    text = (
        template.read_text()
        .replace("@FOUNDRYOS_BIN@", "/bin/x")
        .replace("@PATH@", "/usr/bin")
        .replace("@LOG_DIR@", "/tmp/logs")
    )
    theirs = plistlib.loads(text.encode())
    ours = plistlib.loads(services.render_plist(spec(Path("/tmp"))))
    for key in ("Label", "RunAtLoad", "KeepAlive", "ThrottleInterval", "ProcessType"):
        assert ours[key] == theirs[key], key
    assert ours["ProgramArguments"][1:] == theirs["ProgramArguments"][1:]


def test_unit_file_quotes_paths_with_spaces_and_appends_to_the_log(tmp_path):
    odd = services.ServiceSpec(
        command=["/home/my user/.foundry-os/current/bin/foundryos", "serve"],
        log_file=tmp_path / "logs" / "foundry-os.log",
        env={"PATH": "/usr/bin", "FOUNDRYOS_DATA_DIR": "/data/my dir"},
    )
    text = services.render_unit(odd)
    assert 'ExecStart="/home/my user/.foundry-os/current/bin/foundryos" serve' in text
    assert 'Environment="FOUNDRYOS_DATA_DIR=/data/my dir"' in text
    assert f"StandardOutput=append:{tmp_path / 'logs' / 'foundry-os.log'}" in text
    assert "WantedBy=default.target" in text and "Restart=on-failure" in text


def test_build_spec_uses_the_stable_launcher_and_passes_the_data_dir(env, monkeypatch):
    installer_layout(env, monkeypatch)
    s = services.build_spec(Settings(_env_file=None))
    assert s.command == [str(env.prefix / "current" / "bin" / "foundryos"), "serve"]
    assert s.env["FOUNDRYOS_DATA_DIR"] == str(env.data)
    assert s.env["FOUNDRYOS_RUN_MODE"] == "service"
    assert s.log_file == env.data / "logs" / "foundry-os.log"
    assert str(env.home / ".local" / "bin") in s.env["PATH"].split(os.pathsep)


# ------------------------------------------------------------------ macOS


def test_service_install_writes_the_agent_and_loads_it(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    assert plistlib.loads(plist_path(mac).read_bytes())["Label"] == "ai.foundry-os"
    assert any(
        c.startswith("launchctl bootstrap gui/") and "ai.foundry-os.plist" in c
        for c in calls(mac.log)
    )
    assert "is running at" in r.stdout


def test_service_install_twice_reloads_instead_of_duplicating(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    invoke("service", "install")
    invoke("service", "install")
    log = calls(mac.log)
    assert sum(c.startswith("launchctl bootstrap") for c in log) == 2
    assert any(c.startswith("launchctl bootout") for c in log)
    assert len(list(plist_path(mac).parent.glob("*.plist"))) == 1


def test_service_status_and_uninstall_on_macos(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    assert "not installed" in invoke("service", "status").stdout
    invoke("service", "install")
    out = invoke("service", "status", "--json")
    import json

    info = json.loads(out.stdout)
    assert info["service"]["manager"] == "launchd" and info["service"]["running"] is True
    assert info["service"]["pid"] == 4242
    r = invoke("service", "uninstall")
    assert r.exit_code == 0 and not plist_path(mac).exists()
    assert any(c.startswith("launchctl bootout") for c in calls(mac.log))
    assert "not installed" in invoke("service", "uninstall").stdout  # idempotent


def test_start_stop_go_through_launchctl_when_the_service_is_installed(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    invoke("service", "install")
    mac.log.write_text("")
    stopped = services.LaunchdService()
    stopped.stop()
    assert calls(mac.log)[-1].startswith("launchctl bootout gui/")
    stopped.start()
    assert any(c.startswith("launchctl bootstrap gui/") for c in calls(mac.log))
    stopped.restart()  # loaded -> kickstart -k
    assert calls(mac.log)[-1].startswith("launchctl kickstart -k gui/")


# ------------------------------------------------------------------ Linux


def test_systemd_install_enables_the_user_unit_without_linger(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    assert "ExecStart=" in unit_path(linux).read_text()
    log = calls(linux.log)
    assert "systemctl --user daemon-reload" in log
    assert "systemctl --user enable --now foundry-os.service" in log
    assert not any(c.startswith("loginctl enable-linger") for c in log)  # linger stays opt-in
    assert "--linger" in r.stdout  # told how to opt in


def test_systemd_linger_only_when_asked(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    invoke("service", "install", "--linger")
    assert any(c.startswith("loginctl enable-linger") for c in calls(linux.log))


def test_systemd_status_and_uninstall(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    invoke("service", "install")
    import json

    info = json.loads(invoke("service", "status", "--json").stdout)["service"]
    assert info["manager"] == "systemd" and info["running"] is True and info["pid"] == 777
    assert invoke("service", "uninstall").exit_code == 0
    assert not unit_path(linux).exists()
    log = calls(linux.log)
    assert "systemctl --user disable --now foundry-os.service" in log


def test_systemd_without_a_user_session_is_a_clear_error(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    monkeypatch.setenv("FAKE_RC", "1")
    r = invoke("service", "install")
    assert r.exit_code != 0 and "user session" in r.output
    assert not unit_path(linux).exists()


# ------------------------------------------------------------------ Homebrew defers to brew services


def brew_install(env, monkeypatch, tmp_path):
    cellar = tmp_path / "brew" / "Cellar" / "foundry-os" / "0.1.0" / "libexec"
    write_bundle(cellar, "0.1.0")
    monkeypatch.setattr(install, "bundle_root", lambda: cellar)


def test_homebrew_installs_never_get_a_second_agent(mac, monkeypatch, tmp_path):
    brew_install(mac, monkeypatch, tmp_path)
    r = invoke("service", "install")
    assert r.exit_code == 0 and "brew services start foundry-os" in r.stdout
    assert not plist_path(mac).exists() and not calls(mac.log)
    r = invoke("service", "uninstall")
    assert "brew services stop foundry-os" in r.stdout and not calls(mac.log)


def test_an_existing_brew_service_blocks_a_duplicate_even_from_the_installer(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    brew_plist = mac.home / "Library" / "LaunchAgents" / "homebrew.mxcl.foundry-os.plist"
    brew_plist.parent.mkdir(parents=True)
    brew_plist.write_text("<plist/>")
    r = invoke("service", "install")
    assert "brew services" in r.stdout and not plist_path(mac).exists()


def test_start_and_stop_use_brew_services_when_that_service_exists(mac, monkeypatch, tmp_path):
    brew_install(mac, monkeypatch, tmp_path)
    brew_plist = mac.home / "Library" / "LaunchAgents" / "homebrew.mxcl.foundry-os.plist"
    brew_plist.parent.mkdir(parents=True)
    brew_plist.write_text("<plist/>")
    fake_tool(mac.bin, "brew", mac.log, 'case "$2" in info) echo \'[{"running":true}]\';; esac')
    svc = services.active_service()
    assert isinstance(svc, services.BrewService) and svc.status().running is True
    svc.start()
    svc.stop()
    svc.restart()
    assert [c for c in calls(mac.log) if "services" in c and "info" not in c] == [
        "brew services start foundry-os",
        "brew services stop foundry-os",
        "brew services restart foundry-os",
    ]


def test_tools_come_from_path_only(mac):
    # the fake is first on PATH: a real launchctl can never be reached by these tests
    import shutil

    assert shutil.which("launchctl") == str(mac.bin / "launchctl")
    assert shlex.quote("x")  # keep shlex import used for readability of failures


def test_restart_and_stop_drive_the_service_manager_when_a_service_is_installed(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    invoke("service", "install")
    mac.log.write_text("")
    r = invoke("restart")
    assert r.exit_code == 0, r.output
    assert any(c.startswith("launchctl kickstart -k gui/") for c in calls(mac.log))
    r = invoke("stop")
    assert r.exit_code == 0 and "background service (launchd)" in r.stdout
    assert any(c.startswith("launchctl bootout gui/") for c in calls(mac.log))
    r = invoke("start")  # not running now: goes through the service, not a detached process
    assert r.exit_code == 0 and "through its background service" in r.stdout
    assert any(c.startswith("launchctl bootstrap gui/") for c in calls(mac.log))


# ------------------------------------------------------------------ macOS without a graphical login


def test_gui_domain_refused_falls_back_to_the_user_domain(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    monkeypatch.setenv("FAKE_GUI_FAIL", "1")
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    uid = os.getuid()
    log = calls(mac.log)
    assert any(c.startswith(f"launchctl bootstrap gui/{uid} ") for c in log)  # tried first
    assert any(c.startswith(f"launchctl bootstrap user/{uid} ") for c in log)  # then this
    assert (mac.home / "Library" / "LaunchAgents" / "ai.foundry-os.domain").read_text().strip() == (
        f"user/{uid}"
    )
    assert "is running at" in r.stdout and "does not start by itself after a reboot" in r.stdout
    # the boot story: auto-login or a LaunchDaemon, with the exact commands, none of them run
    assert "automatic login" in r.stdout
    assert "sudo launchctl bootstrap system /Library/LaunchDaemons/ai.foundry-os.plist" in r.stdout
    assert "sudo chown root:wheel /Library/LaunchDaemons/ai.foundry-os.plist" in r.stdout
    assert not any(c.startswith("sudo") for c in log)
    daemon = mac.data / "ai.foundry-os.daemon.plist"
    body = plistlib.loads(daemon.read_bytes())
    assert (
        body["UserName"]
        and body["Label"] == "ai.foundry-os"
        and body["EnvironmentVariables"]["HOME"]
    )
    assert "UserName" not in plistlib.loads(plist_path(mac).read_bytes())  # the agent has none


def test_status_stop_and_uninstall_use_the_recorded_domain(mac, monkeypatch):
    import json

    installer_layout(mac, monkeypatch)
    monkeypatch.setenv("FAKE_GUI_FAIL", "1")
    invoke("service", "install")
    info = json.loads(invoke("service", "status", "--json").stdout)["service"]
    assert info["running"] is True and info["domain"] == f"user/{os.getuid()}"
    assert "user/" in invoke("service", "status").stdout
    mac.log.write_text("")
    svc = services.LaunchdService()
    svc.restart()
    assert calls(mac.log)[-1].startswith(f"launchctl kickstart -k user/{os.getuid()}/")
    r = invoke("service", "uninstall")
    assert r.exit_code == 0 and not plist_path(mac).exists()
    assert any(c.startswith(f"launchctl bootout user/{os.getuid()}/") for c in calls(mac.log))
    assert not (mac.home / "Library" / "LaunchAgents" / "ai.foundry-os.domain").exists()


def test_status_finds_an_agent_loaded_in_either_domain(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    plist_path(mac).parent.mkdir(parents=True)
    plist_path(mac).write_bytes(b"<plist/>")  # no record of a domain
    (mac.home / "loaded").write_text(f"user/{os.getuid()}\n")
    st = services.LaunchdService().status()
    assert st.loaded and st.running and st.domain == f"user/{os.getuid()}"
    (mac.home / "loaded").write_text(f"gui/{os.getuid()}\n")
    assert services.LaunchdService().status().domain == f"gui/{os.getuid()}"


def test_both_domains_refused_is_reported_honestly(mac, monkeypatch):
    installer_layout(mac, monkeypatch)
    monkeypatch.setenv("FAKE_GUI_FAIL", "1")
    monkeypatch.setenv("FAKE_USER_FAIL", "1")
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    assert plist_path(mac).exists()
    assert "would not load it" in r.stdout and "foundryos start" in r.stdout
    assert "sudo launchctl bootstrap system" in r.stdout
    assert "starts at login" not in r.stdout and "is running at" not in r.stdout
    assert not (mac.home / "Library" / "LaunchAgents" / "ai.foundry-os.domain").exists()


# ------------------------------------------------------------------ Linux without a user bus

NO_BUS = """
if [ -z "$XDG_RUNTIME_DIR" ] || [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
  echo "Failed to connect to bus: No medium found" >&2
  exit 1
fi
echo "bus XDG=$XDG_RUNTIME_DIR DBUS=$DBUS_SESSION_BUS_ADDRESS" >> "@LOG@"
case "$2" in
  is-active) [ -f "@HOME@/active" ] && echo active || echo inactive ;;
  is-enabled) [ -f "@HOME@/enabled" ] && echo enabled || echo disabled ;;
  enable) touch "@HOME@/enabled" "@HOME@/active" ;;
  show) echo 777 ;;
esac
"""


@pytest.fixture
def no_bus(env, monkeypatch, tmp_path):
    """A systemctl that only works once XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS point at a bus."""
    monkeypatch.setattr(install, "current_os", lambda: "linux")
    runtime_root = tmp_path / "run-user"
    monkeypatch.setattr(services, "RUNTIME_ROOT", runtime_root)
    fake_tool(
        env.bin,
        "systemctl",
        env.log,
        NO_BUS.replace("@HOME@", str(env.home)).replace("@LOG@", str(env.log)),
    )
    fake_tool(env.bin, "loginctl", env.log)
    monkeypatch.setattr(lifecycle, "probe_health", healthy_when(env.home / "active"))
    return runtime_root


def test_systemctl_without_a_bus_is_retried_with_the_runtime_dir(env, no_bus, monkeypatch):
    runtime = no_bus / str(os.getuid())
    runtime.mkdir(parents=True)
    installer_layout(env, monkeypatch)
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    assert unit_path(env).exists()
    log = calls(env.log)
    assert f"bus XDG={runtime} DBUS=unix:path={runtime}/bus" in log
    assert log.count("systemctl --user show-environment") == 2  # failed once, retried once
    assert services.SystemdService().status().running is True


def test_systemctl_without_a_bus_and_without_a_runtime_dir_is_a_clear_error(
    env, no_bus, monkeypatch
):
    installer_layout(env, monkeypatch)  # /run/user/<uid> does not exist
    r = invoke("service", "install")
    assert r.exit_code != 0 and "user session" in r.output and "XDG_RUNTIME_DIR" in r.output
    assert not unit_path(env).exists()


def test_a_working_environment_is_not_retried(env, no_bus, monkeypatch):
    runtime = no_bus / str(os.getuid())
    runtime.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    assert services.user_bus_env() is None
    assert services.SystemdService().available() is True
    assert calls(env.log).count("systemctl --user show-environment") == 1


# ------------------------------------------------------------------ linger


def loginctl(env: Env, body: str) -> None:
    fake_tool(env.bin, "loginctl", env.log, body)


def test_linger_state_reads_loginctl(linux):
    loginctl(linux, 'echo "Linger=yes"')
    assert services.linger_state("alice") is True
    assert "loginctl show-user alice -p Linger" in calls(linux.log)
    loginctl(linux, 'echo "Linger=no"')
    assert services.linger_state() is False
    loginctl(linux, "echo oops >&2; exit 1")
    assert services.linger_state() is None
    loginctl(linux, 'echo "Something=else"')
    assert services.linger_state() is None


def test_linger_state_is_unknown_off_linux_and_without_loginctl(env, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    assert services.linger_state() is None
    monkeypatch.setattr(install, "current_os", lambda: "linux")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # a real Linux runner has /usr/bin/loginctl
    assert services.linger_state() is None  # no loginctl on the PATH


def test_enable_linger_success_and_failure_never_uses_sudo(linux):
    fake_tool(linux.bin, "sudo", linux.log)
    ok, message = services.enable_linger("alice")
    assert ok is True and "alice" in message
    assert "loginctl enable-linger alice" in calls(linux.log)
    loginctl(linux, 'echo "Failed to set linger: Access denied" >&2; exit 1')
    ok, message = services.enable_linger("alice")
    assert ok is False
    assert "sudo loginctl enable-linger alice" in message and "Access denied" in message
    assert not any(c.startswith("sudo") for c in calls(linux.log))


def test_enable_linger_without_loginctl_still_says_what_to_run(env, monkeypatch):
    monkeypatch.setattr(install, "current_os", lambda: "linux")
    ok, message = services.enable_linger("bob")
    assert ok is False and "sudo loginctl enable-linger bob" in message


def test_install_with_a_failing_linger_still_succeeds_and_prints_the_sudo_hint(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    loginctl(linux, "echo denied >&2; exit 1")
    r = invoke("service", "install", "--linger")
    assert r.exit_code == 0, r.output
    assert "sudo loginctl enable-linger" in r.stdout


def test_install_over_ssh_warns_that_it_stops_at_logout(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 1 10.0.0.9 22")
    loginctl(linux, 'echo "Linger=no"')
    r = invoke("service", "install")
    assert r.exit_code == 0, r.output
    assert "stops when you log out" in r.stdout and "--linger" in r.stdout
    assert not any(c.startswith("loginctl enable-linger") for c in calls(linux.log))  # still opt-in


def test_install_over_ssh_with_linger_already_on_does_not_warn(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 1 10.0.0.9 22")
    loginctl(linux, 'echo "Linger=yes"')
    r = invoke("service", "install")
    assert "stops when you log out" not in r.stdout and "Linger is already on" in r.stdout


def test_service_status_over_ssh_says_when_linger_is_off(linux, monkeypatch):
    installer_layout(linux, monkeypatch)
    invoke("service", "install")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 1 10.0.0.9 22")
    loginctl(linux, 'echo "Linger=no"')
    assert "Linger is off" in invoke("service", "status").stdout
