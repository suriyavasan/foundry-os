"""The MCP stdio server (milestone 10): JSON-RPC protocol, tool exposure, token handling, secrecy."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from company_agent.tests.conftest import RUNTIME_ROOT
from company_agent.tests.stub_agent_api import StubApi


class McpProc:
    def __init__(self, api: StubApi, tmp_path: Path, *, tools: str = "", extra_env: dict | None = None):
        token_file = tmp_path / "run-token"
        token_file.write_text(api.token)
        token_file.chmod(0o600)
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "HERMES_HOME": str(tmp_path / "hh"),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "PYTHONPATH": str(RUNTIME_ROOT),
            "COMPANY_OS_API_URL": api.url,
            "COMPANY_OS_RUN_TOKEN_FILE": str(token_file),
            "COMPANY_OS_RUN_ID": api.run_id,
            "COMPANY_OS_TASK_ID": api.task_id,
            "COMPANY_OS_WORKSPACE": str(ws),
            **({"COMPANY_OS_MCP_TOOLS": tools} if tools else {}),
            **(extra_env or {}),
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "company_agent.mcp_server"], cwd=tmp_path, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, **({"params": params} if params else {})}
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def notify(self, method: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> str:
        assert self.proc.stdin
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        return self.proc.stderr.read() if self.proc.stderr else ""


@pytest.fixture
def api():
    stub = StubApi().start()
    yield stub
    stub.stop()


def test_initialize_list_and_call(api, tmp_path):
    mcp = McpProc(api, tmp_path)
    init = mcp.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "company" and "tools" in init["result"]["capabilities"]
    mcp.notify("notifications/initialized")  # no response
    listed = mcp.request("tools/list")["result"]["tools"]
    assert [t["name"] for t in listed] == [
        "get_context", "report_progress", "submit_artifact", "complete_task",
        "update_task", "request_help", "find_colleagues", "request_purchase",
    ]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in listed)
    out = mcp.request("tools/call", {"name": "report_progress", "arguments": {"message": "halfway"}})["result"]
    assert out["isError"] is False
    assert json.loads(out["content"][0]["text"])["seq"] >= 1
    args = {"name": "complete_task", "arguments": {"result_summary": "done"}}
    first = mcp.request("tools/call", args)["result"]  # the once-only "no deliverable yet" nudge
    assert first["isError"] is True and "no_deliverable" in first["content"][0]["text"]
    done = mcp.request("tools/call", args)["result"]
    assert done["isError"] is False
    assert [r.path for r in api.requests] == [
        "/api/agent/v1/progress", f"/api/agent/v1/tasks/{api.task_id}/complete",
    ]
    assert all(r.authorization == f"Bearer {api.token}" for r in api.requests)
    mcp.close()


def test_only_the_requested_tools_are_offered_and_others_refused(api, tmp_path):
    mcp = McpProc(api, tmp_path, tools="get_context,company_report_progress")
    mcp.request("initialize", {})
    assert [t["name"] for t in mcp.request("tools/list")["result"]["tools"]] == ["get_context", "report_progress"]
    refused = mcp.request("tools/call", {"name": "create_task", "arguments": {"title": "x"}})["result"]
    assert refused["isError"] is True and "Unknown tool" in refused["content"][0]["text"]
    refused = mcp.request("tools/call", {"name": "decide_approval", "arguments": {}})["result"]
    assert refused["isError"] is True
    assert api.requests == []  # nothing outside the list ever reached the API
    mcp.close()


def test_api_errors_are_tool_errors_not_protocol_errors(api, tmp_path):
    api.forced["/progress"] = (403, {"error": {"code": "forbidden", "message": "nope", "details": None}})
    mcp = McpProc(api, tmp_path)
    mcp.request("initialize", {})
    out = mcp.request("tools/call", {"name": "report_progress", "arguments": {"message": "m"}})
    assert "error" not in out and out["result"]["isError"] is True
    assert json.loads(out["result"]["content"][0]["text"])["error"] == "forbidden"
    mcp.close()


def test_protocol_errors_and_robustness(api, tmp_path):
    mcp = McpProc(api, tmp_path)
    assert mcp.request("no/such/method")["error"]["code"] == -32601
    assert mcp.request("ping")["result"] == {}
    assert mcp.proc.stdin and mcp.proc.stdout
    mcp.proc.stdin.write("not json\n")
    mcp.proc.stdin.flush()
    assert json.loads(mcp.proc.stdout.readline())["error"]["code"] == -32700
    assert mcp.request("tools/call", {"name": "get_context", "arguments": "oops"})["result"]  # still alive
    mcp.close()


def test_the_token_never_appears_on_stdout_or_stderr_and_comes_from_a_file(api, tmp_path):
    api.forced["/progress"] = (400, {"error": {"code": "bad", "message": f"echo {api.token}", "details": None}})
    mcp = McpProc(api, tmp_path)
    mcp.request("initialize", {})
    out = mcp.request("tools/call", {"name": "report_progress", "arguments": {"message": "m"}})
    assert api.token not in json.dumps(out) and "[REDACTED]" in json.dumps(out)
    stderr = mcp.close()
    assert api.token not in stderr
    assert api.token not in " ".join(mcp.proc.args)  # not in argv either


def test_missing_configuration_exits_with_an_error(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "company_agent.mcp_server"], cwd=RUNTIME_ROOT, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / "h")},
        input="", timeout=30,
    )
    assert done.returncode != 0 and "COMPANY_OS_API_URL" in done.stderr


def test_default_tools_include_request_purchase_and_all_are_known():
    from company_agent import mcp_server
    from tools import company_tools as ct

    assert "request_purchase" in mcp_server.DEFAULT_TOOLS
    assert all(mcp_server.PREFIX + t in ct._TOOLS for t in mcp_server.DEFAULT_TOOLS)


def test_cli_brief_uses_the_company_agent_playbooks():
    from company_agent import prompts

    founder = prompts.cli_brief({"role": "founder", "task": {"title": "Wake", "kind": "planning"}})
    assert prompts.FOUNDER_PLAYBOOK in founder and "company_get_state" in founder
    assert "BUYING THINGS" not in founder  # the Co-founder does not request purchases
    hr = prompts.cli_brief(
        {"role": "HR", "company_tools": ["company_request_purchase"], "task": {"title": "Hire"}}
    )
    assert prompts.HR_PLAYBOOK in hr and "BUYING THINGS" in hr
    assert prompts.SPECIALIST_PLAYBOOK in prompts.cli_brief({"role": "nonsense", "task": {"title": "x"}})
