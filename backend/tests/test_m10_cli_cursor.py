"""M10/B7: the Cursor CLI (``agent``) adapter against a fake binary printing the documented
stream-json shape. The Cursor CLI is not installed on this machine and is never run here; the fake is a
Python script (tests/m10_helpers.py) that records argv, environment and the generated files under the
child's ``HOME``."""

from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

import pytest

from app.runtimes.base import CancelToken, RuntimeAdapterError
from app.runtimes.cli import store
from app.runtimes.cli.catalog import CLIS, FORBIDDEN_ARGV, assert_safe_argv, sandbox_for
from app.runtimes.cli.cursor import CursorAdapter
from tests.m10_helpers import (
    CURSOR_OK,
    install_fake_cli,
    record_of,
)
from tests.test_m10_cli_adapters import API_KEY, RUN_TOKEN, Sink, World
from tests.test_m10_cli_adapters import world as world  # noqa: F401  (the shared fixture)

BINARY = "agent"


def adapter_of(world: World) -> CursorAdapter:
    a = world.app.state
    return CursorAdapter(a.settings, a.session_factory, a.keys, a.cli_env)


def configure(world: World) -> None:
    world.set_auth("cursor", mode="api_key", credential_id=world.credential())


def run(world: World, lines=None, *, done: bool = True, **options):
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK if lines is None else lines, **options)
    configure(world)
    if done:
        world.set_task_status("done")  # the CLI called complete_task through the company tools
    sink = Sink()
    result = adapter_of(world).execute(world.request(), sink, CancelToken())
    return result, sink, record_of(world.bin_dir, BINARY)


def generated(rec: dict) -> tuple[dict, dict]:
    files = {Path(p).name: f for p, f in rec["files"].items()}
    return files["cli-config.json"], files["mcp.json"]


# ------------------------------------------------------------------ command line


def test_run_argv_is_headless_stream_json_sandboxed_with_force_and_approved_mcps(world):
    result, _, rec = run(world)
    assert result.status == "succeeded"
    argv = rec["argv"]
    ws = str((world.tmp / "workspace").resolve())
    assert argv[:7] == [
        "-p",
        "--output-format",
        "stream-json",
        "--sandbox",
        "enabled",
        "--trust",
        "--workspace",
    ]
    assert Path(argv[7]).resolve() == Path(ws)
    assert argv[8:10] == ["--force", "--approve-mcps"]
    assert argv[-1].startswith("You are")  # the prompt is last
    assert "--model" not in argv  # no model chosen: the CLI's own default
    assert "--api-key" not in argv and "--yolo" not in argv
    assert Path(rec["cwd"]).resolve() == Path(ws)
    for bad in FORBIDDEN_ARGV:
        assert bad not in argv


def test_the_model_comes_from_the_agent_runtime_config(world):
    world.set_auth("cursor", mode="api_key", credential_id=world.credential(), model="gpt-x")
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK)
    world.set_task_status("done")
    adapter_of(world).execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, BINARY)["argv"]
    assert argv[argv.index("--model") + 1] == "gpt-x"


def test_the_forbidden_flag_guard_covers_cursor():
    with pytest.raises(ValueError):
        assert_safe_argv(["-p", "--yolo"])  # Cursor's alias of --force


# ------------------------------------------------------------------ isolation, config, secrets


def test_home_is_the_isolated_agent_home_and_the_key_is_only_in_the_environment(world):
    _, sink, rec = run(world)
    env = rec["env"]
    data_dir = world.app.state.settings.resolved_data_dir
    assert env["HOME"] == str(data_dir / "agents" / world.agent["id"] / "cli-home")
    assert env["CURSOR_API_KEY"] == API_KEY
    assert not [k for k in env if k.startswith("COMPANY_OS_")]
    assert not [k for k in env if k.endswith(("_TOKEN", "_SECRET")) or k.startswith("HERMES")]
    assert RUN_TOKEN not in str(env) and RUN_TOKEN not in " ".join(rec["argv"])
    assert API_KEY not in " ".join(rec["argv"])
    assert all(API_KEY not in f["text"] for f in rec["files"].values())
    assert API_KEY not in str(sink.events) and RUN_TOKEN not in str(sink.events)


def test_cli_config_allows_only_workspace_files_and_company_tools(world):
    _, _, rec = run(world)
    config_file, _ = generated(rec)
    assert config_file["mode"] == 0o600
    perms = json.loads(config_file["text"])["permissions"]
    assert perms["allow"] == ["Read(**)", "Write(**)", "Mcp(company:*)"]
    for rule in ("Shell(*)", "WebFetch(*)", "Write(.cursor/**)"):
        assert rule in perms["deny"]
    # the agent cannot rewrite its own config/MCP servers, nor read the run token
    assert any(r.startswith("Write(") and r.endswith("cli-home/.cursor/**)") for r in perms["deny"])
    assert any(r.startswith("Read(") and "cli-run" in r for r in perms["deny"])
    assert any(r.startswith("Write(") and "cli-run" in r for r in perms["deny"])


def test_mcp_config_launches_only_the_company_server_and_the_token_stays_in_its_file(world):
    _, _, rec = run(world)
    _, mcp_file = generated(rec)
    assert mcp_file["mode"] == 0o600
    servers = json.loads(mcp_file["text"])["mcpServers"]
    assert set(servers) == {"company"}
    server = servers["company"]
    assert server["type"] == "stdio" and server["args"] == ["-m", "company_agent.mcp_server"]
    assert server["env"]["COMPANY_OS_API_URL"] == "http://127.0.0.1:8700/api/agent/v1"
    assert server["env"]["COMPANY_OS_TASK_ID"] == world.task["id"]
    assert RUN_TOKEN not in mcp_file["text"]
    assert "get_context" in server["env"]["COMPANY_OS_MCP_TOOLS"].split(",")
    [(token_path, token)] = rec["token_files"].items()
    assert token["text"] == RUN_TOKEN and token["mode"] == 0o600
    assert str(world.tmp / "workspace") not in token_path and not Path(token_path).exists()


def test_files_are_regenerated_every_run_and_a_scratch_workspace_mcp_config_is_removed(world):
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK)
    configure(world)
    world.set_task_status("done")
    home = world.app.state.settings.resolved_data_dir / "agents" / world.agent["id"] / "cli-home"
    config_path = home / ".cursor" / "cli-config.json"
    mcp_path = home / ".cursor" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"permissions": {"allow": ["Shell(*)"]}}')  # stale/hostile
    mcp_path.write_text('{"mcpServers": {"evil": {"command": "sh"}}}')
    ws = world.tmp / "workspace"
    (ws / ".cursor").mkdir(parents=True)
    (ws / ".cursor" / "mcp.json").write_text('{"mcpServers": {"evil": {"command": "sh"}}}')
    adapter_of(world).execute(world.request(), Sink(), CancelToken())
    assert "Shell(*)" not in json.loads(config_path.read_text())["permissions"]["allow"]
    assert "evil" not in mcp_path.read_text() and "company" in mcp_path.read_text()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert not (ws / ".cursor" / "mcp.json").exists()


def test_a_project_repositorys_own_cursor_config_is_left_alone(world):
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK)
    configure(world)
    world.set_task_status("done")
    ws = world.tmp / "workspace"
    (ws / ".cursor").mkdir(parents=True)
    (ws / ".cursor" / "mcp.json").write_text("{}")
    adapter_of(world).execute(world.request(workspace_kind="project"), Sink(), CancelToken())
    assert (ws / ".cursor" / "mcp.json").exists()


def test_the_connectivity_test_has_no_force_no_tools_and_no_mcp_server(world):
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK)
    configure(world)
    adapter = adapter_of(world)
    with world.app.state.session_factory() as db:
        prep, why = adapter.prepare_test(db, store.load(db, "cursor"))
    assert prep is not None, why
    invocation = adapter.build(prep)
    scan = [a for i, a in enumerate(invocation.argv) if i != invocation.prompt_index]
    assert_safe_argv(scan)
    assert_safe_argv([invocation.config_text])
    assert "--force" not in scan and "--approve-mcps" not in scan and "--sandbox" in scan
    perms = json.loads(invocation.config_text)["permissions"]
    assert perms["allow"] == [] and {"Shell(*)", "Write(**)", "Mcp(*:*)"} <= set(perms["deny"])
    mcp = json.loads((prep.home / ".cursor" / "mcp.json").read_text())
    assert mcp == {"mcpServers": {}}
    assert not (prep.run_dir / "run-token").exists()


# ------------------------------------------------------------------ stream parsing and costs


def test_a_completed_run_streams_events_and_records_no_invented_usage_or_cost(world):
    result, sink, _ = run(world)
    assert result.status == "succeeded" and result.error is None
    types = sink.types
    assert types[0] == "run.started" and types[-1] == "run.completed"
    started = [e.payload for e in sink.events if e.type == "tool.started"]
    completed = [e.payload for e in sink.events if e.type == "tool.completed"]
    assert started[0]["name"] == "submit_artifact" and completed[0]["is_error"] is False
    assert [e.payload["text"] for e in sink.events if e.type == "message"] == [
        "All done, the brief is saved."
    ]
    seqs = [e.seq for e in sink.events]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1
    # Cursor reports neither tokens nor a cost: nothing is invented
    assert result.usage is None


def test_keyed_tool_events_and_a_failed_tool(world):
    lines = [
        {"type": "tool_call", "subtype": "started", "call_id": "r", "tool_call": {"readToolCall": {"args": {"path": "a.md"}}}},
        {"type": "tool_call", "subtype": "completed", "call_id": "r", "tool_call": {"readToolCall": {"args": {"path": "a.md"}, "result": {"error": {"message": "denied by rule"}}}}},
    ]  # fmt: skip
    _, sink, _ = run(world, lines, done=False)
    names = [(e.type, e.payload.get("name")) for e in sink.events if e.type.startswith("tool.")]
    assert names == [("tool.started", "read"), ("tool.completed", "read")]
    done = next(e.payload for e in sink.events if e.type == "tool.completed")
    assert done["is_error"] is True and "denied by rule" in done["result_preview"]


def test_an_error_result_fails_the_run(world):
    lines = [{"type": "result", "subtype": "error", "is_error": True, "result": "rate limited"}]
    result, sink, _ = run(world, lines, done=False, exit=1)
    assert result.status == "failed" and sink.types[-1] == "run.failed"
    assert "Cursor CLI reported an error" in result.error.message
    assert "rate limited" in result.error.message


def test_a_crash_with_nothing_on_stdout_is_a_readable_failure_without_the_key(world):
    result, _, _ = run(world, [], done=False, exit=1, stderr=f"auth required {API_KEY}")
    assert result.status == "failed" and "code 1" in result.error.message
    assert "auth required" in result.error.message and API_KEY not in result.error.message


def test_malformed_lines_and_unknown_events_are_dropped_not_fatal(world):
    result, sink, _ = run(
        world, ["not json", "[1, 2]", "", {"type": "thinking"}, {"type": "assistant"}, *CURSOR_OK]
    )
    assert result.status == "succeeded"
    assert sink.types[-1] == "run.completed"


def test_a_cli_that_never_completes_the_task_keeps_its_files_as_artifacts_and_fails(world):
    result, _, _ = run(
        world,
        done=False,
        write_files=[
            {"path": "brief.md", "content": "# Brief"},
            {"path": ".cursor/rules.md", "content": "not an artifact"},
        ],
    )
    assert result.status == "failed" and result.error.retryable
    assert "task_not_completed" in result.error.message
    titles = {a.title for a in result.artifacts}
    assert {"brief.md", "final-message.md"} <= titles and ".cursor/rules.md" not in titles


def test_cost_kinds_reuse_the_base_billing_and_never_call_an_estimate_a_charge(world):
    from app.models.enums import CostKind
    from app.runtimes.cli.base import Parsed

    adapter = adapter_of(world)
    install_fake_cli(world.bin_dir, BINARY, [])
    configure(world)
    with world.app.state.session_factory() as db:
        prep, _ = adapter.prepare_test(db, store.load(db, "cursor"))
    assert prep is not None
    bill = adapter.billing(prep, Parsed())  # nothing reported
    assert bill["cost_micros"] is None and bill["cost_kind"] == CostKind.UNKNOWN.value
    # Cursor's documented events carry no cost, so the parser never sets one
    from app.runtimes.cli.cursor import CursorParser

    parsed = Parsed()
    parser = CursorParser(prep, parsed)
    for line in CURSOR_OK:
        parser.feed(line)
    assert parsed.cost_usd is None and parsed.finished and parsed.final_text.startswith("All done")


def test_cancel_stops_the_process_group_and_reports_cancelled(world):
    install_fake_cli(world.bin_dir, BINARY, [CURSOR_OK[0]], hang=True, ignore_term=True)
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
    spec = CLIS["cursor"]
    assert spec.auth_modes == ("api_key",) and spec.key_env == "CURSOR_API_KEY"
    assert spec.binaries == ("agent",) and spec.runtime_id == "cli:cursor"
    install_fake_cli(world.bin_dir, BINARY, CURSOR_OK)
    r = world.client.put("/api/cli-runtimes/cursor/auth", json={"mode": "own_login"})
    assert r.status_code == 422 and "api_key" in json.dumps(r.json())
    adapter = adapter_of(world)
    with world.app.state.session_factory() as db:
        store.save(db, "cursor", auth_mode="own_login")
        db.commit()
    with pytest.raises(RuntimeAdapterError):
        adapter.execute(world.request(), Sink(), CancelToken())


def test_sandbox_is_honestly_none():
    box = sandbox_for("cursor", True)
    assert box["available"] is False and box["kind"] == "none" and "Shell(*)" in box["detail"]


def test_status_lists_cursor_with_a_script_install_method(world):
    items = {i["id"]: i for i in world.client.get("/api/cli-runtimes").json()["items"]}
    cur = items["cursor"]
    assert cur["display_name"] == "Cursor CLI" and cur["installed"] is False
    assert cur["auth_modes"] == ["api_key"]
    inst = cur["install"]
    assert inst["manual"] == "curl https://cursor.com/install -fsS | bash"
    assert inst["requires"] == [] and inst["status"] == "none"
    assert [m["method"] for m in inst["methods"]] == ["script"]
    assert inst["methods"][0]["url"] == "https://cursor.com/install"
    assert any("never started" in w or "never run" in w for w in cur["warnings"])
    assert any("cursor.com/install" in w for w in cur["warnings"])
    install_fake_cli(world.bin_dir, BINARY, [])
    cur = world.client.get("/api/cli-runtimes/cursor").json()
    assert cur["installed"] and cur["binary_path"] == str(world.bin_dir / "agent")
    assert not any("cursor.com/install" in w for w in cur["warnings"])


def test_the_install_endpoint_never_pipes_the_installer_and_npm_is_not_used(world):
    from tests.m10_helpers import write_script

    write_script(world.bin_dir, "npm", "import sys\nsys.exit(0)\n")
    # npm is not a Cursor install method
    r = world.client.post(
        "/api/cli-runtimes/cursor/install", json={"confirm": True, "method": "npm"}
    )
    assert r.status_code == 422
    # the default test transport answers 404: nothing is downloaded, nothing runs
    r = world.client.post("/api/cli-runtimes/cursor/install", json={"confirm": True})
    assert r.status_code == 409 and "installer" in r.json()["error"]["message"]
    assert world.client.get("/api/cli-runtimes/cursor").json()["install"]["status"] == "none"


def test_a_successful_run_keeps_its_files_but_not_the_cli_configuration(world):
    result, _, _ = run(
        world,
        write_files=[
            {"path": "report.md", "content": "# Report"},
            {"path": ".cursor/note.txt", "content": "cli state"},
        ],
    )
    assert result.status == "succeeded"
    assert [a.title for a in result.artifacts] == ["report.md"]
