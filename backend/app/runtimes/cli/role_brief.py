"""Role instructions for a coding-CLI run (B9).

A management run (the Co-founder, HR, a project lead, a manager, or any non-work task) must follow the
same playbook a ``company_agent`` run of that role gets. The text lives in ``runtime/company_agent/
prompts.py`` (the runtime has its own virtualenv, so it is not imported here): this module asks the
runtime's interpreter for it (``python -m company_agent.prompts``, JSON in, JSON out) and renames the
company tools to the CLI's MCP names. Nothing is forked. The brief is guidance only; what a run may do
is enforced by the tool list it is offered and, again, by the agent API.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.runtimes.base import RuntimeAdapterError, RuntimeErrorKind

BRIEF_TIMEOUT_SECONDS = 20
_TOOL_RE = re.compile(r"\bcompany_([a-z][a-z_]*)\b")


def rename_tools(text: str, offered: list[str], tool_prefix: str) -> str:
    """``company_get_state`` -> ``mcp__company__get_state`` for the tools this run is offered."""
    names = set(offered)

    def sub(m: re.Match[str]) -> str:
        return f"{tool_prefix}{m.group(1)}" if m.group(1) in names else m.group(0)

    return _TOOL_RE.sub(sub, text)


def fetch(
    python: Path | None,
    runtime_dir: Path | None,
    scratch: Path,
    *,
    role: str,
    tools: list[str],
    task: dict[str, Any],
    tool_prefix: str,
) -> str:
    """The role brief for this run; raises a CONFIG error when the runtime cannot produce it (a
    management run must never silently start without its instructions)."""
    if python is None or runtime_dir is None or not Path(python).exists():
        raise RuntimeAdapterError(
            RuntimeErrorKind.CONFIG,
            "The company runtime is not installed, so the role instructions for this run are "
            "unavailable (create runtime/.venv, see runtime/README.md)",
        )
    payload = {"role": role, "company_tools": tools, "task": task}
    scratch.mkdir(parents=True, exist_ok=True)
    env = {
        "PYTHONPATH": str(runtime_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "HOME": str(scratch),
    }
    try:
        done = subprocess.run(
            [str(python), "-m", "company_agent.prompts"],
            input=json.dumps(payload),
            cwd=runtime_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=BRIEF_TIMEOUT_SECONDS,
        )
        brief = str(json.loads(done.stdout)["brief"]) if done.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        brief = ""
    if not brief.strip():
        raise RuntimeAdapterError(
            RuntimeErrorKind.CONFIG, "The role instructions for this run could not be produced"
        )
    return rename_tools(brief, [t.removeprefix("company_") for t in tools], tool_prefix)
