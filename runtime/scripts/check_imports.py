#!/usr/bin/env python
"""Import check for the pruned Hermes fork (run after any prune / upstream sync).

Imports the pieces the company runtime depends on, makes sure every tool module under ``tools/``
imported without errors, and resolves the tool definitions for the company toolsets.

    cd runtime && .venv/bin/python scripts/check_imports.py

Exit code 0 = OK. Uses a throwaway HERMES_HOME; never touches ~/.hermes.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.TemporaryDirectory(prefix="company-agent-check-")
os.environ["HERMES_HOME"] = os.path.join(_tmp.name, "home")
os.environ["HOME"] = _tmp.name
os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
os.environ["HERMES_MIN_CONTEXT_TOKENS"] = "16384"
os.makedirs(os.environ["HERMES_HOME"], exist_ok=True)
# Same setting the launcher writes: tools go straight into the request (no search/describe/call bridge).
Path(os.environ["HERMES_HOME"], "config.yaml").write_text('tools:\n  tool_search:\n    enabled: "off"\n')

problems: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "Could not import tool module" in msg or "Skipping" in msg and "has no __init__" in msg:
            problems.append(msg)


logging.getLogger().addHandler(_Capture())
logging.getLogger().setLevel(logging.WARNING)

COMPANY_TOOLS = {
    "company_get_context", "company_create_task", "company_update_task", "company_complete_task",
    "company_review_task", "company_submit_artifact", "company_report_progress",
    "company_record_decision", "company_request_approval", "company_decide_approval",
}
MODULES = [
    "run_agent", "model_tools", "tools.registry", "toolsets", "tools.file_tools", "tools.web_tools",
    "tools.skills_tool", "tools.todo_tool", "tools.company_tools", "tools.terminal_tool",
    "agent.model_metadata", "agent.file_safety", "agent.system_prompt",
    "company_agent.run", "company_agent.runner", "company_agent.spec", "company_agent.protocol",
    "company_agent.prompts", "company_agent.home", "company_agent.context", "company_agent.approvals",
    "company_agent.toolpolicy", "company_agent.guard",
]


def main() -> int:
    for name in MODULES:
        try:
            importlib.import_module(name)
            print(f"ok   import {name}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"import {name}: {type(exc).__name__}: {exc}")
            print(f"FAIL import {name}: {exc}")

    try:
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH

        if MINIMUM_CONTEXT_LENGTH != 16384:
            problems.append(f"HERMES_MIN_CONTEXT_TOKENS not honoured (floor={MINIMUM_CONTEXT_LENGTH})")
        else:
            print("ok   configurable context floor = 16384")

        from company_agent import context as ctx
        import model_tools
        from tools.registry import registry

        # The company tools are hidden until the launcher configures the agent API connection.
        ctx.configure("http://127.0.0.1:1/api/agent/v1", "check-token-not-used", run_id="r", task_id="t",
                      workspace_dir=_tmp.name)
        from company_agent.spec import parse_spec
        from company_agent.toolpolicy import resolve_toolsets

        # What the launcher really enables for the example toolsets of the run spec (spec A3).
        example = parse_spec({
            "protocol": 1, "run_id": "r", "company": {"id": "c", "name": "n"},
            "agent": {"id": "a", "name": "n", "role_title": "r"}, "task": {"id": "t", "title": "t"},
            "model": {"provider": "custom", "base_url": "http://127.0.0.1:1/v1", "api_key": "k", "model": "m",
                      "context_length": 32768},
            "toolsets": ["company", "file", "web", "skills", "todo", "terminal", "code_execution"],
            "limits": {"max_iterations": 5, "max_run_seconds": 60},
            "agent_api": {"url": "http://127.0.0.1:1/api/agent/v1", "token": "tok"},
            "workspace_dir": _tmp.name + "/ws", "hermes_home": _tmp.name + "/home2",
        })
        launcher_toolsets, _warnings = resolve_toolsets(example)
        for toolsets in (["company"], launcher_toolsets, ["company", "file", "web", "skills", "todo"]):
            defs = model_tools.get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
            names = sorted(d["function"]["name"] for d in defs)
            print(f"ok   {toolsets} -> {len(names)} tools: {', '.join(names)}")
            missing = COMPANY_TOOLS - set(names)
            if missing:
                problems.append(f"{toolsets}: missing company tools {sorted(missing)}")
            forbidden = {"terminal", "execute_code", "delegate_task", "browser_exec", "memory", "cronjob_manage"} & set(names)
            if toolsets == launcher_toolsets:
                forbidden |= {"skill_manage", "web_extract", "process_manage"} & set(names)
            if forbidden:
                problems.append(f"{toolsets}: unexpected powerful tools {sorted(forbidden)}")
        if "company_get_context" not in registry.get_all_tool_names():
            problems.append("company tools were not auto-registered")
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        problems.append(f"tool definition resolution failed: {type(exc).__name__}: {exc}")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nAll import checks passed.")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
