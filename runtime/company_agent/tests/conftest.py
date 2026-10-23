"""Shared pytest fixtures. Tests that start the launcher run it as a real subprocess
(``python -m company_agent.run``) exactly like the control plane will."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

# Hermes reads HERMES_HOME at import: never let the test process see the developer's real ~/.hermes.
_SESSION_HOME = tempfile.mkdtemp(prefix="company-agent-tests-home-")
os.environ["HERMES_HOME"] = os.path.join(_SESSION_HOME, "hermes")
os.environ["HOME"] = _SESSION_HOME
os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
os.makedirs(os.environ["HERMES_HOME"], exist_ok=True)

import pytest

from company_agent.tests.fake_openai import FakeOpenAI
from company_agent.tests.stub_agent_api import StubApi

RUNTIME_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def api():
    stub = StubApi().start()
    yield stub
    stub.stop()


@pytest.fixture
def model():
    server = FakeOpenAI().start()
    yield server
    server.stop()


def make_spec(tmp_path: Path, api: StubApi, model: FakeOpenAI, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "protocol": 1,
        "run_id": api.run_id,
        "company": {"id": "c1", "name": "Acme Robotics", "mission": "Build helpful robots",
                    "strategic_goal": "Ship v1", "constraints": ["No spending without approval"]},
        "agent": {"id": "a1", "name": "Ada", "role_title": "Research Analyst", "kind": "worker",
                  "responsibilities": "Research and write briefs", "instructions": "Be concise."},
        "task": {"id": api.task_id, "title": "Write a market brief", "description": "5 bullets",
                 "acceptance_criteria": "Exactly 5 bullets", "kind": "work", "priority": "normal", "input_refs": []},
        "model": {"provider": "custom", "base_url": model.base_url, "api_key": "sk-test-model-key-0123456789",
                  "model": "fake-model", "context_length": 32768, "is_local": True},
        "toolsets": ["company", "file", "todo"],
        "limits": {"max_iterations": 12, "max_run_seconds": 120},
        "agent_api": {"url": api.url, "token": api.token},
        "workspace_dir": str(tmp_path / "workspace"),
        "hermes_home": str(tmp_path / "home"),
        "sandbox": {"backend": "none"},
    }
    spec.update(overrides)
    return spec


class Launched:
    """A running ``python -m company_agent.run`` subprocess."""

    def __init__(self, spec: Any):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "company_agent.run"],
            cwd=RUNTIME_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(RUNTIME_ROOT)}, text=True,
        )
        payload = spec if isinstance(spec, str) else json.dumps(spec)
        assert self.proc.stdin is not None
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        self.proc.stdin.close()

    def finish(self, timeout: float = 120) -> tuple[int, list[dict[str, Any]], str]:
        out, err = self.proc.communicate(timeout=timeout)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        return self.proc.returncode, events, err


@pytest.fixture
def launch():
    started: list[Launched] = []

    def _launch(spec: Any) -> Launched:
        run = Launched(spec)
        started.append(run)
        return run

    yield _launch
    for run in started:
        if run.proc.poll() is None:
            run.proc.kill()
