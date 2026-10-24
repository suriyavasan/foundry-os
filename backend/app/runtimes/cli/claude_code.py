"""Claude Code adapter (``cli:claude_code``). Flags verified against ``claude --help`` 2.1.292 on
2026-10-24 (docs/cli-runtimes.md). The prompt is the first positional right after ``-p``; every
variadic option is written as ``--flag=value`` so it can never swallow the prompt."""

from __future__ import annotations

import json
import os
from typing import Any

from app.runtimes.cli.base import (
    CliAdapter,
    Invocation,
    Parsed,
    RunPrep,
    StreamParser,
    clip,
)
from app.runtimes.cli.catalog import CLIS

FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
DENIED_TOOLS = (
    "Bash",
    "BashOutput",
    "KillShell",
    "PowerShell",
    "WebFetch",
    "WebSearch",
    "Task",
    "NotebookEdit",
    "SlashCommand",
)
MCP_PREFIX = "mcp__company__"


class ClaudeParser(StreamParser):
    def __init__(self, prep: RunPrep, parsed: Parsed) -> None:
        super().__init__(prep, parsed)
        self._tool_names: dict[str, str] = {}

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = obj.get("type")
        out: list[tuple[str, dict[str, Any]]] = []
        if kind == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    self.parsed.final_text = str(block["text"])
                    out.append(("message", {"text": clip(block["text"], 2000)}))
                elif block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    if block.get("id"):
                        self._tool_names[str(block["id"])] = name
                    out.append(
                        (
                            "tool.started",
                            {
                                "name": name.removeprefix(MCP_PREFIX),
                                "tool": name,
                                "input_preview": clip(
                                    json.dumps(block.get("input"), default=str), 300
                                ),
                            },
                        )
                    )
        elif kind == "user":
            content = (obj.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = self._tool_names.get(str(block.get("tool_use_id")), "tool")
                    result = block.get("content")
                    if isinstance(result, list):
                        result = " ".join(
                            str(b.get("text", "")) for b in result if isinstance(b, dict)
                        )
                    out.append(
                        (
                            "tool.completed",
                            {
                                "name": name.removeprefix(MCP_PREFIX),
                                "is_error": bool(block.get("is_error")),
                                "result_preview": clip(result, 300),
                            },
                        )
                    )
        elif kind == "result":
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}

            def n(key: str) -> int:
                v = usage.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

            self.parsed.input_tokens = (
                n("input_tokens") + n("cache_read_input_tokens") + n("cache_creation_input_tokens")
            )
            self.parsed.output_tokens = n("output_tokens")
            cost = obj.get("total_cost_usd")
            if isinstance(cost, int | float) and not isinstance(cost, bool) and cost >= 0:
                self.parsed.cost_usd = float(cost)
            text = obj.get("result")
            if isinstance(text, str) and text.strip():
                self.parsed.final_text = text
            if obj.get("is_error") or str(obj.get("subtype") or "success") != "success":
                self.parsed.error = clip(
                    (text if obj.get("is_error") and text else None)
                    or obj.get("subtype")
                    or "error",
                    400,
                )
            else:
                self.parsed.finished = True
        return out


class ClaudeCodeAdapter(CliAdapter):
    spec = CLIS["claude_code"]
    parser_class = ClaudeParser
    tool_prefix = MCP_PREFIX
    config_hint = (
        "Install with `npm install -g @anthropic-ai/claude-code`; sign in with `claude` /login."
    )

    def classify_failure(self, parsed: Parsed) -> str:
        err = parsed.error or ""
        if "max_budget" in err:
            return "Claude Code stopped at its budget cap (--max-budget-usd)"
        return f"Claude Code reported an error: {err}"

    def build(self, prep: RunPrep) -> Invocation:
        argv = [
            prep.binary,
            "-p",
            prep.prompt,
            "--output-format=stream-json",
            "--verbose",
            "--no-session-persistence",
            "--restricted",
            "--disable-slash-commands",
            "--permission-mode=dontAsk",
        ]
        if prep.auth_mode == "api_key":
            argv.append("--bare")  # no hooks, no auto-memory, no CLAUDE.md, API key only
        if prep.budget_usd:
            argv.append(f"--max-budget-usd={max(prep.budget_usd, 0.01):.4f}")
        if prep.model:
            argv.append(f"--model={prep.model}")
        env = self.child_env(prep)
        env["DISABLE_AUTOUPDATER"] = "1"
        env["DISABLE_TELEMETRY"] = "1"
        config_text = ""
        if prep.mode == "test":
            argv += ["--tools="]  # no tools at all
        else:
            token_file = self.write_token_file(prep)
            config = {
                "mcpServers": {
                    "company": {
                        "command": self.mcp_command(prep)[0],
                        "args": self.mcp_command(prep)[1:],
                        "env": self.mcp_server_env(prep, token_file),
                    }
                }
            }
            config_text = json.dumps(config)
            path = prep.run_dir / "mcp.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(config_text)
            argv += [
                f"--mcp-config={path}",
                "--strict-mcp-config",
                "--tools=" + ",".join(FILE_TOOLS),
                "--allowedTools=" + ",".join(("mcp__company", *FILE_TOOLS)),
                "--disallowedTools=" + ",".join(DENIED_TOOLS),
            ]
            config_text = ""  # holds paths and the API url only; nothing to scan
        if prep.auth_mode == "api_key":
            env["ANTHROPIC_API_KEY"] = prep.secret or ""
            env["CLAUDE_CONFIG_DIR"] = str(prep.home / ".claude")
        return Invocation(
            argv=argv, env=env, cwd=prep.workspace, prompt_index=2, config_text=config_text
        )
