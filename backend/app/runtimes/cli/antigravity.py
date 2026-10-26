"""Google Antigravity CLI adapter (``cli:antigravity``, binary ``agy``).

Flags verified against ``agy --help`` 1.3.3 on 2026-10-24; the stream-json event shapes, the
``settings.json`` keys, the permission rule syntax and the MCP config file come from the official
documentation (antigravity.google/docs/cli/headless, /cli/reference, /docs/permissions, /docs/mcp),
read the same day. **``agy`` was never started with a prompt during development** (it would use the
owner's Google login): the adapter is tested against a fake binary printing the documented stream
(docs/cli-runtimes.md lists what is verified and what is not).

Only ``api_key`` mode exists. ``agy`` reads its settings from ``$HOME/.gemini/antigravity-cli/`` and its
global MCP servers from ``$HOME/.gemini/config/mcp_config.json``; an own-login run would need the
company MCP server written into the person's real ``~/.gemini``, and Foundry AI OS never touches a CLI's
own configuration or credentials. With a Gemini API key the child gets an isolated ``HOME`` and both
files are generated there, fresh, on every run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.runtimes.base import RuntimeAdapterError, RuntimeErrorKind
from app.runtimes.cli.base import (
    CliAdapter,
    Invocation,
    Parsed,
    RunPrep,
    StreamParser,
    clip,
)
from app.runtimes.cli.catalog import CLIS

SERVER = "company"
# Permission rules (official docs: ``action(target)``; precedence deny > ask > allow; an "ask" cannot be
# answered in print mode, so anything not allowed below is refused).
DENIED_EVERYWHERE = ("command(*)", "read_url(*)", "execute_url(*)")
# agy runs the permission preset ``strict`` (the most restrictive name in its documented list
# request-review | proceed-in-sandbox | always-proceed | strict). ``always-proceed`` is forbidden.
TOOL_PERMISSION = "strict"
# MCP tools reach the model through one generic tool; its arguments name the real tool (unverified).
GENERIC_MCP_TOOL = "call_mcp_tool"


def _dir_rule(path: Path) -> str:
    return str(path).rstrip("/") + "/"


class AntigravityParser(StreamParser):
    """``--output-format stream-json``: NDJSON ``{"event": "init"|"step_update"|"result", <event>: {...}}``."""

    def __init__(self, prep: RunPrep, parsed: Parsed) -> None:
        super().__init__(prep, parsed)
        self._text: dict[Any, str] = {}
        self._started: set[Any] = set()

    @staticmethod
    def _payload(obj: dict[str, Any], key: str) -> dict[str, Any]:
        inner = obj.get(key)
        return inner if isinstance(inner, dict) else obj

    @staticmethod
    def _tool_name(su: dict[str, Any], info: dict[str, Any]) -> tuple[str, Any]:
        name = str(info.get("name") or su.get("tool_name") or "tool")
        params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
        if name == GENERIC_MCP_TOOL:
            inner = params.get("ToolName") or params.get("tool_name") or params.get("toolName")
            if inner:
                name = str(inner)
        return name.removeprefix("company_").removeprefix("mcp_company_"), params

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = obj.get("event")
        out: list[tuple[str, dict[str, Any]]] = []
        if kind == "step_update":
            su = self._payload(obj, "step_update")
            idx = su.get("step_index")
            stype = su.get("step_type")
            done = su.get("state") == "DONE"
            if stype == "agent_response":
                delta = su.get("text_delta")
                if isinstance(delta, str):
                    self._text[idx] = self._text.get(idx, "") + delta
                if done:
                    text = self._text.pop(idx, "")
                    if text.strip():
                        self.parsed.final_text = text
                        out.append(("message", {"text": clip(text, 2000)}))
            elif stype == "tool":
                info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
                name, params = self._tool_name(su, info)
                if idx not in self._started:
                    self._started.add(idx)
                    started: dict[str, Any] = {"name": name}
                    if params:
                        started["input_preview"] = clip(json.dumps(params, default=str), 300)
                    out.append(("tool.started", started))
                if done:
                    err = info.get("error")
                    detail = err.get("message") if isinstance(err, dict) else err
                    out.append(
                        (
                            "tool.completed",
                            {
                                "name": name,
                                "is_error": bool(err),
                                "result_preview": clip(detail or info.get("output"), 300),
                            },
                        )
                    )
        elif kind == "result":
            res = self._payload(obj, "result")
            usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}

            def n(key: str) -> int:
                v = usage.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

            self.parsed.input_tokens = n("input_tokens") + n("cache_read_tokens")
            self.parsed.output_tokens = n("output_tokens") + n("thinking_tokens")
            text = res.get("response")
            if isinstance(text, str) and text.strip():
                self.parsed.final_text = text
            status = str(res.get("status") or "").upper()
            if status == "SUCCESS":
                self.parsed.finished = True
            else:
                reason = res.get("error")
                self.parsed.error = clip(
                    reason
                    or f"the run ended with status {status or 'unknown'}"
                    + (" (a tool needed approval)" if status == "WAITING" else ""),
                    400,
                )
        return out


class AntigravityAdapter(CliAdapter):
    spec = CLIS["antigravity"]
    parser_class = AntigravityParser
    tool_prefix = ""  # MCP tools are addressed as server "company" + tool name
    config_hint = (
        "Install it yourself with Google's installer (see docs/cli-runtimes.md); it signs in with a "
        "Gemini API key (Google AI Studio) stored as a credential."
    )

    def classify_failure(self, parsed: Parsed) -> str:
        return f"Antigravity CLI reported an error: {parsed.error}"

    # ------------------------------------------------------------ generated files

    @staticmethod
    def _write_private(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)

    def settings(self, prep: RunPrep) -> dict[str, Any]:
        """``settings.json``: Gemini API-key mode, nothing but workspace files and the company tools."""
        gemini = prep.home / ".gemini"
        deny = list(DENIED_EVERYWHERE)
        if prep.mode == "test":
            allow: list[str] = []
            deny += ["write_file(*)", "mcp(*)"]
        else:
            ws = prep.workspace
            allow = [
                f"read_file({_dir_rule(ws)})",
                f"write_file({_dir_rule(ws)})",
                f"mcp({SERVER}/*)",
            ]
            deny += [
                # the agent must not rewrite its own permissions or MCP servers (the workspace
                # ``.agents/mcp_config.json`` is also removed before every run), nor read the run token
                f"write_file({_dir_rule(gemini)})",
                f"write_file({_dir_rule(ws / '.agents')})",
                f"write_file({_dir_rule(ws / '.gemini')})",
                f"read_file({_dir_rule(prep.run_dir)})",
                f"write_file({_dir_rule(prep.run_dir)})",
            ]
        return {
            "modelProvider": "gemini",
            "toolPermission": TOOL_PERMISSION,
            "enableTerminalSandbox": True,
            "allowNonWorkspaceAccess": False,
            "disableSlashCommands": True,
            "permissions": {"allow": allow, "ask": [], "deny": deny},
        }

    def mcp_config(self, prep: RunPrep, token_file: Path | None) -> dict[str, Any]:
        if token_file is None:
            return {"mcpServers": {}}
        command = self.mcp_command(prep)
        return {
            "mcpServers": {
                SERVER: {
                    "command": command[0],
                    "args": command[1:],
                    "env": self.mcp_server_env(prep, token_file),
                }
            }
        }

    def build(self, prep: RunPrep) -> Invocation:
        if prep.auth_mode != "api_key" or not prep.secret:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIG,
                "Antigravity CLI runs only with a Gemini API key (Settings > AI): Foundry AI OS "
                "never writes into your own ~/.gemini",
            )
        home = prep.home / ".gemini"
        token_file = None if prep.mode == "test" else self.write_token_file(prep)
        if token_file is not None:
            # A previous run may have left an MCP server in the workspace; agy would start it.
            (prep.workspace / ".agents" / "mcp_config.json").unlink(missing_ok=True)
        settings_text = json.dumps(self.settings(prep), indent=2)
        self._write_private(home / "antigravity-cli" / "settings.json", settings_text)
        self._write_private(
            home / "config" / "mcp_config.json",
            json.dumps(self.mcp_config(prep, token_file), indent=2),
        )
        argv = [
            prep.binary,
            "-p",
            prep.prompt,
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--sandbox",
            "--log-file",
            str(prep.run_dir / "agy.log"),
        ]
        if prep.max_run_seconds:
            argv += ["--print-timeout", f"{int(prep.max_run_seconds)}s"]
        if prep.model:
            argv += ["--model", prep.model]
        env = self.child_env(prep)
        env["GEMINI_API_KEY"] = prep.secret
        # The binary contains this variable name; no settings key exists for telemetry (docs).
        env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
        return Invocation(
            argv=argv, env=env, cwd=prep.workspace, prompt_index=2, config_text=settings_text
        )
