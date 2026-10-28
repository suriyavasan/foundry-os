"""Shared helpers for the Milestone 10 tests: a ready AI setup, fake coding-CLI binaries."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from tests.helpers import api
from tests.test_model_providers import FakeOllama, provider_body, use_ollama


def make_ready(app, client, cid: str, *, name: str = "Local Ollama") -> dict:
    """Register a (fake) Ollama, pass its readiness test and select it as the management model."""
    use_ollama(app, FakeOllama(served=32768))
    r = client.post(api(cid, "/model-providers"), json=provider_body(name=name))
    assert r.status_code == 201, r.text
    p = r.json()
    t = client.post(api(cid, f"/model-providers/{p['id']}/test"))
    assert t.status_code == 200 and t.json()["last_test"]["ok"] is True, t.text
    s = client.put(
        api(cid, "/ai-setup"),
        json={
            "management_provider_id": p["id"],
            "management_model": p["default_model"],
            "work_mode": "api",
        },
    )
    assert s.status_code == 200, s.text
    assert s.json()["ready"] is True
    return p


def write_script(directory: Path, name: str, source: str) -> Path:
    """An executable Python script (a stand-in for a CLI binary or for npm)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


VERSIONS = {
    "claude": "2.1.292 (Claude Code)",
    "codex": "codex-cli 0.164.0",
    "opencode": "1.18.3",
    "agy": "1.3.3",
    "agent": "2026.10.28-fake",
}

# What a fake CLI does is described by a JSON "script" next to it: lines to print, an exit code,
# and a record of how it was called (argv + selected environment), so tests can assert on both.
FAKE_CLI = r"""
import json, os, signal, sys, time
here = os.path.dirname(os.path.abspath(__file__))
name = os.path.basename(__file__)
if sys.argv[1:2] == ["--version"]:
    print(%(version)r); sys.exit(0)
script = json.load(open(os.path.join(here, name + ".script.json")))
import re, stat
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": {k: v for k, v in os.environ.items()},
    "files": {},
    "token_files": {},
}
candidates = [a.split("=", 1)[-1] for a in sys.argv[1:]] + [os.environ.get("OPENCODE_CONFIG", "")]
gem = os.path.join(os.environ.get("HOME", ""), ".gemini")
candidates += [os.path.join(gem, "antigravity-cli", "settings.json"), os.path.join(gem, "config", "mcp_config.json")]
cur = os.path.join(os.environ.get("HOME", ""), ".cursor")
candidates += [os.path.join(cur, "cli-config.json"), os.path.join(cur, "mcp.json")]
blob = " ".join(sys.argv[1:])
for c in candidates:
    if c and os.path.isfile(c):
        text = open(c).read(20000)
        record["files"][c] = {"mode": stat.S_IMODE(os.stat(c).st_mode), "text": text}
        blob += " " + text
for m in re.finditer(r'COMPANY_OS_RUN_TOKEN_FILE"?\s*[=:]\s*"([^"]+)"', blob):
    path = m.group(1)
    if os.path.isfile(path):
        record["token_files"][path] = {
            "mode": stat.S_IMODE(os.stat(path).st_mode),
            "text": open(path).read(500),
        }
open(os.path.join(here, name + ".record.json"), "w").write(json.dumps(record))
for write in script.get("write_files", []):
    path = os.path.join(os.getcwd(), write["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(write["content"])
if script.get("hang"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN if script.get("ignore_term") else signal.SIG_DFL)
    print(json.dumps(script.get("lines", [{}])[0]), flush=True)
    time.sleep(60)
for line in script.get("lines", []):
    print(line if isinstance(line, str) else json.dumps(line), flush=True)
    time.sleep(script.get("delay", 0))
if script.get("stderr"):
    print(script["stderr"], file=sys.stderr, flush=True)
sys.exit(script.get("exit", 0))
"""


def install_fake_cli(directory: Path, binary: str, lines: list, **options) -> Path:
    """A fake ``claude`` / ``codex`` / ``opencode`` that prints ``lines`` (dicts or raw strings)."""
    script = write_script(directory, binary, FAKE_CLI % {"version": VERSIONS[binary]})
    (directory / f"{binary}.script.json").write_text(json.dumps({"lines": lines, **options}))
    return script


def record_of(directory: Path, binary: str) -> dict:
    return json.loads((directory / f"{binary}.record.json").read_text())


# Recorded-format streams (shapes from each CLI's documentation / a live run; docs/cli-runtimes.md).
CLAUDE_OK = [
    {
        "type": "system",
        "subtype": "init",
        "session_id": "s1",
        "tools": ["Read", "mcp__company__complete_task"],
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "I will write the brief."},
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "mcp__company__submit_artifact",
                    "input": {"title": "Brief"},
                },
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}
            ]
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Done: the brief is saved.",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 100},
        "num_turns": 2,
    },
]
CODEX_OK = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "turn.started"},
    {
        "type": "item.started",
        "item": {
            "id": "i1",
            "type": "mcp_tool_call",
            "server": "company",
            "tool": "submit_artifact",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "i1",
            "type": "mcp_tool_call",
            "server": "company",
            "tool": "submit_artifact",
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {"id": "i2", "type": "agent_message", "text": "All done, the brief is saved."},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 900, "cached_input_tokens": 100, "output_tokens": 210},
    },
]
OPENCODE_OK = [
    {"type": "step_start", "sessionID": "s1", "part": {"type": "step-start"}},
    {
        "type": "tool_use",
        "sessionID": "s1",
        "part": {
            "type": "tool",
            "tool": "write",
            "state": {
                "status": "completed",
                "input": {"filePath": "/x/brief.md", "content": "hi"},
                "output": "Wrote file successfully.",
            },
        },
    },
    {
        "type": "step_finish",
        "part": {
            "reason": "tool-calls",
            "tokens": {
                "total": 5042,
                "input": 5000,
                "output": 37,
                "reasoning": 0,
                "cache": {"write": 0, "read": 5},
            },
            "cost": 0,
        },
    },
    {"type": "text", "part": {"type": "text", "text": "done"}},
    {
        "type": "step_finish",
        "part": {
            "reason": "stop",
            "tokens": {
                "total": 61,
                "input": 41,
                "output": 2,
                "reasoning": 0,
                "cache": {"write": 0, "read": 18},
            },
            "cost": 0,
        },
    },
]

# Antigravity CLI (``agy --output-format stream-json``): shapes from antigravity.google/docs/cli/headless.
ANTIGRAVITY_OK = [
    {
        "event": "init",
        "conversation_id": "c1",
        "init": {"cwd": "/w", "tools": ["write_to_file"], "permission_mode": "strict"},
    },
    {
        "event": "step_update",
        "step_update": {
            "conversation_id": "c1",
            "step_index": 1,
            "state": "ACTIVE",
            "step_type": "tool",
            "tool_name": "call_mcp_tool",
            "tool_info": {
                "name": "call_mcp_tool",
                "parameters": {"ServerName": "company", "ToolName": "submit_artifact"},
            },
        },
    },
    {
        "event": "step_update",
        "step_update": {
            "conversation_id": "c1",
            "step_index": 1,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "call_mcp_tool",
            "tool_info": {
                "name": "call_mcp_tool",
                "parameters": {"ServerName": "company", "ToolName": "submit_artifact"},
                "output": "ok",
            },
        },
    },
    {
        "event": "step_update",
        "step_update": {
            "step_index": 2,
            "state": "ACTIVE",
            "step_type": "agent_response",
            "text_delta": "All done, ",
        },
    },
    {
        "event": "step_update",
        "step_update": {
            "step_index": 2,
            "state": "DONE",
            "step_type": "agent_response",
            "text_delta": "the brief is saved.",
        },
    },
    {
        "event": "result",
        "result": {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "response": "All done, the brief is saved.",
            "duration_seconds": 4.2,
            "num_turns": 1,
            "usage": {
                "input_tokens": 800,
                "output_tokens": 150,
                "thinking_tokens": 60,
                "cache_read_tokens": 100,
                "total_tokens": 1110,
            },
        },
    },
]

# Cursor CLI (``agent -p --output-format stream-json``): shapes from cursor.com/docs/cli/reference/output-format.
CURSOR_OK = [
    {
        "type": "system",
        "subtype": "init",
        "apiKeySource": "env",
        "cwd": "/w",
        "session_id": "s1",
        "model": "Fake",
        "permissionMode": "default",
    },
    {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
        "session_id": "s1",
    },
    {
        "type": "tool_call",
        "subtype": "started",
        "call_id": "c1",
        "tool_call": {
            "function": {"name": "company_submit_artifact", "arguments": '{"title": "Brief"}'}
        },
        "session_id": "s1",
    },
    {
        "type": "tool_call",
        "subtype": "completed",
        "call_id": "c1",
        "tool_call": {
            "function": {"name": "company_submit_artifact", "arguments": "{}"},
            "result": {"success": {"content": "ok"}},
        },
        "session_id": "s1",
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "All done, the brief is saved."}],
        },
        "session_id": "s1",
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1500,
        "duration_api_ms": 1500,
        "result": "All done, the brief is saved.",
        "session_id": "s1",
    },
]


def register_cli_adapters(app) -> None:
    """The test app's registry only holds the scripted runtime: add the four CLI adapters."""
    from app.runtimes.cli.antigravity import AntigravityAdapter
    from app.runtimes.cli.claude_code import ClaudeCodeAdapter
    from app.runtimes.cli.codex import CodexAdapter
    from app.runtimes.cli.cursor import CursorAdapter
    from app.runtimes.cli.opencode import OpenCodeAdapter

    for cls in (
        ClaudeCodeAdapter,
        CodexAdapter,
        OpenCodeAdapter,
        AntigravityAdapter,
        CursorAdapter,
    ):
        adapter = cls(
            app.state.settings, app.state.session_factory, app.state.keys, app.state.cli_env
        )
        if app.state.runtimes.get(adapter.id) is None:
            app.state.runtimes.register(adapter)
