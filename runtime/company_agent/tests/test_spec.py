"""Run spec validation and the exit-code-3 path."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from company_agent.spec import SpecError, parse_spec, read_spec_from_fd
from company_agent.tests.conftest import RUNTIME_ROOT, make_spec


@pytest.fixture
def good(tmp_path, api, model):
    return make_spec(tmp_path, api, model)


def test_valid_spec_parses(good):
    spec = parse_spec(good)
    assert spec.run_id == "run-1" and spec.model.provider == "custom"
    assert spec.toolsets == ["company", "file", "todo"]
    assert spec.max_iterations == 12 and spec.sandbox_backend == "none"


def test_secrets_stay_out_of_repr_and_public_dict(good):
    spec = parse_spec(good)
    blob = repr(spec) + json.dumps(spec.public_dict())
    assert good["agent_api"]["token"] not in blob
    assert good["model"]["api_key"] not in blob
    assert set(spec.secrets) == {good["agent_api"]["token"], good["model"]["api_key"]}


def test_credentials_in_base_url_are_stripped_from_public_dict(good):
    good["model"]["base_url"] = "http://user:hunter2@127.0.0.1:11434/v1"
    assert "hunter2" not in json.dumps(parse_spec(good).public_dict())


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(protocol=2), "protocol"),
    (lambda s: s.pop("run_id"), "run_id"),
    (lambda s: s["company"].pop("name"), "company.name"),
    (lambda s: s["agent"].pop("role_title"), "agent.role_title"),
    (lambda s: s["task"].pop("id"), "task.id"),
    (lambda s: s["model"].update(provider="openai"), "model.provider"),
    (lambda s: s["model"].update(api_key=""), "model.api_key"),
    (lambda s: s["model"].update(context_length=100), "model.context_length"),
    (lambda s: s["model"].update(base_url="ftp://x"), "model.base_url"),
    (lambda s: s.update(toolsets=[]), "toolsets"),
    (lambda s: s.update(toolsets=["company", "browser"]), "browser"),
    (lambda s: s.update(toolsets=["company", "delegation", "memory"]), "delegation"),
    (lambda s: s.update(company_tools=["company_get_context", "rm_rf"]), "rm_rf"),
    (lambda s: s["limits"].update(max_iterations=0), "limits.max_iterations"),
    (lambda s: s["limits"].update(max_run_seconds="soon"), "limits.max_run_seconds"),
    (lambda s: s["agent_api"].pop("token"), "agent_api.token"),
    (lambda s: s["agent_api"].update(url="file:///etc"), "agent_api.url"),
    (lambda s: s.update(workspace_dir="relative/path"), "workspace_dir"),
    (lambda s: s["sandbox"].update(backend="docker"), "docker_image"),
    (lambda s: s["sandbox"].update(backend="vm"), "sandbox.backend"),
])
def test_invalid_specs_are_rejected_with_a_useful_message(good, mutate, needle):
    bad = copy.deepcopy(good)
    mutate(bad)
    with pytest.raises(SpecError) as ei:
        parse_spec(bad)
    assert needle in str(ei.value)


def test_all_problems_reported_together(good):
    bad = copy.deepcopy(good)
    bad["run_id"] = ""
    bad["model"]["provider"] = "nope"
    bad["toolsets"] = ["browser"]
    with pytest.raises(SpecError) as ei:
        parse_spec(bad)
    assert len(ei.value.problems) >= 3


def test_workspace_and_home_must_not_nest(good, tmp_path):
    good["hermes_home"] = str(tmp_path / "workspace" / "home")
    with pytest.raises(SpecError):
        parse_spec(good)


def test_read_spec_returns_without_waiting_for_eof(tmp_path):
    """The control plane may keep stdin open; the document alone is enough."""
    import os

    r, w = os.pipe()
    os.write(w, b'{"protocol": 1}\n')
    assert read_spec_from_fd(r, timeout=2) == {"protocol": 1}
    os.close(r), os.close(w)


def test_read_spec_times_out_on_silence():
    import os

    r, w = os.pipe()
    with pytest.raises(SpecError, match="timed out"):
        read_spec_from_fd(r, timeout=0.5)
    os.close(r), os.close(w)


def run_launcher(stdin: str):
    p = subprocess.run([sys.executable, "-m", "company_agent.run"], input=stdin, capture_output=True, text=True,
                       cwd=RUNTIME_ROOT, timeout=60, env={"PATH": "/usr/bin:/bin"})
    return p.returncode, [json.loads(l) for l in p.stdout.splitlines() if l.strip()], p.stderr


@pytest.mark.parametrize("stdin", ["", "not json at all", "[]", '{"protocol": 1}'])
def test_bad_spec_exits_3_with_a_single_failed_event(stdin):
    code, events, _ = run_launcher(stdin)
    assert code == 3
    assert [e["type"] for e in events] == ["run.failed"]
    assert events[0]["seq"] == 1
    assert events[0]["payload"]["exit_reason"] == "bad_spec"


def test_bad_spec_error_never_echoes_secrets(good):
    good["toolsets"] = ["browser"]
    good["model"]["api_key"] = "sk-super-secret-key-0123456789"
    code, events, err = run_launcher(json.dumps(good))
    assert code == 3
    blob = json.dumps(events) + err
    assert "sk-super-secret-key" not in blob and good["agent_api"]["token"] not in blob


def test_version_flag_needs_no_stdin():
    p = subprocess.run([sys.executable, "-m", "company_agent.run", "--version"], capture_output=True, text=True,
                       cwd=RUNTIME_ROOT, timeout=30, stdin=subprocess.DEVNULL)
    info = json.loads(p.stdout)
    assert p.returncode == 0 and info["protocol"] == 1 and info["upstream_hermes_commit"].startswith("db1fa153")
