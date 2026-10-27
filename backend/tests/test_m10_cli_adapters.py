"""M10 section 3: each CLI adapter against a fake binary that prints recorded-format JSON streams.

Nothing here starts a real Claude Code / Codex / OpenCode, and nothing uses anyone's login: the fake
binaries are Python scripts (tests/m10_helpers.py) that print a prepared stream and record how they
were called (argv, environment, the generated config files)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import update

from app.models.agent import Agent
from app.models.company import Company
from app.models.work import Task
from app.runtimes.base import (
    AgentSpec,
    CancelToken,
    CompanySpec,
    ExecutionEvent,
    ExecutionRequest,
    Limits,
    TaskSpec,
)
from app.runtimes.cli.catalog import FORBIDDEN_ARGV
from app.runtimes.cli.claude_code import ClaudeCodeAdapter
from app.runtimes.cli.codex import CodexAdapter
from app.runtimes.cli.opencode import OpenCodeAdapter
from tests.helpers import api, new_agent, new_task
from tests.m10_helpers import (
    CLAUDE_OK,
    CODEX_OK,
    OPENCODE_OK,
    install_fake_cli,
    make_ready,
    record_of,
)

RUN_TOKEN = "cos_run.TEST-RUN-TOKEN-0123456789abcdef"
API_KEY = "sk-test-api-key-0123456789abcdef"

ADAPTERS = {
    "claude": ("claude_code", ClaudeCodeAdapter, CLAUDE_OK),
    "codex": ("codex", CodexAdapter, CODEX_OK),
    "opencode": ("opencode", OpenCodeAdapter, OPENCODE_OK),
}


@dataclass
class Sink:
    events: list[ExecutionEvent] = field(default_factory=list)

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]


@dataclass
class World:
    app: object
    client: object
    cid: str
    agent: dict
    task: dict
    bin_dir: Path
    tmp: Path

    def adapter(self, cli: str):
        _, cls, _ = ADAPTERS[cli]
        a = self.app
        return cls(a.state.settings, a.state.session_factory, a.state.keys, a.state.cli_env)

    def set_auth(self, cli_id: str, **body):
        r = self.client.put(f"/api/cli-runtimes/{cli_id}/auth", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def credential(self, value: str = API_KEY) -> str:
        r = self.client.post(
            api(self.cid, "/credentials"),
            json={"name": f"key-{uuid.uuid4().hex[:8]}", "value": value},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def set_task_status(self, status: str) -> None:
        with self.app.state.session_factory() as db:
            db.execute(update(Task).where(Task.id == self.task["id"]).values(status=status))
            db.commit()

    def request(
        self,
        *,
        budget: int | None = 50_000,
        max_run_seconds: int = 60,
        workspace_kind: str = "run_dir",
        workspace_files: list[str] | None = None,
    ) -> ExecutionRequest:
        with self.app.state.session_factory() as db:
            agent = db.get(Agent, self.agent["id"])
            company = db.get(Company, self.cid)
            task = db.get(Task, self.task["id"])
            ws = self.tmp / "workspace"
            ws.mkdir(exist_ok=True)
            return ExecutionRequest(
                run_id="run-1",
                attempt_number=1,
                task=TaskSpec(
                    id=task.id,
                    title=task.title,
                    description="Write a one-page brief.",
                    acceptance_criteria="One page",
                    kind=task.kind,
                    priority=task.priority,
                ),
                agent=AgentSpec(
                    id=agent.id,
                    name=agent.name,
                    role_title=agent.role_title,
                    instructions=None,
                    responsibilities=None,
                    model_provider=None,
                    model_name=None,
                    runtime_config=dict(agent.runtime_config),
                    tool_permissions=[],
                ),
                company=CompanySpec(
                    id=company.id, name=company.name, strategic_goal=None, constraints=[]
                ),
                limits=Limits(max_run_seconds=max_run_seconds, budget_micros=budget),
                run_token=RUN_TOKEN,
                agent_api_url="http://127.0.0.1:8700/api/agent/v1",
                workspace_dir=str(ws),
                workspace_kind=workspace_kind,
                workspace_files=list(workspace_files or []),
            )


@pytest.fixture
def world(app, client, company, tmp_path, monkeypatch) -> World:
    monkeypatch.setenv("HOME", str(tmp_path / "realhome"))
    (tmp_path / "realhome").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    app.state.cli_env.extra_dirs.append(bin_dir)
    cid = company["id"]
    make_ready(app, client, cid)
    agent = new_agent(client, cid, name="Wes", role_title="Writer")
    task = new_task(client, cid, title="Write the brief", assignee_agent_id=agent["id"])
    w = World(app, client, cid, agent, task, bin_dir, tmp_path)
    w.set_task_status("in_progress")
    return w


def all_cli_modes():
    return [
        ("claude", "api_key"),
        ("claude", "own_login"),
        ("codex", "api_key"),
        ("codex", "own_login"),
        ("opencode", "api_key"),
        ("opencode", "own_login"),
    ]


def configure(world: World, cli: str, mode: str) -> None:
    cli_id = ADAPTERS[cli][0]
    if cli == "opencode":
        # OpenCode needs a model: a 'provider/model' name keeps these tests independent of providers
        if mode == "api_key":
            world.set_auth(
                cli_id, mode="api_key", credential_id=world.credential(), model="anthropic/claude-x"
            )
        else:
            world.set_auth(cli_id, mode="own_login", model="anthropic/claude-x")
    elif mode == "api_key":
        world.set_auth(cli_id, mode="api_key", credential_id=world.credential())
    else:
        world.set_auth(cli_id, mode="own_login")


# ------------------------------------------------------------------ success, failure, partial


@pytest.mark.parametrize(("cli", "mode"), all_cli_modes())
def test_a_completed_run_succeeds_and_streams_events(world, cli, mode):
    _, _, lines = ADAPTERS[cli]
    binary = cli  # the executable is named like the key
    install_fake_cli(world.bin_dir, binary, lines)
    configure(world, cli, mode)
    world.set_task_status("done")  # the CLI called complete_task through the company tools
    sink = Sink()
    result = world.adapter(cli).execute(world.request(), sink, CancelToken())
    assert result.status == "succeeded" and result.error is None
    types = sink.types
    assert types[0] == "run.started" and types[-1] == "run.completed"
    assert "tool.started" in types and "message" in types and "usage" in types
    seqs = [e.seq for e in sink.events]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1
    usage = result.usage
    assert usage is not None and (usage.input_tokens or 0) > 0
    if mode == "own_login":
        # a subscription: the price is unknown, nothing is charged against the budgets
        assert usage.cost_kind.value == "unknown" and usage.cost_micros == 0
        assert usage.billing_note == "subscription"
    elif cli == "claude":
        assert usage.cost_kind.value == "estimated" and usage.cost_micros == 12_300  # $0.0123
    else:
        assert usage.cost_kind.value == "unknown" and usage.cost_micros is None  # no cost reported
    # the final usage event carries the same billing information for the mid-run budget checks
    final = [e for e in sink.events if e.type == "usage"][-1].payload
    assert final["billing"]["cost_kind"] == usage.cost_kind.value


def test_claude_events_name_the_tools(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    sink = Sink()
    world.adapter("claude").execute(world.request(), sink, CancelToken())
    started = [e.payload for e in sink.events if e.type == "tool.started"]
    completed = [e.payload for e in sink.events if e.type == "tool.completed"]
    assert started[0]["name"] == "submit_artifact" and completed[0]["is_error"] is False


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_a_cli_that_never_completes_the_task_keeps_its_work_as_artifacts_and_fails(world, cli):
    _, _, lines = ADAPTERS[cli]
    binary = cli  # the executable is named like the key
    install_fake_cli(
        world.bin_dir,
        binary,
        lines,
        write_files=[
            {"path": "brief.md", "content": "# Brief\nhello"},
            {"path": "notes/draft.txt", "content": "draft"},
        ],
    )
    configure(world, cli, "api_key")
    sink = Sink()
    result = world.adapter(cli).execute(world.request(), sink, CancelToken())
    assert result.status == "failed" and result.error is not None and result.error.retryable
    assert "task_not_completed" in result.error.message
    titles = {a.title for a in result.artifacts}
    assert {"brief.md", "notes/draft.txt", "final-message.md"} <= titles
    assert sink.types[-1] == "run.failed"
    brief = next(a for a in result.artifacts if a.title == "brief.md")
    assert brief.content == "# Brief\nhello"


def test_cli_errors_and_exit_codes_become_readable_failures(world):
    err = [
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "result": "",
            "usage": {},
        },
    ]
    install_fake_cli(world.bin_dir, "claude", err, exit=1)
    configure(world, "claude", "api_key")
    result = world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert result.status == "failed" and "budget cap" in result.error.message
    # a crash with nothing on stdout
    install_fake_cli(world.bin_dir, "codex", [], exit=3, stderr=f"boom {API_KEY}")
    configure(world, "codex", "api_key")
    result = world.adapter("codex").execute(world.request(), Sink(), CancelToken())
    assert result.status == "failed" and "code 3" in result.error.message
    assert API_KEY not in result.error.message  # secrets are redacted from stderr tails
    # Codex reports a failed turn
    install_fake_cli(
        world.bin_dir,
        "codex",
        [{"type": "turn.failed", "error": {"message": "model refused"}}],
        exit=1,
    )
    result = world.adapter("codex").execute(world.request(), Sink(), CancelToken())
    assert "model refused" in result.error.message
    # OpenCode reports an error event
    install_fake_cli(
        world.bin_dir,
        "opencode",
        [{"type": "error", "error": {"name": "APIError", "data": {"message": "Unauthorized"}}}],
        exit=1,
    )
    configure(world, "opencode", "api_key")
    result = world.adapter("opencode").execute(world.request(), Sink(), CancelToken())
    assert "Unauthorized" in result.error.message


def test_malformed_and_oversized_lines_are_dropped_not_fatal(world):
    lines = ["not json", "[1, 2]", "", *CLAUDE_OK]
    install_fake_cli(world.bin_dir, "claude", lines)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    sink = Sink()
    result = world.adapter("claude").execute(world.request(), sink, CancelToken())
    assert result.status == "succeeded" and "usage" in sink.types


def test_cancel_stops_the_process_group_and_reports_cancelled(world):
    install_fake_cli(
        world.bin_dir,
        "claude",
        [{"type": "system", "subtype": "init"}],
        hang=True,
        ignore_term=True,
    )
    configure(world, "claude", "api_key")
    cancel = CancelToken()
    sink = Sink()
    threading.Timer(0.6, cancel.set).start()
    started = time.monotonic()
    adapter = world.adapter("claude")
    adapter._settings.runtime_kill_grace_seconds = 0.5  # SIGTERM is ignored: SIGKILL after 0.5 s
    result = adapter.execute(world.request(), sink, cancel)
    assert result.status == "cancelled" and sink.types[-1] == "run.cancelled"
    assert time.monotonic() - started < 10


def test_budget_cap_is_passed_to_claude_and_a_missing_budget_is_not_invented(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    world.adapter("claude").execute(world.request(budget=250_000), Sink(), CancelToken())
    assert "--max-budget-usd=0.2500" in record_of(world.bin_dir, "claude")["argv"]
    world.adapter("claude").execute(world.request(budget=None), Sink(), CancelToken())
    assert not any(
        a.startswith("--max-budget-usd") for a in record_of(world.bin_dir, "claude")["argv"]
    )


# ------------------------------------------------------------------ isolation & secrets


@pytest.mark.parametrize(("cli", "mode"), all_cli_modes())
def test_isolation_environment_and_secret_handling(world, cli, mode, tmp_path):
    _, _, lines = ADAPTERS[cli]
    binary = cli  # the executable is named like the key
    install_fake_cli(world.bin_dir, binary, lines)
    configure(world, cli, mode)
    world.set_task_status("done")
    sink = Sink()
    world.adapter(cli).execute(world.request(), sink, CancelToken())
    rec = record_of(world.bin_dir, binary)
    env, argv = rec["env"], rec["argv"]
    joined = " ".join(argv)
    data_dir = world.app.state.settings.resolved_data_dir
    # --- environment: minimal, no control-plane settings, no owner token
    assert not [k for k in env if k.startswith("COMPANY_OS_")]
    assert RUN_TOKEN not in str(env) and RUN_TOKEN not in joined
    assert "owner" not in " ".join(env).lower().replace("opencode", "")
    # --- the run token only ever lives in a 0600 file outside the workspace
    [(token_path, token)] = rec["token_files"].items()
    assert token["text"] == RUN_TOKEN and token["mode"] == 0o600
    assert str(world.tmp / "workspace") not in token_path
    assert not Path(token_path).exists()  # removed with the per-run directory
    # --- HOME
    if mode == "api_key":
        assert env["HOME"] == str(data_dir / "agents" / world.agent["id"] / "cli-home")
    else:
        assert env["HOME"] == str(world.tmp / "realhome")
    # --- the API key reaches the child's environment only (never argv / generated config files)
    if mode == "api_key" and cli != "opencode":
        key_var = {"claude": "ANTHROPIC_API_KEY", "codex": "CODEX_API_KEY"}[cli]
        assert env[key_var] == API_KEY
    assert API_KEY not in joined
    assert all(API_KEY not in f["text"] for f in rec["files"].values())
    # --- and never into the events the control plane stores
    assert API_KEY not in str(sink.events) and RUN_TOKEN not in str(sink.events)
    # --- cwd is the run workspace
    assert Path(rec["cwd"]).resolve() == (world.tmp / "workspace").resolve()


@pytest.mark.parametrize(("cli", "mode"), all_cli_modes())
def test_dangerous_flags_are_never_built(world, cli, mode):
    """Spec M10: never bypassPermissions, --dangerously-skip-permissions, --auto, danger-full-access."""
    _, _, lines = ADAPTERS[cli]
    binary = cli  # the executable is named like the key
    install_fake_cli(world.bin_dir, binary, lines)
    configure(world, cli, mode)
    world.set_task_status("done")
    world.adapter(cli).execute(world.request(), Sink(), CancelToken())
    rec = record_of(world.bin_dir, binary)
    texts = [
        *rec["argv"][:0],
        *(a for i, a in enumerate(rec["argv"]) if i not in prompt_positions(rec["argv"])),
    ]
    texts += [f["text"] for f in rec["files"].values()]
    blob = "\n".join(texts)
    for bad in FORBIDDEN_ARGV:
        assert bad not in blob, f"{binary} was started with {bad}"
    assert "--auto" not in rec["argv"]


def prompt_positions(argv: list[str]) -> set[int]:
    """The prompt is the one long argument (it may legitimately talk about anything)."""
    longest = max(range(len(argv)), key=lambda i: len(argv[i]))
    return {longest}


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_the_connectivity_test_mode_builds_safe_commands_too(world, cli):
    _, _, lines = ADAPTERS[cli]
    binary = cli
    install_fake_cli(world.bin_dir, binary, lines)
    configure(world, cli, "api_key")
    adapter = world.adapter(cli)
    from app.runtimes.cli import store

    with world.app.state.session_factory() as db:
        prep, why = adapter.prepare_test(db, store.load(db, ADAPTERS[cli][0]))
    assert prep is not None, why
    invocation = adapter.build(prep)
    scan = [a for i, a in enumerate(invocation.argv) if i != invocation.prompt_index]
    for bad in FORBIDDEN_ARGV:
        assert not any(bad in a for a in scan) and bad not in invocation.config_text
    assert "--auto" not in invocation.argv
    # no company tools in the test: no MCP configuration, no token
    assert "mcp" not in invocation.config_text.lower() or cli != "opencode"


def test_claude_is_restricted_to_file_tools_and_the_company_server(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, "claude")["argv"]
    assert argv[0] == "-p" and "--restricted" in argv and "--bare" in argv  # api_key: --bare
    assert "--permission-mode=dontAsk" in argv and "--strict-mcp-config" in argv
    assert "--tools=Read,Write,Edit,Glob,Grep" in argv
    assert any(a.startswith("--allowedTools=mcp__company") for a in argv)
    denied = next(a for a in argv if a.startswith("--disallowedTools="))
    assert {"Bash", "WebFetch", "WebSearch"} <= set(denied.split("=", 1)[1].split(","))
    # own login cannot use --bare (it would never read the OAuth login)
    configure(world, "claude", "own_login")
    world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, "claude")["argv"]
    assert "--bare" not in argv and "--restricted" in argv


def test_claude_mcp_config_launches_the_company_server_with_a_token_file(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    rec = record_of(world.bin_dir, "claude")
    [(path, file)] = [(p, f) for p, f in rec["files"].items() if p.endswith("mcp.json")]
    import json

    server = json.loads(file["text"])["mcpServers"]["company"]
    assert server["args"] == ["-m", "company_agent.mcp_server"]
    assert server["env"]["COMPANY_OS_API_URL"] == "http://127.0.0.1:8700/api/agent/v1"
    assert server["env"]["COMPANY_OS_TASK_ID"] == world.task["id"]
    assert RUN_TOKEN not in file["text"] and file["mode"] == 0o600
    tools = server["env"]["COMPANY_OS_MCP_TOOLS"].split(",")
    assert tools == [
        "get_context",
        "report_progress",
        "submit_artifact",
        "complete_task",
        "update_task",
        "request_help",
        "find_colleagues",
    ]  # nothing that creates tasks, hires or decides approvals


def test_codex_uses_the_workspace_write_sandbox_with_network_off(world):
    install_fake_cli(world.bin_dir, "codex", CODEX_OK)
    configure(world, "codex", "api_key")
    world.set_task_status("done")
    world.adapter("codex").execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, "codex")["argv"]
    assert argv[:3] == ["exec", "--json", "--sandbox"] and argv[3] == "workspace-write"
    for flag in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config"):
        assert flag in argv
    assert "sandbox_workspace_write.network_access=false" in argv
    assert 'approval_policy="never"' in argv
    assert any(a.startswith("mcp_servers.company.command=") for a in argv)
    assert not any(RUN_TOKEN in a for a in argv)


def test_opencode_config_denies_shell_web_and_subagents_and_points_at_the_provider(world):
    install_fake_cli(world.bin_dir, "opencode", OPENCODE_OK)
    configure(world, "opencode", "api_key")
    world.set_task_status("done")
    world.adapter("opencode").execute(world.request(), Sink(), CancelToken())
    rec = record_of(world.bin_dir, "opencode")
    import json

    [(_, file)] = [(p, f) for p, f in rec["files"].items() if p.endswith("opencode.json")]
    config = json.loads(file["text"])
    perm = config["permission"]
    assert perm["bash"] == "deny" and perm["webfetch"] == "deny" and perm["task"] == "deny"
    assert perm["external_directory"] == "deny" and perm["company_*"] == "allow"
    assert config["mcp"]["company"]["type"] == "local" and file["mode"] == 0o600
    argv = rec["argv"]
    assert argv[0] == "run" and "--pure" in argv and argv[argv.index("--format") + 1] == "json"
    assert argv[argv.index("-m") + 1] == "anthropic/claude-x"
    assert rec["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert rec["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_opencode_on_a_local_company_provider_costs_nothing_and_keeps_no_key(world):
    """The live-proof setup: OpenCode pointed at the company's local Ollama provider."""
    install_fake_cli(world.bin_dir, "opencode", OPENCODE_OK)
    providers = world.client.get(api(world.cid, "/model-providers")).json()["items"]
    local = providers[0]
    world.set_auth("opencode", mode="api_key", model_provider_id=local["id"], model="qwen2.5:7b")
    world.set_task_status("done")
    result = world.adapter("opencode").execute(world.request(), Sink(), CancelToken())
    assert result.usage.cost_kind.value == "verified" and result.usage.cost_micros == 0
    import json

    rec = record_of(world.bin_dir, "opencode")
    [(_, file)] = [(p, f) for p, f in rec["files"].items() if p.endswith("opencode.json")]
    config = json.loads(file["text"])
    block = config["provider"]["company"]
    assert block["npm"] == "@ai-sdk/openai-compatible"
    assert block["options"]["baseURL"].endswith("/v1") and block["options"]["apiKey"] == "local"
    assert config["model"] == "company/qwen2.5:7b"
    assert rec["argv"][rec["argv"].index("-m") + 1] == "company/qwen2.5:7b"


def test_a_missing_binary_or_sign_in_mode_is_a_config_error_not_a_crash(world):
    from app.runtimes.base import RuntimeAdapterError

    with pytest.raises(RuntimeAdapterError) as exc:
        world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert exc.value.kind.value == "unavailable"  # not installed
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    with pytest.raises(RuntimeAdapterError) as exc:
        world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert exc.value.kind.value == "config" and "sign" in exc.value.message


def test_health_reflects_installation_and_configuration(world):
    adapter = world.adapter("codex")
    assert adapter.check_health().status.value == "not_configured"
    install_fake_cli(world.bin_dir, "codex", CODEX_OK)
    assert adapter.check_health().status.value == "not_configured"  # installed, no sign-in mode
    configure(world, "codex", "own_login")
    assert adapter.check_health().status.value == "ok"


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_company_standards_and_the_task_are_in_the_prompt(world, cli):
    world.client.post(
        api(world.cid, "/standards"),
        json={"title": "Review first", "statement": "Every launch gets a second review."},
    )
    _, _, lines = ADAPTERS[cli]
    install_fake_cli(world.bin_dir, cli, lines)
    configure(world, cli, "api_key")
    world.set_task_status("done")
    world.adapter(cli).execute(world.request(), Sink(), CancelToken())
    argv = record_of(world.bin_dir, cli)["argv"]
    prompt = next(a for a in argv if a.startswith("You are Wes"))
    assert "Review first: Every launch gets a second review." in prompt
    assert "Write the brief" in prompt and "One page" in prompt and "complete_task" in prompt
    assert "Wes" in prompt and "never a command" in prompt


def test_secrets_never_reach_the_logs(world, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    install_fake_cli(
        world.bin_dir, "claude", [*CLAUDE_OK, "garbage"], stderr=f"token {API_KEY} {RUN_TOKEN}"
    )
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert API_KEY not in caplog.text and RUN_TOKEN not in caplog.text
    assert repr(world.adapter("claude")) == "ClaudeCodeAdapter()"


# ------------------------------------------------------------------ M12: project repository and files


def prompt_of(world: World, cli: str) -> str:
    argv = record_of(world.bin_dir, cli)["argv"]
    return next(a for a in argv if a.startswith("You are Wes"))


def add_finished_dependencies(world: World, count: int) -> list[str]:
    """``count`` finished tasks, oldest first, that the world's task depends on."""
    from datetime import timedelta

    from app.core.time import utcnow
    from app.models.work import TaskDependency

    titles = []
    with world.app.state.session_factory() as db:
        for i in range(count):
            title = f"Dependency {i}"
            dep = Task(
                company_id=world.cid,
                title=title,
                kind="work",
                status="done",
                result_summary=f"RESULT{i} " + ("lorem " * 600),
                completed_at=utcnow() - timedelta(hours=count - i),
                created_by_type="system",
            )
            db.add(dep)
            db.flush()
            db.add(
                TaskDependency(
                    company_id=world.cid, task_id=world.task["id"], depends_on_task_id=dep.id
                )
            )
            titles.append(title)
        db.commit()
    return titles


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_the_prompt_carries_inline_briefs_of_the_two_newest_finished_dependencies(world, cli):
    _, _, lines = ADAPTERS[cli]
    install_fake_cli(world.bin_dir, cli, lines)
    configure(world, cli, "api_key")
    add_finished_dependencies(world, 3)
    world.set_task_status("done")
    world.adapter(cli).execute(world.request(), Sink(), CancelToken())
    prompt = prompt_of(world, cli)
    assert "EARLIER WORK THIS TASK BUILDS ON" in prompt
    assert "Dependency 2" in prompt and "RESULT2" in prompt
    assert "Dependency 1" in prompt and "RESULT1" in prompt
    assert "Dependency 0" not in prompt  # only the two newest
    section = prompt.split("EARLIER WORK THIS TASK BUILDS ON", 1)[1].split("HOW TO WORK", 1)[0]
    parts = [p for p in section.split("\n- ") if "RESULT" in p]
    assert len(parts) == 2 and all(len(p) <= 1500 for p in parts)  # 2 x 1,500 characters


def test_no_dependency_section_without_finished_dependencies(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    prompt = prompt_of(world, "claude")
    assert "EARLIER WORK" not in prompt and "PROJECT REPOSITORY" not in prompt


def test_the_prompt_describes_the_project_worktree_and_the_review_copy(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    files = [f"src/file{i}.py" for i in range(60)]
    world.adapter("claude").execute(
        world.request(workspace_kind="project", workspace_files=files), Sink(), CancelToken()
    )
    prompt = prompt_of(world, "claude")
    assert "PROJECT REPOSITORY" in prompt and "git worktree" in prompt
    assert "Do NOT run git commit" in prompt and "src/file0.py" in prompt
    assert "(+20 more)" in prompt  # the listing is bounded
    world.adapter("claude").execute(
        world.request(workspace_kind="review", workspace_files=["index.html"]),
        Sink(),
        CancelToken(),
    )
    prompt = prompt_of(world, "claude")
    assert "READ-ONLY copy" in prompt and "discarded" in prompt and "index.html" in prompt


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_a_successful_per_run_dir_run_keeps_the_files_it_wrote(world, cli):
    _, _, lines = ADAPTERS[cli]
    install_fake_cli(
        world.bin_dir,
        cli,
        lines,
        write_files=[
            {"path": "site/index.html", "content": "<h1>hi</h1>"},
            {"path": "notes.txt", "content": "n"},
        ],
    )
    configure(world, cli, "api_key")
    world.set_task_status("done")  # it called complete_task but never submit_artifact
    result = world.adapter(cli).execute(world.request(), Sink(), CancelToken())
    assert result.status == "succeeded"
    assert {a.title for a in result.artifacts} == {"site/index.html", "notes.txt"}


def test_files_are_not_duplicated_when_the_run_already_submitted_an_artifact(world):
    install_fake_cli(
        world.bin_dir,
        "claude",
        CLAUDE_OK,
        write_files=[{"path": "report.md", "content": "# r"}],
    )
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    adapter = world.adapter("claude")
    adapter._run_has_artifact = lambda run_id: True  # the run used submit_artifact
    result = adapter.execute(world.request(), Sink(), CancelToken())
    assert result.status == "succeeded" and result.artifacts == []


@pytest.mark.parametrize("kind", ["project", "review"])
def test_a_project_worktree_is_not_snapshotted_by_the_cli_adapter(world, kind):
    """In a project worktree the control plane commits the files ("Changes" artifact, branch)."""
    install_fake_cli(
        world.bin_dir, "claude", CLAUDE_OK, write_files=[{"path": "app.py", "content": "x=1"}]
    )
    configure(world, "claude", "api_key")
    adapter = world.adapter("claude")
    world.set_task_status("done")
    ok = adapter.execute(world.request(workspace_kind=kind), Sink(), CancelToken())
    assert ok.status == "succeeded" and ok.artifacts == []
    world.set_task_status("in_progress")  # the CLI did not complete the task
    failed = adapter.execute(world.request(workspace_kind=kind), Sink(), CancelToken())
    assert failed.status == "failed" and {a.title for a in failed.artifacts} <= {"final-message.md"}


def test_a_git_file_in_the_workspace_is_ignored_by_the_cli_snapshot(world):
    install_fake_cli(
        world.bin_dir, "claude", CLAUDE_OK, write_files=[{"path": "app.py", "content": "x=1"}]
    )
    configure(world, "claude", "api_key")
    ws = world.tmp / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / ".git").write_text("gitdir: /elsewhere/.git/worktrees/run\n")  # what a worktree has
    world.set_task_status("done")
    result = world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert {a.title for a in result.artifacts} == {"app.py"}
