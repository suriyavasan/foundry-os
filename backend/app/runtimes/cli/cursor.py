"""Cursor CLI adapter (``cli:cursor``, binary ``agent``).

Coded from the official Cursor CLI documentation (cursor.com/docs/cli/overview, /cli/reference/parameters,
/cli/reference/output-format, /cli/reference/authentication, /cli/reference/permissions, /cli/headless,
/cli/mcp and /docs/context/mcp, all read 2026-10-28). **The Cursor CLI is not installed on the
development machine, so nothing here was ever run against the real binary** -- the adapter is tested
with a fake binary that prints the documented ``stream-json`` shapes (docs/cli-runtimes.md lists what is
unverified and which gaps the documentation leaves).

Only ``api_key`` mode exists. The documentation says where the CLI reads MCP servers (``~/.cursor/mcp.json``)
and permissions (``~/.cursor/cli-config.json``) but not where a browser login is stored, and it names no
variable that relocates the config directory. An own-login run would therefore need the company MCP
server written into the person's real ``~/.cursor``, and Foundry AI OS never touches a CLI's own
configuration or credentials. With a Cursor API key (``CURSOR_API_KEY``, child environment only) the child
gets an isolated ``HOME`` and both files are generated there, fresh, on every run.

Safety: print mode only *proposes* changes unless ``--force`` is given, and the company MCP server needs
``--approve-mcps``. Run mode therefore passes ``--force`` -- but only together with ``--sandbox enabled``
and permission rules that deny every shell command and web fetch and allow only workspace files and the
company tools (the documentation states that explicit deny rules still apply under ``--force``). The
alias ``--yolo`` is forbidden by the framework's argv guard. The connectivity test passes neither."""

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


def _text_of(message: Any) -> str:
    """Join the ``text`` parts of ``message.content`` (documented: an array of ``{type: text, text}``)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(p.get("text") or "")
        for p in content
        if isinstance(p, dict) and p.get("type", "text") == "text"
    )


class CursorParser(StreamParser):
    """``--output-format stream-json``: NDJSON ``system`` / ``user`` / ``assistant`` / ``tool_call`` /
    ``result`` events. Unknown event types and fields are ignored (the documentation says to)."""

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = obj.get("type")
        out: list[tuple[str, dict[str, Any]]] = []
        if kind == "assistant":
            text = _text_of(obj.get("message"))
            if text.strip():
                out.append(("message", {"text": clip(text, 2000)}))
        elif kind == "tool_call":
            name, preview = self._tool(obj.get("tool_call"))
            subtype = obj.get("subtype")
            if subtype == "started":
                started: dict[str, Any] = {"name": name}
                if preview:
                    started["input_preview"] = preview
                out.append(("tool.started", started))
            elif subtype == "completed":
                out.append(("tool.completed", self._completed(name, obj.get("tool_call"))))
        elif kind == "result":
            text = obj.get("result")
            if isinstance(text, str) and text.strip():
                self.parsed.final_text = text
            if obj.get("subtype") == "success" and not obj.get("is_error"):
                self.parsed.finished = True
            else:
                self.parsed.error = clip(
                    text
                    if obj.get("is_error") and isinstance(text, str) and text.strip()
                    else f"the run ended with {obj.get('subtype') or 'an error'}",
                    400,
                )
        return out

    @staticmethod
    def _tool(call: Any) -> tuple[str, str]:
        """Tool events are keyed by tool type (``readToolCall``) or carry ``function.{name,arguments}``."""
        if not isinstance(call, dict) or not call:
            return "tool", ""
        fn = call.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            name, args = str(fn["name"]), fn.get("arguments")
        else:
            key = next(iter(call))
            inner = call[key] if isinstance(call[key], dict) else {}
            name, args = key.removesuffix("ToolCall"), inner.get("args")
        for prefix in ("mcp_company_", "company_", "company-", "company:"):
            name = name.removeprefix(prefix)
        preview = clip(args if isinstance(args, str) else json.dumps(args, default=str), 300)
        return name, preview if args else ""

    @staticmethod
    def _completed(name: str, call: Any) -> dict[str, Any]:
        result: Any = None
        if isinstance(call, dict):
            for value in call.values():
                if isinstance(value, dict) and "result" in value:
                    result = value["result"]
        failed = isinstance(result, dict) and any(
            k in result for k in ("error", "rejected", "failure")
        )
        detail = result
        if isinstance(result, dict):
            detail = result.get("error") or result.get("success") or result.get("rejected")
        return {
            "name": name,
            "is_error": failed,
            "result_preview": clip(
                detail if isinstance(detail, str) else json.dumps(detail, default=str), 300
            )
            if detail
            else "",
        }


class CursorAdapter(CliAdapter):
    spec = CLIS["cursor"]
    parser_class = CursorParser
    tool_prefix = ""  # MCP tools are addressed as server "company" + tool name
    config_hint = (
        "Install it yourself with Cursor's installer (see docs/cli-runtimes.md); it signs in with a "
        "Cursor API key (Dashboard > API Keys) stored as a credential."
    )

    def classify_failure(self, parsed: Parsed) -> str:
        return f"Cursor CLI reported an error: {parsed.error}"

    # ------------------------------------------------------------ generated files

    @staticmethod
    def _write_private(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)

    def permissions(self, prep: RunPrep) -> dict[str, Any]:
        """``cli-config.json``: deny wins over allow (documented). No shell, no web, nothing outside the
        workspace and the company tools; the agent cannot rewrite its configuration or read the run token."""
        cursor_home = prep.home / ".cursor"
        deny = ["Shell(*)", "WebFetch(*)"]
        if prep.mode == "test":
            return {"allow": [], "deny": [*deny, "Write(**)", "Mcp(*:*)"]}
        deny += [
            f"Write({cursor_home}/**)",
            "Write(.cursor/**)",
            f"Read({prep.run_dir}/**)",
            f"Write({prep.run_dir}/**)",
        ]
        return {"allow": ["Read(**)", "Write(**)", f"Mcp({SERVER}:*)"], "deny": deny}

    def mcp_config(self, prep: RunPrep, token_file: Path | None) -> dict[str, Any]:
        if token_file is None:
            return {"mcpServers": {}}
        command = self.mcp_command(prep)
        return {
            "mcpServers": {
                SERVER: {
                    "type": "stdio",
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
                "Cursor CLI runs only with a Cursor API key (Settings > AI): Foundry AI OS "
                "never writes into your own ~/.cursor",
            )
        home = prep.home / ".cursor"
        run = prep.mode != "test"
        token_file = self.write_token_file(prep) if run else None
        config_text = json.dumps({"permissions": self.permissions(prep)}, indent=2)
        self._write_private(home / "cli-config.json", config_text)
        self._write_private(
            home / "mcp.json", json.dumps(self.mcp_config(prep, token_file), indent=2)
        )
        if run and prep.workspace_kind != "project":
            # Project-level config outranks the global one; a scratch workspace may hold a stale or
            # agent-written MCP server that --approve-mcps would start. (A real project repository's
            # own .cursor/mcp.json is the person's, so it is left alone: a documented gap.)
            (prep.workspace / ".cursor" / "mcp.json").unlink(missing_ok=True)
        argv = [
            prep.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--sandbox",
            "enabled",
            "--trust",
            "--workspace",
            str(prep.workspace),
        ]
        if run:
            argv += ["--force", "--approve-mcps"]
        if prep.model:
            argv += ["--model", prep.model]
        argv.append(prep.prompt)
        env = self.child_env(prep)
        env["CURSOR_API_KEY"] = prep.secret
        return Invocation(
            argv=argv,
            env=env,
            cwd=prep.workspace,
            prompt_index=len(argv) - 1,
            config_text=config_text,
        )
