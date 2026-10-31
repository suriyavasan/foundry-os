"""Company OS MCP server (COMPANY FORK, milestone 10): the company tools of one run, for coding CLIs.

A coding CLI (Claude Code, Codex, OpenCode) started by the control plane has no company tools of its
own. The adapter writes a per-run MCP configuration that launches ``python -m company_agent.mcp_server``
(stdio transport: one JSON-RPC 2.0 message per line) and the CLI's model calls the same tools an
``company_agent`` employee has -- ``get_context``, ``report_progress``, ``submit_artifact``,
``complete_task``, ``update_task``, ``request_help``, ``find_colleagues`` -- backed by
``tools/company_tools.py`` and therefore by the agent API, which is where every rule is enforced (risk
tiers, rate limits, DLP, roles, budgets). This module is a protocol adapter, not a security boundary.

Hand-written on purpose: the ``mcp`` SDK is not part of Hermes' exact dependency pins (FORK.md), and
the subset a CLI needs (initialize, tools/list, tools/call, ping) is small.

Configuration (environment of THIS process, set by the CLI from the per-run MCP config):

* ``COMPANY_OS_API_URL``         agent API base URL
* ``COMPANY_OS_RUN_TOKEN_FILE``  path of a 0600 file holding the run token (the token is never in
                                 argv or in this environment; the adapter deletes the file after the run)
* ``COMPANY_OS_RUN_ID`` / ``COMPANY_OS_TASK_ID`` / ``COMPANY_OS_WORKSPACE``
* ``COMPANY_OS_MCP_TOOLS``       comma-separated tool names to expose (short names, no ``company_``)
* ``COMPANY_OS_TASK_KIND``       optional

Tool names are exposed without the ``company_`` prefix (the CLI adds its own server prefix).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "company", "version": "1"}
DEFAULT_TOOLS = (
    "get_context",
    "report_progress",
    "submit_artifact",
    "complete_task",
    "update_task",
    "request_help",
    "find_colleagues",
    "request_purchase",
    "ask_founder",
)
PREFIX = "company_"
MAX_MESSAGE_BYTES = 4_000_000


def _short(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def read_token(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def configure_from_env(env: Optional[dict[str, str]] = None) -> list[str]:
    """Install the agent-API connection; return the (short) tool names to expose."""
    from company_agent import context as ctx

    env = os.environ if env is None else env
    url = env.get("COMPANY_OS_API_URL", "")
    token_file = env.get("COMPANY_OS_RUN_TOKEN_FILE", "")
    if not url or not token_file:
        raise SystemExit("COMPANY_OS_API_URL and COMPANY_OS_RUN_TOKEN_FILE are required")
    wanted = [t.strip() for t in env.get("COMPANY_OS_MCP_TOOLS", "").split(",") if t.strip()]
    tools = [_short(t) for t in (wanted or DEFAULT_TOOLS)]
    ctx.configure(
        url,
        read_token(token_file),
        run_id=env.get("COMPANY_OS_RUN_ID", ""),
        task_id=env.get("COMPANY_OS_TASK_ID", ""),
        workspace_dir=env.get("COMPANY_OS_WORKSPACE", ""),
        allowed_tools={PREFIX + t for t in tools},
        task_kind=env.get("COMPANY_OS_TASK_KIND", ""),
    )
    return tools


class Server:
    def __init__(self, tool_names: list[str]) -> None:
        from tools import company_tools as ct

        self._ct = ct
        self._tools: dict[str, tuple[dict[str, Any], Any]] = {}
        for short in tool_names:
            entry = ct._TOOLS.get(PREFIX + short)
            if entry is None:
                continue  # an unknown name is simply not offered
            schema, handler, _emoji = entry
            self._tools[short] = (schema, handler)

    # ------------------------------------------------------------------ protocol

    def tool_list(self) -> list[dict[str, Any]]:
        out = []
        for short, (schema, _handler) in self._tools.items():
            out.append(
                {
                    "name": short,
                    "description": schema.get("description", ""),
                    "inputSchema": schema.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return out

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
        if not isinstance(arguments, dict):
            arguments = {}
        _schema, handler = entry
        try:
            text = handler(arguments)
        except Exception as exc:  # a tool bug must not kill the server (nor leak a traceback)
            return {
                "content": [{"type": "text", "text": f"Tool failed: {type(exc).__name__}"}],
                "isError": True,
            }
        text = text if isinstance(text, str) else json.dumps(text, default=str)
        return {"content": [{"type": "text", "text": text}], "isError": _is_error(text)}

    def handle(self, message: Any) -> Optional[dict[str, Any]]:
        """One JSON-RPC message in, the response (or None for a notification) out."""
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid request")
        mid, method = message.get("id"), message.get("method")
        is_notification = "id" not in message
        if not isinstance(method, str):
            return None if is_notification else _error(mid, -32600, "Invalid request")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _result(
                mid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": self.tool_list()})
        if method == "tools/call":
            return _result(mid, self.call(str(params.get("name") or ""), params.get("arguments")))
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled, ...
        return _error(mid, -32601, f"Method not found: {method}")


def _is_error(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    return isinstance(parsed, dict) and isinstance(parsed.get("error"), str)


def _result(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(server: Server, stdin: Any = None, stdout: Any = None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_MESSAGE_BYTES:
            response: Optional[dict[str, Any]] = _error(None, -32600, "Message too large")
        else:
            try:
                response = server.handle(json.loads(line))
            except ValueError:
                response = _error(None, -32700, "Parse error")
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> int:
    tools = configure_from_env()
    serve(Server(tools))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
