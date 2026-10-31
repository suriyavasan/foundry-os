"""Install methods for every coding CLI: npm, and the vendor's official installer script. Everything is a
fake: scripts come from a fake HTTP transport and run only as tiny local shell files. No real vendor
installer is ever downloaded or run."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time

import httpx
import pytest

from app.models.audit import AuditEvent
from app.runtimes.cli.catalog import (
    CLIS,
    assert_safe_install_argv,
    npm_method,
    script_method,
)
from app.services import cli_runtimes
from tests.conftest import human
from tests.helpers import new_agent
from tests.m10_helpers import FakeInstallerHost, fake_installer_source, write_script
from tests.test_m10_cli_runtimes_api import fake_npm, items, wait_install

AGY_URL = "https://antigravity.google/cli/install.sh"
CURSOR_URL = "https://cursor.com/install"
CODEX_URL = "https://chatgpt.com/codex/install.sh"


@pytest.fixture
def home(app, tmp_path, monkeypatch):
    """A temp HOME with the system PATH on (so known dirs under it are searched). Real CLIs on the
    developer's machine must not matter: PATH is cut down to the system dirs."""
    h = tmp_path / "home"
    h.mkdir()
    app.state.cli_env.home = h
    app.state.cli_env.use_system_path = True
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    real_search_path = type(app.state.cli_env).search_path

    def search_path(self, spec=None):  # drop the fixed Homebrew dirs, where a real CLI may live
        dirs = real_search_path(self, spec).split(os.pathsep)
        return os.pathsep.join(d for d in dirs if d not in ("/opt/homebrew/bin", "/usr/local/bin"))

    monkeypatch.setattr(type(app.state.cli_env), "search_path", search_path)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def bin_dir(app, tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    app.state.cli_env.extra_dirs.append(d)
    return d


def host(app, routes) -> FakeInstallerHost:
    h = FakeInstallerHost(routes)
    app.state.installer_transport = h.transport
    return h


def post(client, cli, **body):
    return client.post(f"/api/cli-runtimes/{cli}/install", json={"confirm": True, **body})


def audit_actions(app, action):
    with app.state.session_factory() as db:
        return [e for e in db.query(AuditEvent).all() if e.action == action]


# ------------------------------------------------------------------ catalog


def test_every_cli_has_a_method_and_script_urls_are_https_on_their_host():
    assert set(CLIS) == {"claude_code", "codex", "opencode", "antigravity", "cursor"}
    for spec in CLIS.values():
        assert spec.install_methods, spec.id
        for m in spec.install_methods:
            if m.kind == "script":
                assert m.url.startswith("https://") and m.host in m.url
                assert m.interpreter in ("bash", "sh")
    assert [m.kind for m in CLIS["codex"].install_methods] == ["npm", "script"]
    assert [m.kind for m in CLIS["cursor"].install_methods] == ["script"]


def test_install_argv_rules_refuse_pipes_shell_strings_and_sudo(tmp_path):
    s = script_method(CURSOR_URL, "bash")
    assert_safe_install_argv(["/bin/bash", str(tmp_path / "install.sh")], s)
    for bad in (
        ["/bin/bash", "-c", "curl x | bash"],
        ["sudo", "/bin/bash", "f"],
        ["/bin/bash", "f", "extra"],
        ["/usr/bin/sudo", "bash", "f"],
        ["/bin/zsh", "f"],
    ):
        with pytest.raises(ValueError):
            assert_safe_install_argv(bad, s)
    n = npm_method("@openai/codex")
    assert_safe_install_argv(["npm", "install", "-g", "@openai/codex"], n)
    with pytest.raises(ValueError):
        assert_safe_install_argv(["sudo", "npm", "install", "-g", "x"], n)


# ------------------------------------------------------------------ status shape / default method


def test_status_lists_methods_with_exact_commands_and_the_default(client, company, bin_dir, home):
    inst = items(client)["codex"]["install"]
    assert [m["method"] for m in inst["methods"]] == ["npm", "script"]
    npm, script = inst["methods"]
    assert npm == {
        "method": "npm",
        "command": "npm install -g @openai/codex",
        "url": None,
        "available": False,
        "reason": "npm was not found on this machine",
    }
    assert script["command"] == f"Download {CODEX_URL} and run it with sh"
    assert script["url"] == CODEX_URL and script["available"] is True
    assert inst["default_method"] == "script" and inst["available"] is True
    assert inst["command"] == script["command"]
    fake_npm(bin_dir)
    inst = items(client)["codex"]["install"]
    assert inst["default_method"] == "npm" and inst["command"] == "npm install -g @openai/codex"
    cursor = items(client)["cursor"]["install"]
    assert [m["method"] for m in cursor["methods"]] == ["script"] and cursor[
        "default_method"
    ] == "script"


def test_no_method_can_run_when_the_interpreter_is_missing(client, company, monkeypatch, bin_dir):
    real = cli_runtimes.CliEnvironment.find_tool
    monkeypatch.setattr(
        cli_runtimes.CliEnvironment,
        "find_tool",
        lambda self, n: None if n == "bash" else real(self, n),
    )
    inst = items(client)["cursor"]["install"]
    assert inst["available"] is False and inst["default_method"] is None
    assert inst["methods"][0]["reason"] == "bash was not found on this machine"
    assert inst["command"] == f"Download {CURSOR_URL} and run it with bash"  # still shown
    r = post(client, "cursor")
    assert r.status_code == 409 and "bash" in r.json()["error"]["message"]
    assert "curl https://cursor.com/install" in r.json()["error"]["message"]  # the manual command


def test_unknown_or_unsupported_method_is_rejected(client, company, bin_dir, home):
    assert post(client, "cursor", method="npm").status_code == 422
    assert post(client, "cursor", method="bogus").status_code == 422
    assert post(client, "codex", method="npm").status_code == 409  # no npm here
    assert post(client, "codex", confirm=False).status_code == 422


# ------------------------------------------------------------------ script install


def test_script_install_downloads_hashes_runs_bash_on_a_temp_file_and_finds_the_binary(
    app, client, company, bin_dir, home
):
    script = fake_installer_source(home, "agy")
    h = host(app, {AGY_URL: (200, script)})
    assert items(client)["antigravity"]["installed"] is False
    r = post(client, "antigravity", method="script")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["method"] == "script" and body["url"] == AGY_URL
    assert body["command"] == f"Download {AGY_URL} and run it with bash"
    sha = hashlib.sha256(script.encode()).hexdigest()
    assert body["sha256"] == sha
    log = wait_install(client, "antigravity", "succeeded")
    assert log["exit_code"] == 0 and any("installing agy" in ln for ln in log["lines"])
    assert "sk-ant-cli-key-0123456789abcdef" not in " ".join(log["lines"])  # redacted
    assert h.requests == [AGY_URL]
    # the script ran as a private temp file that is gone now
    ran = (home / "installer.path").read_text().strip()
    assert ran.endswith("install.sh") and not os.path.exists(ran)
    assert not os.path.exists(os.path.dirname(ran))
    # binary discovered without a restart
    s = items(client)["antigravity"]
    assert s["installed"] is True and s["version"] == "1.3.3"
    assert s["binary_path"] == str(home / ".local/bin/agy")
    assert s["install"]["available"] is False
    # audit: start + finish carry method, url, sha256, exit code and no output
    started = audit_actions(app, "cli_runtime.install_started")[-1]
    finished = audit_actions(app, "cli_runtime.install_finished")[-1]
    assert started.data["sha256"] == sha and started.data["url"] == AGY_URL
    assert started.data["method"] == "script"
    assert finished.data["exit_code"] == 0 and finished.data["status"] == "succeeded"
    assert finished.data["sha256"] == sha
    assert "installing" not in str(finished.data) and "leaked" not in str(finished.data)


def test_script_argv_has_no_pipe_shell_or_sudo(app, client, company, bin_dir, home, monkeypatch):
    seen: list[list[str]] = []
    real = subprocess.Popen

    def spy(argv, *a, **kw):
        seen.append(list(argv))
        return real(argv, *a, **kw)

    monkeypatch.setattr(cli_runtimes.subprocess, "Popen", spy)
    host(app, {CURSOR_URL: (200, fake_installer_source(home, "agent"))})
    assert post(client, "cursor").status_code == 202
    wait_install(client, "cursor", "succeeded")
    (argv,) = seen
    assert len(argv) == 2 and os.path.basename(argv[0]) == "bash"
    assert argv[1].endswith("install.sh") and "|" not in " ".join(argv)
    assert "sudo" not in " ".join(argv) and "-c" not in argv


def test_a_failing_script_is_reported_and_the_temp_file_removed(
    app, client, company, bin_dir, home
):
    host(app, {AGY_URL: (200, fake_installer_source(home, "agy", exit_code=3))})
    post(client, "antigravity")
    log = wait_install(client, "antigravity", "failed")
    assert log["exit_code"] == 3
    ran = (home / "installer.path").read_text().strip()
    assert not os.path.exists(os.path.dirname(ran))
    assert items(client)["antigravity"]["installed"] is False
    assert audit_actions(app, "cli_runtime.install_finished")[-1].data["status"] == "failed"


def test_script_confirm_is_required_and_nothing_is_downloaded_without_it(
    app, client, company, bin_dir, home
):
    h = host(app, {AGY_URL: (200, "#!/bin/sh\nexit 0\n")})
    assert client.post("/api/cli-runtimes/antigravity/install", json={}).status_code == 422
    assert post(client, "antigravity", confirm=False).status_code == 422
    assert h.requests == []


# ------------------------------------------------------------------ download safety


def test_a_cross_host_redirect_is_refused(app, client, company, bin_dir, home):
    h = host(app, {AGY_URL: (302, "https://evil.example/install.sh")})
    r = post(client, "antigravity")
    assert r.status_code == 409 and "antigravity.google" in r.json()["error"]["message"]
    assert h.requests == [AGY_URL]  # the other host was never contacted
    assert not audit_actions(app, "cli_runtime.install_started")
    assert client.get("/api/cli-runtimes/antigravity/install-log").json()["status"] == "none"


def test_a_same_host_redirect_is_followed_and_http_is_refused(app, client, company, bin_dir, home):
    other = "https://antigravity.google/cli/v2/install.sh"
    h = host(
        app,
        {
            AGY_URL: (301, "/cli/v2/install.sh"),
            other: (200, fake_installer_source(home, "agy")),
        },
    )
    assert post(client, "antigravity").status_code == 202
    wait_install(client, "antigravity", "succeeded")
    assert h.requests == [AGY_URL, other]
    # a downgrade to http (even on the same host) is refused
    h2 = host(app, {CURSOR_URL: (302, "http://cursor.com/install")})
    assert post(client, "cursor").status_code == 409
    assert h2.requests == [CURSOR_URL]


def test_redirect_loops_oversize_empty_and_error_responses_are_refused(
    app, client, company, bin_dir, home
):
    host(app, {AGY_URL: (302, AGY_URL)})
    assert "redirects" in post(client, "antigravity").json()["error"]["message"]
    host(app, {AGY_URL: (200, b"x" * (cli_runtimes.SCRIPT_MAX_BYTES + 1))})
    assert "1 MB" in post(client, "antigravity").json()["error"]["message"]
    host(app, {AGY_URL: (200, b"  \n")})
    assert post(client, "antigravity").status_code == 409
    host(app, {AGY_URL: (500, b"oops")})
    assert "500" in post(client, "antigravity").json()["error"]["message"]
    assert client.get("/api/cli-runtimes/antigravity/install-log").json()["status"] == "none"


def test_a_transport_error_is_a_clear_conflict(app, client, company, bin_dir, home):
    def boom(request):
        raise httpx.ConnectError("no route")

    app.state.installer_transport = httpx.MockTransport(boom)
    r = post(client, "antigravity")
    assert r.status_code == 409 and "download" in r.json()["error"]["message"].lower()


# ------------------------------------------------------------------ method choice / state


def test_default_is_npm_when_present_else_script_and_npm_can_be_forced(
    app, client, company, bin_dir, home
):
    fake_npm(bin_dir)
    h = host(app, {CODEX_URL: (200, "#!/bin/sh\nexit 0\n")})
    r = post(client, "codex")
    assert r.status_code == 202 and r.json()["method"] == "npm"
    wait_install(client, "codex", "succeeded")
    assert h.requests == []  # the script was never fetched
    assert items(client)["codex"]["installed"] is True


def test_script_is_chosen_when_it_is_asked_for_even_if_npm_exists(
    app, client, company, bin_dir, home
):
    fake_npm(bin_dir)
    h = host(app, {CODEX_URL: (200, fake_installer_source(home, "codex"))})
    r = post(client, "codex", method="script")
    assert r.status_code == 202 and r.json()["method"] == "script"
    wait_install(client, "codex", "succeeded")
    assert h.requests == [CODEX_URL]
    assert not (bin_dir / "npm.argv").exists()
    ran = (home / "installer.path").read_text().strip()
    assert os.path.basename(ran) == "install.sh"


def test_already_installed_conflicts_and_nothing_is_downloaded(app, client, company, bin_dir, home):
    write_script(bin_dir, "agy", "print('1.0')")
    h = host(app, {AGY_URL: (200, "#!/bin/sh\nexit 0\n")})
    assert post(client, "antigravity").status_code == 409
    assert h.requests == []


def test_installs_are_single_flight(app, client, company, bin_dir, home):
    marker = home / "go"
    slow = f'#!/bin/sh\nwhile [ ! -f "{marker}" ]; do sleep 0.05; done\nexit 0\n'
    h = host(app, {AGY_URL: (200, slow)})
    assert post(client, "antigravity").status_code == 202
    r = post(client, "antigravity")
    assert r.status_code == 409 and "already being installed" in r.json()["error"]["message"]
    assert h.requests == [AGY_URL]  # the second request did not even download
    marker.write_text("x")
    wait_install(client, "antigravity", "succeeded")
    deadline = time.monotonic() + 5
    while any(t.name == "cli-install-antigravity" and t.is_alive() for t in threading.enumerate()):
        assert time.monotonic() < deadline
        time.sleep(0.05)


# ------------------------------------------------------------------ authorization


def test_only_the_installation_owner_can_install(
    app, client, company, make_user, client_as, add_member, bin_dir, home
):
    from tests.test_agent_api import start_run

    cid = company["id"]
    h = host(app, {AGY_URL: (200, fake_installer_source(home, "agy"))})
    agent = new_agent(client, cid)
    session = start_run(app, client, cid, agent["id"])
    r = client.post(
        "/api/cli-runtimes/antigravity/install", json={"confirm": True}, headers=session.headers
    )
    assert r.status_code == 403
    for role in ("admin", "viewer"):
        user = make_user(f"{role}@x.test")
        add_member(cid, user.id, role)
        other = client_as(human(user))
        assert (
            other.post("/api/cli-runtimes/antigravity/install", json={"confirm": True}).status_code
            == 403
        )
        assert other.get("/api/cli-runtimes/antigravity/install-log").status_code == 403
    assert h.requests == []
