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


@pytest.fixture
def mac(env, monkeypatch):
    monkeypatch.setattr(install, "current_os", lambda: "darwin")
    fake_tool(
        env.bin,
        "launchctl",
        env.log,
        f"""
        if [ "$1" = print ]; then
          [ -f "{env.home}/loaded" ] || exit 113
          printf '{LAUNCHD_PRINT}'
        elif [ "$1" = bootstrap ]; then touch "{env.home}/loaded"
        elif [ "$1" = bootout ]; then rm -f "{env.home}/loaded"
        fi
        """,
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
    assert not any(c.startswith("loginctl") for c in log)
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
