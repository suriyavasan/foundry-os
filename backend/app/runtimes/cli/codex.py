"""Codex CLI adapter (``cli:codex``).

Coded from the official documentation (developers.openai.com/codex, 2026-10-24); **Codex is not
installed on the development machine, so nothing here was run against the real binary** -- it is
tested with a fake binary that emits recorded-format JSONL (docs/cli-runtimes.md)."""

from __future__ import annotations

import json
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


class CodexParser(StreamParser):
    def __init__(self, prep: RunPrep, parsed: Parsed) -> None:
        super().__init__(prep, parsed)
        self._last_error: str | None = None

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = str(obj.get("type") or "")
        out: list[tuple[str, dict[str, Any]]] = []
        item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
        itype = str(item.get("type") or "")
        if kind in ("item.started", "item.completed"):
            started = kind == "item.started"
            if itype == "agent_message" and not started:
                text = str(item.get("text") or "")
                if text.strip():
                    self.parsed.final_text = text
                    out.append(("message", {"text": clip(text, 2000)}))
            elif itype == "command_execution":
                if started:
                    out.append(
                        (
                            "tool.started",
                            {"name": "shell", "command": clip(item.get("command"), 300)},
                        )
                    )
                else:
                    out.append(
                        (
                            "tool.completed",
                            {
                                "name": "shell",
                                "is_error": item.get("exit_code") not in (0, None),
                                "result_preview": clip(item.get("aggregated_output"), 300),
                            },
                        )
                    )
            elif itype == "mcp_tool_call":
                name = str(item.get("tool") or "tool")
                if started:
                    out.append(("tool.started", {"name": name, "server": item.get("server")}))
                else:
                    out.append(
                        (
                            "tool.completed",
                            {
                                "name": name,
                                "is_error": item.get("status") == "failed",
                                "result_preview": clip(
                                    item.get("result") or item.get("error"), 300
                                ),
                            },
                        )
                    )
            elif itype in ("file_change", "web_search"):
                out.append(("tool.started" if started else "tool.completed", {"name": itype}))
        elif kind == "turn.completed":
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}

            def n(key: str) -> int:
                v = usage.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

            self.parsed.input_tokens += n("input_tokens")
            self.parsed.output_tokens += n("output_tokens")
            self.parsed.finished = True
        elif kind == "turn.failed":
            err = obj.get("error")
            msg = err.get("message") if isinstance(err, dict) else err
            self.parsed.error = clip(msg or self._last_error or "the turn failed", 400)
        elif kind == "error":
            self._last_error = clip(obj.get("message"), 400)
        return out


class CodexAdapter(CliAdapter):
    spec = CLIS["codex"]
    parser_class = CodexParser
    tool_prefix = ""
    config_hint = "Install with `npm install -g @openai/codex`; sign in with `codex login`."

    def classify_failure(self, parsed: Parsed) -> str:
        return f"Codex reported an error: {parsed.error}"

    @staticmethod
    def _toml(value: Any) -> str:
        if isinstance(value, dict):
            return "{" + ", ".join(f"{k}={CodexAdapter._toml(v)}" for k, v in value.items()) + "}"
        return json.dumps(value)

    def build(self, prep: RunPrep) -> Invocation:
        last_message = prep.run_dir / "last-message.txt"
        argv = [
            prep.binary,
            "exec",
            "--json",
            "--sandbox",
            "read-only" if prep.mode == "test" else "workspace-write",
            "-C",
            str(prep.workspace),
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "-c",
            'approval_policy="never"',
            "-c",
            "sandbox_workspace_write.network_access=false",
            "-o",
            str(last_message),
        ]
        if prep.model:
            argv += ["-m", prep.model]
        if prep.mode != "test":
            token_file = self.write_token_file(prep)
            command = self.mcp_command(prep)
            argv += [
                "-c",
                f"mcp_servers.company.command={self._toml(command[0])}",
                "-c",
                f"mcp_servers.company.args={self._toml(command[1:])}",
                "-c",
                f"mcp_servers.company.env={self._toml(self.mcp_server_env(prep, token_file))}",
            ]
        argv.append(prep.prompt)
        env = self.child_env(prep)
        if prep.auth_mode == "api_key":
            env["CODEX_API_KEY"] = prep.secret or ""
            codex_home = prep.home / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(codex_home)
        return Invocation(argv=argv, env=env, cwd=prep.workspace, prompt_index=len(argv) - 1)
