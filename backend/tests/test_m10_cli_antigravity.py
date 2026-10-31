"""M10: the Antigravity CLI (``agy``) adapter against a fake binary printing the documented
stream-json shape. ``agy`` is never started with a prompt here and nothing uses anyone's Google login;
the fake is a Python script (tests/m10_helpers.py) that records argv, environment and the generated
files under the child's ``HOME``."""

from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

import pytest

from app.runtimes.base import CancelToken, RuntimeAdapterError
from app.runtimes.cli import store
from app.runtimes.cli.antigravity import AntigravityAdapter
from app.runtimes.cli.catalog import CLIS, FORBIDDEN_ARGV, assert_safe_argv, sandbox_for
from tests.m10_helpers import (
    ANTIGRAVITY_OK,
    install_fake_cli,
    record_of,
)
from tests.test_m10_cli_adapters import API_KEY, RUN_TOKEN, Sink, World
from tests.test_m10_cli_adapters import world as world  # noqa: F401  (the shared fixture)

BINARY = "agy"


def adapter_of(world: World) -> AntigravityAdapter:
    a = world.app.state
    return AntigravityAdapter(a.settings, a.session_factory, a.keys, a.cli_env)


def configure(world: World) -> None:
    world.set_auth("antigravity", mode="api_key", credential_id=world.credential())


def run(world: World, lines=None, *, done: bool = True, **options):
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK if lines is None else lines, **options)
    configure(world)
    if done:
        world.set_task_status("done")  # the CLI called complete_task through the company tools
    sink = Sink()
    result = adapter_of(world).execute(world.request(), sink, CancelToken())
    return result, sink, record_of(world.bin_dir, BINARY)


def generated(rec: dict) -> tuple[dict, dict]:
    files = {Path(p).name: f for p, f in rec["files"].items()}
    return files["settings.json"], files["mcp_config.json"]


# ------------------------------------------------------------------ command line


def test_argv_is_headless_stream_json_with_the_safe_flags(world):
    result, _, rec = run(world)
    assert result.status == "succeeded"
    argv = rec["argv"]
    assert argv[0] == "-p" and argv[2:5] == [
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
    ]
    assert "--sandbox" in argv and "--new-project" not in argv
    assert argv[argv.index("--print-timeout") + 1] == "60s"  # the run's timeout
    assert argv[argv.index("--log-file") + 1].endswith("/agy.log")
    assert "--model" not in argv  # no model chosen: the CLI's own default
    assert Path(rec["cwd"]).resolve() == (world.tmp / "workspace").resolve()
    for bad in FORBIDDEN_ARGV:
        assert bad not in argv


def test_the_model_comes_from_the_agent_runtime_config(world):
    world.set_auth(
        "antigravity", mode="api_key", credential_id=world.credential(), model="gemini-x"
    )
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK)
    world.set_task_status("done")
    adapter_of(world).execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, BINARY)["argv"]
    assert argv[argv.index("--model") + 1] == "gemini-x"


def test_the_forbidden_flag_guard_covers_antigravity():
    for bad in ("--dangerously-skip-permissions", "--yolo"):
        with pytest.raises(ValueError):
            assert_safe_argv(["-p", bad])
    with pytest.raises(ValueError):  # the settings.json preset that auto-approves every tool call
        assert_safe_argv(['{"toolPermission": "always-proceed"}'])


# ------------------------------------------------------------------ isolation, settings, secrets


def test_home_is_the_isolated_agent_home_and_the_key_is_only_in_the_environment(world):
    sink_result, sink, rec = run(world)
    env = rec["env"]
    data_dir = world.app.state.settings.resolved_data_dir
    assert env["HOME"] == str(data_dir / "agents" / world.agent["id"] / "cli-home")
    assert env["GEMINI_API_KEY"] == API_KEY and env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"
    assert not [k for k in env if k.startswith("COMPANY_OS_")]
    assert RUN_TOKEN not in str(env) and RUN_TOKEN not in " ".join(rec["argv"])
    assert API_KEY not in " ".join(rec["argv"])
    assert all(API_KEY not in f["text"] for f in rec["files"].values())
    assert API_KEY not in str(sink.events) and RUN_TOKEN not in str(sink.events)


def test_settings_json_is_api_key_mode_with_only_workspace_files_and_company_tools(world):
    _, _, rec = run(world)
    settings_file, _ = generated(rec)
    assert settings_file["mode"] == 0o600
    settings = json.loads(settings_file["text"])
    ws = str((world.tmp / "workspace").resolve())
    assert settings["modelProvider"] == "gemini"
    assert settings["toolPermission"] == "strict"
    assert settings["allowNonWorkspaceAccess"] is False
    assert settings["enableTerminalSandbox"] is True and settings["disableSlashCommands"] is True
    perms = settings["permissions"]
    assert perms["ask"] == []
    assert "mcp(company/*)" in perms["allow"]
    assert any(r.startswith("write_file(") and ws in r for r in perms["allow"])
    for rule in ("command(*)", "read_url(*)", "execute_url(*)"):
        assert rule in perms["deny"]
    # the agent cannot rewrite its own settings or MCP servers, nor read the run token
    assert any(r.startswith("write_file(") and r.endswith(".gemini/)") for r in perms["deny"])
    assert any(".agents/" in r for r in perms["deny"])
    assert any(r.startswith("read_file(") and "cli-run" in r for r in perms["deny"])
    assert "always-proceed" not in settings_file["text"]


def test_mcp_config_launches_only_the_company_server_and_the_token_stays_in_its_file(world):
    _, _, rec = run(world)
    _, mcp_file = generated(rec)
    assert mcp_file["mode"] == 0o600
    servers = json.loads(mcp_file["text"])["mcpServers"]
    assert set(servers) == {"company"}
    server = servers["company"]
    assert server["args"] == ["-m", "company_agent.mcp_server"]
    assert server["env"]["COMPANY_OS_API_URL"] == "http://127.0.0.1:8700/api/agent/v1"
    assert server["env"]["COMPANY_OS_TASK_ID"] == world.task["id"]
    assert RUN_TOKEN not in mcp_file["text"]
    assert server["env"]["COMPANY_OS_MCP_TOOLS"].split(",") == [
        "get_context",
        "report_progress",
        "submit_artifact",
        "complete_task",
        "update_task",
        "request_help",
        "find_colleagues",
        "request_purchase",
        "record_decision",  # M18
        "ask_founder",
        "request_human_work",  # M22: a person-only step (the server limits it)
    ]
    [(token_path, token)] = rec["token_files"].items()
    assert token["text"] == RUN_TOKEN and token["mode"] == 0o600
    assert str(world.tmp / "workspace") not in token_path and not Path(token_path).exists()


def test_the_files_are_regenerated_on_every_run_and_a_workspace_mcp_config_is_removed(world):
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK)
    configure(world)
    world.set_task_status("done")
    home = world.app.state.settings.resolved_data_dir / "agents" / world.agent["id"] / "cli-home"
    settings_path = home / ".gemini" / "antigravity-cli" / "settings.json"
    mcp_path = home / ".gemini" / "config" / "mcp_config.json"
    settings_path.parent.mkdir(parents=True)
    mcp_path.parent.mkdir(parents=True)
    settings_path.write_text('{"toolPermission": "always-proceed"}')  # a stale/hostile file
    mcp_path.write_text('{"mcpServers": {"evil": {"command": "sh"}}}')
    ws = world.tmp / "workspace"
    (ws / ".agents").mkdir(parents=True)
    (ws / ".agents" / "mcp_config.json").write_text('{"mcpServers": {"evil": {"command": "sh"}}}')
    adapter_of(world).execute(world.request(), Sink(), CancelToken())
    assert "always-proceed" not in settings_path.read_text()
    assert "evil" not in mcp_path.read_text() and "company" in mcp_path.read_text()
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert not (ws / ".agents" / "mcp_config.json").exists()


def test_the_connectivity_test_has_no_tools_and_no_mcp_server(world):
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK)
    configure(world)
    adapter = adapter_of(world)
    with world.app.state.session_factory() as db:
        prep, why = adapter.prepare_test(db, store.load(db, "antigravity"))
    assert prep is not None, why
    invocation = adapter.build(prep)
    scan = [a for i, a in enumerate(invocation.argv) if i != invocation.prompt_index]
    assert_safe_argv(scan)
    assert_safe_argv([invocation.config_text])
    settings = json.loads(invocation.config_text)["permissions"]
    assert (
        settings["allow"] == []
        and "mcp(*)" in settings["deny"]
        and "write_file(*)" in settings["deny"]
    )
    mcp = json.loads((prep.home / ".gemini" / "config" / "mcp_config.json").read_text())
    assert mcp == {"mcpServers": {}}
    assert not (prep.run_dir / "run-token").exists()


# ------------------------------------------------------------------ stream parsing


def test_a_completed_run_streams_events_and_reports_tokens(world):
    result, sink, _ = run(world)
    assert result.status == "succeeded" and result.error is None
    types = sink.types
    assert types[0] == "run.started" and types[-1] == "run.completed"
    started = [e.payload for e in sink.events if e.type == "tool.started"]
    completed = [e.payload for e in sink.events if e.type == "tool.completed"]
    assert started[0]["name"] == "submit_artifact" and completed[0]["is_error"] is False
    messages = [e.payload["text"] for e in sink.events if e.type == "message"]
    assert messages == ["All done, the brief is saved."]  # text deltas assembled per step
    seqs = [e.seq for e in sink.events]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1
    usage = result.usage
    assert usage is not None
    assert usage.input_tokens == 900  # input + cache_read
    assert usage.output_tokens == 210  # output + thinking
    assert usage.cost_kind.value == "unknown" and usage.cost_micros is None  # no cost reported


def test_a_failed_tool_step_is_an_error_completion(world):
    lines = [
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "error": {"type": "permission_denied", "message": "denied by rule"},
                },
            },
        }
    ]
    _, sink, _ = run(world, lines, done=False)
    names = [(e.type, e.payload.get("is_error")) for e in sink.events if e.type.startswith("tool.")]
    assert names == [("tool.started", None), ("tool.completed", True)]
    done = next(e.payload for e in sink.events if e.type == "tool.completed")
    assert "denied by rule" in done["result_preview"]


@pytest.mark.parametrize("status", ["ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING"])
def test_a_result_that_is_not_success_fails_the_run(world, status):
    lines = [
        {
            "event": "result",
            "result": {
                "status": status,
                "error": f"agy says {status}" if status == "ERROR" else "",
            },
        }
    ]
    result, sink, _ = run(world, lines, done=False, exit=1 if status == "ERROR" else 0)
    assert result.status == "failed" and sink.types[-1] == "run.failed"
    assert "Antigravity CLI reported an error" in result.error.message
    assert (f"agy says {status}" if status == "ERROR" else status) in result.error.message


def test_a_crash_with_nothing_on_stdout_is_a_readable_failure_without_the_key(world):
    result, _, _ = run(world, [], done=False, exit=1, stderr=f"auth required {API_KEY}")
    assert result.status == "failed" and "code 1" in result.error.message
    assert "auth required" in result.error.message and API_KEY not in result.error.message


def test_malformed_lines_are_dropped_not_fatal(world):
    result, sink, _ = run(
        world, ["not json", "[1, 2]", "", {"event": "step_update"}, *ANTIGRAVITY_OK]
    )
    assert result.status == "succeeded" and "usage" in sink.types


def test_a_cli_that_never_completes_the_task_keeps_its_files_as_artifacts_and_fails(world):
    result, _, _ = run(
        world,
        done=False,
        write_files=[
            {"path": "brief.md", "content": "# Brief"},
            {"path": ".agents/skill.md", "content": "not an artifact"},
        ],
    )
    assert result.status == "failed" and result.error.retryable
    assert "task_not_completed" in result.error.message
    titles = {a.title for a in result.artifacts}
    assert {"brief.md", "final-message.md"} <= titles and ".agents/skill.md" not in titles


def test_cancel_stops_the_process_group_and_reports_cancelled(world):
    install_fake_cli(world.bin_dir, BINARY, [{"event": "init"}], hang=True, ignore_term=True)
    configure(world)
    cancel = CancelToken()
    sink = Sink()
    threading.Timer(0.6, cancel.set).start()
    started = time.monotonic()
    adapter = adapter_of(world)
    adapter._settings.runtime_kill_grace_seconds = 0.5
    result = adapter.execute(world.request(), sink, cancel)
    assert result.status == "cancelled" and sink.types[-1] == "run.cancelled"
    assert time.monotonic() - started < 10


# ------------------------------------------------------------------ catalog, API, routing


def test_own_login_is_not_offered_and_the_adapter_refuses_it(world):
    spec = CLIS["antigravity"]
    assert spec.auth_modes == ("api_key",) and spec.key_env == "GEMINI_API_KEY"
    assert spec.binaries == ("agy",) and spec.runtime_id == "cli:antigravity"
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK)
    r = world.client.put("/api/cli-runtimes/antigravity/auth", json={"mode": "own_login"})
    assert r.status_code == 422 and "api_key" in json.dumps(r.json())
    # even if a stored state said own_login, nothing is written into the real home
    adapter = adapter_of(world)
    with world.app.state.session_factory() as db:
        store.save(db, "antigravity", auth_mode="own_login")
        db.commit()
    realhome = world.tmp / "realhome"
    with pytest.raises(RuntimeAdapterError):
        adapter.execute(world.request(), Sink(), CancelToken())
    assert not (realhome / ".gemini").exists()


def test_sandbox_is_honestly_none():
    box = sandbox_for("antigravity", True)
    assert box["available"] is False and box["kind"] == "none" and "command(*)" in box["detail"]


def test_status_lists_antigravity_with_a_script_install_method(world):
    items = {i["id"]: i for i in world.client.get("/api/cli-runtimes").json()["items"]}
    agy = items["antigravity"]
    assert agy["display_name"] == "Antigravity CLI" and agy["installed"] is False
    assert agy["auth_modes"] == ["api_key"]
    inst = agy["install"]
    assert inst["manual"] == "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    assert inst["requires"] == [] and inst["status"] == "none"
    assert [m["method"] for m in inst["methods"]] == ["script"]
    assert inst["methods"][0]["url"] == "https://antigravity.google/cli/install.sh"
    assert any("never started with a prompt" in w for w in agy["warnings"])
    # the web tile shows `warnings`, so the manual command is repeated there while it is missing
    assert any("install.sh | bash" in w for w in agy["warnings"])
    install_fake_cli(world.bin_dir, BINARY, [])
    agy = world.client.get("/api/cli-runtimes/antigravity").json()
    assert agy["installed"] and agy["version"] == "1.3.3"
    assert not any("install.sh" in w for w in agy["warnings"])
    assert agy["binary_path"] == str(world.bin_dir / "agy")


def test_the_install_endpoint_never_pipes_the_installer_and_npm_is_not_used(world):
    # a working npm is present: it must still not be used for Antigravity
    from tests.m10_helpers import write_script

    write_script(world.bin_dir, "npm", "import sys\nsys.exit(0)\n")
    r = world.client.post(
        "/api/cli-runtimes/antigravity/install", json={"confirm": True, "method": "npm"}
    )
    assert r.status_code == 422
    # the default test transport answers 404: nothing is downloaded, nothing runs
    r = world.client.post("/api/cli-runtimes/antigravity/install", json={"confirm": True})
    assert r.status_code == 409 and "installer" in r.json()["error"]["message"]
    assert world.client.get("/api/cli-runtimes/antigravity").json()["install"]["status"] == "none"


def test_a_successful_run_keeps_its_files_but_not_the_cli_configuration(world):
    result, _, _ = run(
        world,
        write_files=[
            {"path": "report.md", "content": "# Report"},
            {"path": ".agents/note.txt", "content": "cli state"},
        ],
    )
    assert result.status == "succeeded"
    assert [a.title for a in result.artifacts] == ["report.md"]


def test_the_prompt_names_the_project_repository(world):
    install_fake_cli(world.bin_dir, BINARY, ANTIGRAVITY_OK)
    configure(world)
    world.set_task_status("done")
    request = world.request(workspace_kind="project", workspace_files=["README.md"])
    adapter_of(world).execute(request, Sink(), CancelToken())
    prompt = next(a for a in record_of(world.bin_dir, BINARY)["argv"] if a.startswith("You are"))
    assert "PROJECT REPOSITORY" in prompt and "README.md" in prompt
