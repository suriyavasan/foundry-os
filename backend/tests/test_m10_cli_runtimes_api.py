"""M10 section 3: /api/cli-runtimes (status, sign-in mode, install, test) with fake binaries and a
fake npm. Nothing here installs software or signs in anywhere."""

from __future__ import annotations

import json
import time

import pytest

from tests.conftest import human
from tests.helpers import api, new_agent
from tests.m10_helpers import (
    CLAUDE_OK,
    OPENCODE_OK,
    install_fake_cli,
    make_ready,
    register_cli_adapters,
    write_script,
)

KEY = "sk-ant-cli-key-0123456789abcdef"


@pytest.fixture
def bin_dir(app, tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    app.state.cli_env.extra_dirs.append(d)
    register_cli_adapters(app)
    return d


def items(client) -> dict[str, dict]:
    r = client.get("/api/cli-runtimes")
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def test_status_of_clis_installed_or_not(client, company, bin_dir):
    s = items(client)
    assert set(s) == {"claude_code", "codex", "opencode", "antigravity", "cursor"}
    codex = s["codex"]
    assert codex["installed"] is False and codex["binary_path"] is None and codex["version"] is None
    assert codex["install"] == {
        "available": False,  # npm is not there either
        "command": "npm install -g @openai/codex",
        "requires": ["npm"],
        "status": "none",
        "manual": None,
    }
    assert codex["auth_modes"] == ["own_login", "api_key"]
    assert codex["auth"]["configured"] is False and codex["auth"]["mode"] is None
    assert codex["login_hint"] == "Run `codex login` in a terminal"
    assert (
        codex["sandbox"]["kind"] == "os" and codex["sandbox"]["available"] is False
    )  # not installed
    install_fake_cli(bin_dir, "claude", CLAUDE_OK)
    claude = items(client)["claude_code"]
    assert claude["installed"] and claude["version"] == "2.1.292 (Claude Code)"
    assert claude["binary_path"] == str(bin_dir / "claude")
    assert (
        claude["sandbox"]
        == {
            "available": False,
            "kind": "none",
            "detail": claude["sandbox"]["detail"],
        }
        and "Bash" in claude["sandbox"]["detail"]
    )
    assert client.get("/api/cli-runtimes/opencode").json()["id"] == "opencode"
    assert client.get("/api/cli-runtimes/nope").status_code == 404


def test_auth_modes_validation_and_the_key_is_never_returned(client, company, bin_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", CLAUDE_OK)
    cred = client.post(api(cid, "/credentials"), json={"name": "ant", "value": KEY}).json()
    put = lambda cli, **b: client.put(f"/api/cli-runtimes/{cli}/auth", json=b)  # noqa: E731
    assert put("claude_code", mode="api_key").status_code == 422  # needs a credential
    assert put("claude_code", mode="own_login", credential_id=cred["id"]).status_code == 422
    assert (
        put(
            "claude_code", mode="api_key", credential_id=cred["id"], model_provider_id="x"
        ).status_code
        == 422
    )
    assert put("claude_code", mode="api_key", credential_id="nope").status_code == 404
    assert put("opencode", mode="own_login").status_code == 422  # OpenCode needs a model
    r = put("claude_code", mode="api_key", credential_id=cred["id"])
    assert r.status_code == 200 and KEY not in r.text
    auth = r.json()["auth"]
    assert auth == {
        "mode": "api_key",
        "configured": True,
        "credential_id": cred["id"],
        "last_test": None,
        "model_provider_id": None,
        "model": None,
    }
    r = put("claude_code", mode="own_login")
    assert r.json()["auth"]["mode"] == "own_login" and r.json()["auth"]["credential_id"] is None
    assert any("terms" in w for w in r.json()["warnings"])  # the own-login warning
    # the activity feed records it without the key
    feed = client.get(api(cid, "/activity")).text
    assert "cli_runtime.auth_updated" in feed and KEY not in feed


def test_a_credential_of_a_company_you_do_not_administer_is_not_found(
    app, client, company, make_user, client_as, bin_dir
):
    other_owner = make_user("o2@x.test")
    other = client_as(human(other_owner))
    other_company = other.post("/api/companies", json={"name": "Other"}).json()
    cred = other.post(
        api(other_company["id"], "/credentials"), json={"name": "k", "value": KEY}
    ).json()
    r = client.put(
        "/api/cli-runtimes/claude_code/auth", json={"mode": "api_key", "credential_id": cred["id"]}
    )
    assert r.status_code == 404


def test_agents_and_other_members_cannot_touch_the_clis(
    app, client, company, make_user, client_as, add_member, bin_dir
):
    from tests.test_agent_api import start_run

    cid = company["id"]
    agent = new_agent(client, cid)
    session = start_run(app, client, cid, agent["id"])
    for method, path, body in (
        ("get", "/api/cli-runtimes", None),
        ("post", "/api/cli-runtimes/codex/install", {"confirm": True}),
        ("put", "/api/cli-runtimes/codex/auth", {"mode": "own_login"}),
        ("post", "/api/cli-runtimes/codex/test", None),
        ("get", "/api/cli-runtimes/codex/install-log", None),
    ):
        r = getattr(client, method)(
            path, headers=session.headers, **({"json": body} if body else {})
        )
        assert r.status_code == 403, (path, r.text)
        assert r.json()["error"]["code"] == "guardrail_denied"
    # an admin of a company is not the installation owner
    admin_user = make_user("admin@x.test")
    add_member(cid, admin_user.id, "admin")
    admin = client_as(human(admin_user))
    assert admin.get("/api/cli-runtimes").status_code == 403
    assert admin.put("/api/cli-runtimes/codex/auth", json={"mode": "own_login"}).status_code == 403
    # ... but sees the CLI status inside the company's ai-setup
    make_ready(app, client, cid)
    assert {c["id"] for c in admin.get(api(cid, "/ai-setup")).json()["clis"]} >= {"codex"}


# ------------------------------------------------------------------ install


def fake_npm(bin_dir, *, exit_code=0, creates="codex", log="added 1 package"):
    """A stand-in for npm: records its argv, 'installs' by creating the fake binary."""
    source = f"""
import json, os, sys
open(os.path.join({str(bin_dir)!r}, "npm.argv"), "w").write(json.dumps(sys.argv[1:]))
print({log!r}); print("token sk-ant-cli-key-0123456789abcdef leaked?")
if {exit_code} == 0:
    p = os.path.join({str(bin_dir)!r}, {creates!r})
    open(p, "w").write("#!/bin/sh\\necho codex-cli 0.164.0\\n"); os.chmod(p, 0o755)
sys.exit({exit_code})
"""
    return write_script(bin_dir, "npm", source)


def wait_install(client, cli_id, status):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        log = client.get(f"/api/cli-runtimes/{cli_id}/install-log").json()
        if log["status"] == status:
            return log
        time.sleep(0.05)
    raise AssertionError(f"install never reached {status}: {log}")


def test_install_needs_confirm_and_never_runs_without_it(client, company, bin_dir):
    fake_npm(bin_dir)
    for body in ({}, {"confirm": False}):
        r = client.post("/api/cli-runtimes/codex/install", json=body)
        assert r.status_code == 422, r.text
    assert "codex" not in {p.name for p in bin_dir.iterdir()}
    assert not (bin_dir / "npm.argv").exists()  # npm was never even started
    assert client.get("/api/cli-runtimes/codex/install-log").json()["status"] == "none"


def test_install_runs_exactly_the_shown_command_without_a_shell_or_sudo(client, company, bin_dir):
    fake_npm(bin_dir)
    assert items(client)["codex"]["install"]["available"] is True
    r = client.post("/api/cli-runtimes/codex/install", json={"confirm": True})
    assert r.status_code == 202 and r.json()["command"] == "npm install -g @openai/codex"
    log = wait_install(client, "codex", "succeeded")
    assert json.loads((bin_dir / "npm.argv").read_text()) == ["install", "-g", "@openai/codex"]
    assert log["exit_code"] == 0 and any("added 1 package" in line for line in log["lines"])
    assert "sk-ant-cli-key-0123456789abcdef" not in " ".join(log["lines"])  # the log is redacted
    s = items(client)["codex"]
    assert s["installed"] is True and s["version"] == "codex-cli 0.164.0"
    assert s["install"]["available"] is False  # nothing to install any more
    # a second install of an installed CLI is refused
    assert client.post("/api/cli-runtimes/codex/install", json={"confirm": True}).status_code == 409


def test_a_failing_install_is_reported_and_leaves_nothing_behind(client, company, bin_dir):
    fake_npm(bin_dir, exit_code=1)
    client.post("/api/cli-runtimes/codex/install", json={"confirm": True})
    log = wait_install(client, "codex", "failed")
    assert log["exit_code"] == 1 and log["finished_at"]
    assert items(client)["codex"]["installed"] is False


def test_install_without_npm_is_a_clear_conflict(client, company, bin_dir):
    r = client.post("/api/cli-runtimes/codex/install", json={"confirm": True})
    assert r.status_code == 409 and "npm" in r.json()["error"]["message"]


def test_the_install_argv_never_contains_sudo(client, company, bin_dir):
    from app.runtimes.cli.catalog import CLIS

    for spec in CLIS.values():
        if spec.install_command is None:  # Antigravity: a curl | bash installer we never run
            assert spec.id in ("antigravity", "cursor") and spec.npm_package is None
            continue
        assert spec.install_command[:3] == ["npm", "install", "-g"]
        assert "sudo" not in spec.install_command


# ------------------------------------------------------------------ test


def test_the_connectivity_test_runs_the_cli_headless_and_stores_the_result(
    client, company, bin_dir
):
    cid = company["id"]
    install_fake_cli(
        bin_dir,
        "claude",
        [{"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": {}}],
    )
    assert (
        client.post("/api/cli-runtimes/claude_code/test").status_code == 409
    )  # no sign-in mode yet
    cred = client.post(api(cid, "/credentials"), json={"name": "ant", "value": KEY}).json()
    client.put(
        "/api/cli-runtimes/claude_code/auth", json={"mode": "api_key", "credential_id": cred["id"]}
    )
    r = client.post("/api/cli-runtimes/claude_code/test")
    assert r.status_code == 200, r.text
    last = r.json()["auth"]["last_test"]
    assert last["ok"] is True and "OK" in last["detail"] and last["tested_at"]
    rec = json.loads((bin_dir / "claude.record.json").read_text())
    argv = rec["argv"]
    assert "--max-budget-usd=0.0500" in argv and "--tools=" in argv  # capped, no tools, no MCP
    assert not any("mcp" in a for a in argv)
    assert KEY not in r.text
    # a failing CLI: the detail says so
    install_fake_cli(bin_dir, "claude", [], exit=2, stderr="login required")
    last = client.post("/api/cli-runtimes/claude_code/test").json()["auth"]["last_test"]
    assert last["ok"] is False and "code 2" in last["detail"]
    # changing the sign-in settings forgets the old result
    r = client.put("/api/cli-runtimes/claude_code/auth", json={"mode": "own_login"})
    assert r.json()["auth"]["last_test"] is None


def test_opencode_test_uses_a_company_provider_without_any_account(client, app, company, bin_dir):
    cid = company["id"]
    p = make_ready(app, client, cid)
    install_fake_cli(bin_dir, "opencode", [*OPENCODE_OK])
    client.put(
        "/api/cli-runtimes/opencode/auth",
        json={"mode": "api_key", "model_provider_id": p["id"], "model": "qwen2.5:7b"},
    )
    r = client.post("/api/cli-runtimes/opencode/test")
    assert r.json()["auth"]["last_test"]["ok"] is True, r.text
    assert r.json()["auth"]["model_provider_id"] == p["id"]


# ------------------------------------------------------------------ hybrid is validated against the CLI


def test_hybrid_needs_an_installed_configured_cli(app, client, company, bin_dir):
    cid = company["id"]
    p = make_ready(app, client, cid)
    body = {
        "management_provider_id": p["id"],
        "management_model": "qwen2.5:7b",
        "work_mode": "hybrid",
        "work_runtime": "cli:codex",
    }
    r = client.put(api(cid, "/ai-setup"), json=body)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "cli_not_installed"
    install_fake_cli(bin_dir, "codex", [])
    r = client.put(api(cid, "/ai-setup"), json=body)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "cli_not_configured"
    client.put("/api/cli-runtimes/codex/auth", json={"mode": "own_login"})
    r = client.put(api(cid, "/ai-setup"), json=body)
    assert r.status_code == 200 and r.json()["work_mode"] == "hybrid"
    assert r.json()["work_runtime"] == "cli:codex"
    # back to API only clears the runtime
    r = client.put(
        api(cid, "/ai-setup"), json={**body, "work_mode": "api", "work_runtime": "cli:codex"}
    )
    assert r.json()["work_mode"] == "api" and r.json()["work_runtime"] is None
