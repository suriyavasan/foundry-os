"""M18 Part B: spec ``mode: "verify"`` -- the work graph's self-check. One tool-less question; the
model's final answer is the run's result (no task to complete)."""

from __future__ import annotations

import pytest

from company_agent.spec import SpecError, parse_spec
from company_agent.tests.conftest import make_spec

VERDICT = '{"pass": false, "feedback": "The second bullet is missing."}'


def verify_spec(tmp_path, api, model):
    spec = make_spec(tmp_path, api, model, toolsets=["todo"], company_tools=[], mode="verify")
    spec["task"]["description"] = "Judge this result. Answer with JSON only."
    return spec


def test_verify_mode_is_parsed_and_validated(tmp_path, api, model):
    assert parse_spec(verify_spec(tmp_path, api, model)).mode == "verify"
    assert parse_spec(make_spec(tmp_path, api, model)).mode == "work"
    with pytest.raises(SpecError, match="mode must be"):
        parse_spec(make_spec(tmp_path, api, model, mode="other"))


def test_a_verify_run_answers_without_tools_and_the_answer_is_the_result(tmp_path, api, model, launch):
    model.script = [{"content": VERDICT}]
    code, events, err = launch(verify_spec(tmp_path, api, model)).finish()
    assert code == 0, err[-2000:]
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["payload"]["summary"] == VERDICT
    assert not [e for e in events if e["type"] == "tool.started"]
    first = model.requests[0]
    names = {t["function"]["name"] for t in first.get("tools", [])}
    assert not [n for n in names if n.startswith("company_")]  # no company tools at all
    assert "verifier" in first["messages"][0]["content"]
    assert first["messages"][-1]["content"].startswith("Judge this result")
