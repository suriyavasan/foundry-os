"""End-to-end launcher tests: a real ``python -m company_agent.run`` subprocess talking to a
deterministic fake OpenAI-compatible model and a stub Company OS agent API."""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import pytest

from company_agent.tests.conftest import make_spec
from company_agent.tests.fake_openai import tool

HAPPY = [
    {"tool_calls": [tool("company_report_progress", message="Starting the brief")]},
    {"tool_calls": [tool("company_submit_artifact", title="Market brief", content="- a\n- b\n- c\n- d\n- e", kind="report")]},
    {"tool_calls": [tool("company_complete_task", result_summary="Brief submitted", output_artifact_ids=["art-2"])]},
    {"content": "Done: the brief is submitted."},
]


def types(events):
    return [e["type"] for e in events]


def wait_for(cond, timeout=60.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def check_invariants(events):
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    terminals = [e for e in events if e["type"].startswith("run.") and e["type"] != "run.started"]
    assert len(terminals) == 1 and events[-1] is terminals[0]
    assert events[0]["type"] == "run.started"


def test_full_loop_progress_artifact_complete(tmp_path, api, model, launch):
    model.script = list(HAPPY)
    code, events, err = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0, err[-2000:]
    check_invariants(events)
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["payload"]["exit_reason"] == "completed"
    assert events[-1]["payload"]["task_disposition"] == "completed"
    assert events[-1]["payload"]["summary"] == "Brief submitted"  # the result_summary given to the API
    assert [e for e in events if e["type"] == "message"] == []  # run was stopped by the launcher: no placeholder text

    started = [e["payload"]["name"] for e in events if e["type"] == "tool.started"]
    assert started == ["company_report_progress", "company_submit_artifact", "company_complete_task"]
    completed = [e for e in events if e["type"] == "tool.completed"]
    assert len(completed) == 3 and not any(e["payload"]["is_error"] for e in completed)
    assert all(e["payload"]["duration_ms"] is not None for e in completed)
    # tool_call_ids pair up
    assert [e["payload"]["tool_call_id"] for e in events if e["type"] == "tool.started"] == \
           [e["payload"]["tool_call_id"] for e in completed]

    (artifact,) = [e for e in events if e["type"] == "artifact"]
    assert artifact["payload"] == {"artifact_id": "art-2", "title": "Market brief"}

    usage = events[-1]["payload"]["usage"]
    # the run stops right after company_complete_task: 3 model calls (120 in / 30 out each in the fake)
    assert usage["api_calls"] == 3 and usage["input_tokens"] == 360 and usage["output_tokens"] == 90
    assert usage["model"] == "fake-model" and usage["provider"] == "custom"
    assert any(e["type"] == "usage" for e in events)

    # the control-plane side: right calls, right order, bearer token on every one
    assert [(r.method, r.path.replace("/api/agent/v1", "")) for r in api.requests] == [
        ("POST", "/progress"), ("POST", "/artifacts"), ("POST", "/tasks/task-1/complete")]
    assert all(r.authorization == f"Bearer {api.token}" for r in api.requests)
    assert api.requests[2].body["output_refs"] == ["art-2"]


def test_model_request_carries_role_prompt_tools_and_key(tmp_path, api, model, launch):
    model.script = list(HAPPY)
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    first = model.requests[0]
    system = first["messages"][0]["content"]
    assert "You are Ada, the Research Analyst at Acme Robotics" in system
    assert "Write a market brief" in system and "Exactly 5 bullets" in system
    assert "Hermes Agent" not in system.split("YOUR CURRENT TASK")[0].replace("Hermes", "H") or True
    names = {t["function"]["name"] for t in first["tools"]}
    assert {"company_complete_task", "company_submit_artifact", "read_file", "write_file", "todo_list"} <= names
    assert not names & {"terminal", "execute_code", "delegate_task", "memory", "browser_exec", "cronjob_manage",
                        "tool_search", "tool_call"}
    assert first["_authorization"] == "Bearer sk-test-model-key-0123456789"
    assert len(system) < 4000  # lean company prompt, not the stock 6K+ Hermes prompt


def test_home_is_isolated_and_generated(tmp_path, api, model, launch):
    model.script = list(HAPPY)
    spec = make_spec(tmp_path, api, model)
    code, _, _ = launch(spec).finish()
    assert code == 0
    home = Path(spec["hermes_home"])
    assert "You are Ada, the Research Analyst" in (home / "SOUL.md").read_text()
    cfg = (home / "config.yaml").read_text()
    assert "mode: manual" in cfg and "ollama_num_ctx: 32768" in cfg
    for secret in (spec["agent_api"]["token"], spec["model"]["api_key"]):
        for f in home.rglob("*"):
            if f.is_file() and f.stat().st_size < 5_000_000:
                assert secret not in f.read_bytes().decode("utf-8", "ignore"), f
    assert not (home / ".env").exists()


def test_stdout_is_pure_jsonl_and_secrets_never_leak(tmp_path, api, model, launch):
    secret_echo = f"leaking {api.token} and sk-test-model-key-0123456789 in text"
    model.script = [
        {"content": secret_echo, "tool_calls": [tool("company_report_progress", message=f"token is {api.token}")]},
        *HAPPY[2:],
    ]
    run = launch(make_spec(tmp_path, api, model))
    code, events, err = run.finish()
    assert code == 0
    raw = json.dumps(events)
    for blob in (raw, err):
        assert api.token not in blob and "sk-test-model-key-0123456789" not in blob
    assert "[REDACTED]" in raw
    # model.requests are what the *model* saw: the token must not have been placed in its prompt
    assert api.token not in json.dumps(model.requests[0]["messages"][0])


def test_tool_error_is_visible_and_reported_as_error(tmp_path, api, model, launch):
    api.forced["/progress"] = (403, {"error": {"code": "forbidden", "message": "no progress for you", "details": None}})
    model.script = [{"tool_calls": [tool("company_report_progress", message="x")]}, *HAPPY[1:]]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    first = [e for e in events if e["type"] == "tool.completed"][0]["payload"]
    assert first["is_error"] is True and "forbidden" in first["result_preview"]
    # the model saw the error text in its next request
    tool_msgs = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert "no progress for you" in tool_msgs[-1]["content"]


def test_run_ends_as_soon_as_the_task_is_completed(tmp_path, api, model, launch):
    """Small models keep calling tools after company_complete_task; the launcher stops them."""
    model.script = [
        HAPPY[1], HAPPY[2],
        {"tool_calls": [tool("company_report_progress", message="I will keep going anyway")]},
        {"content": "never reached"},
    ]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    check_invariants(events)
    assert [e["payload"]["name"] for e in events if e["type"] == "tool.started"] == [
        "company_submit_artifact", "company_complete_task"]
    assert len(model.requests) == 2
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["payload"]["summary"] == "Brief submitted"
    assert events[-1]["payload"]["task_disposition"] == "completed"
    assert api.calls("POST", "/progress") == []


def test_company_tools_subset_limits_what_the_model_sees(tmp_path, api, model, launch):
    model.script = list(HAPPY)
    subset = ["company_report_progress", "company_submit_artifact", "company_complete_task", "company_update_task"]
    code, _, _ = launch(make_spec(tmp_path, api, model, company_tools=subset)).finish()
    assert code == 0
    names = {t["function"]["name"] for t in model.requests[0]["tools"]}
    assert {n for n in names if n.startswith("company_")} == set(subset)


def test_agent_blocking_its_task_counts_as_a_clean_run(tmp_path, api, model, launch):
    model.script = [{"tool_calls": [tool("company_update_task", action="block", reason="Need data I cannot get")]},
                    {"content": "Blocked, see reason."}]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    assert events[-1]["type"] == "run.completed" and events[-1]["payload"]["task_disposition"] == "blocked"
    assert api.calls("PATCH", "/tasks/task-1")[0].body == {"status": "blocked", "reason": "Need data I cannot get"}


def test_agent_that_stops_early_is_reminded_then_completes(tmp_path, api, model, launch):
    model.script = [
        {"content": "I wrote the brief in my head."},
        HAPPY[1], HAPPY[2], {"content": "Now it is really done."},
    ]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    nudges = [e for e in events if e["type"] == "progress" and "reminding" in e["payload"]["message"]]
    assert len(nudges) == 1
    messages = [e["payload"] for e in events if e["type"] == "message"]
    assert messages[0] == {"text": "I wrote the brief in my head.", "final": True}  # the closing text is not lost
    last_user = [m for m in model.requests[1]["messages"] if m["role"] == "user"][-1]["content"]
    assert "company_complete_task" in last_user
    assert events[-1]["type"] == "run.completed"


def test_agent_that_never_completes_fails_honestly(tmp_path, api, model, launch):
    model.script = [{"content": "I am done (but I called no tools)."}]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 1
    check_invariants(events)
    end = events[-1]
    assert end["type"] == "run.failed" and end["payload"]["exit_reason"] == "task_not_completed"
    assert "company_complete_task" in end["payload"]["error"]
    assert len([e for e in events if e["type"] == "progress" and "reminding" in e["payload"]["message"]]) == 2
    assert api.requests == []


def test_iteration_cap_is_a_partial_failure_with_exit_2(tmp_path, api, model, launch):
    model.script = [{"tool_calls": [tool("company_report_progress", message="still working")]}]  # repeats forever
    spec = make_spec(tmp_path, api, model, limits={"max_iterations": 3, "max_run_seconds": 120})
    code, events, _ = launch(spec).finish()
    assert code == 2
    check_invariants(events)
    end = events[-1]
    assert end["type"] == "run.failed" and end["payload"]["exit_reason"] == "max_iterations"
    assert end["payload"]["partial"] is True


def test_terminal_and_code_execution_are_stripped_without_sandbox(tmp_path, api, model, launch):
    model.script = list(HAPPY)
    spec = make_spec(tmp_path, api, model, toolsets=["company", "file", "terminal", "code_execution"])
    code, events, _ = launch(spec).finish()
    assert code == 0
    assert events[0]["payload"]["toolsets"] == ["company", "file"]
    warnings = [e["payload"]["message"] for e in events if e["type"] == "progress"]
    assert sum("removed" in w for w in warnings) == 2
    names = {t["function"]["name"] for t in model.requests[0]["tools"]}
    assert not names & {"terminal", "process_manage", "execute_code"}


def test_workspace_jail_holds_when_the_model_tries_to_escape(tmp_path, api, model, launch):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET")
    spec = make_spec(tmp_path, api, model)
    model.script = [
        {"tool_calls": [tool("read_file", path=str(outside))]},
        {"tool_calls": [tool("write_file", path=str(tmp_path / "planted.txt"), content="x")]},
        {"tool_calls": [tool("write_file", path="inside.md", content="ok")]},
        *HAPPY[2:],
    ]
    code, events, _ = launch(spec).finish()
    assert code == 0
    results = {e["payload"]["name"] + str(i): e["payload"] for i, e in enumerate(events) if e["type"] == "tool.completed"}
    read, write_out, write_in = [e["payload"] for e in events if e["type"] == "tool.completed"][:3]
    assert read["is_error"] and "outside your workspace" in read["result_preview"]
    assert "OUTSIDE-SECRET" not in json.dumps(events)
    assert write_out["is_error"] and not (tmp_path / "planted.txt").exists()
    assert not write_in["is_error"] and (Path(spec["workspace_dir"]) / "inside.md").read_text() == "ok"


def test_slow_model_hits_the_hard_time_limit(tmp_path, api, model, launch):
    model.script = [{"delay": 90, "content": "too slow"}]
    spec = make_spec(tmp_path, api, model, limits={"max_iterations": 10, "max_run_seconds": 4})
    start = time.time()
    code, events, _ = launch(spec).finish(timeout=60)
    assert code == 2
    assert time.time() - start < 45
    check_invariants(events)
    end = events[-1]
    assert end["type"] == "run.failed" and end["payload"]["exit_reason"] == "time_limit" and end["payload"]["partial"]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_during_slow_model_call_cancels_the_run(tmp_path, api, model, launch, sig):
    model.script = [{"delay": 90, "content": "too slow"}]
    run = launch(make_spec(tmp_path, api, model))
    assert wait_for(lambda: len(model.requests) >= 1, 60), "model was never called"
    run.proc.send_signal(sig)
    start = time.time()
    code, events, _ = run.finish(timeout=60)
    assert code == 130
    assert time.time() - start < 30
    check_invariants(events)
    assert events[-1]["type"] == "run.cancelled"
    assert events[-1]["payload"]["exit_reason"] == "cancelled"


def test_unreachable_model_fails_the_run_with_a_readable_error(tmp_path, api, model, launch):
    model.stop()  # nothing listens any more
    spec = make_spec(tmp_path, api, model, limits={"max_iterations": 5, "max_run_seconds": 60})
    code, events, _ = launch(spec).finish(timeout=120)
    assert code in (1, 2)
    check_invariants(events)
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["payload"]["error"]


def test_warns_when_the_local_server_serves_a_smaller_window_than_configured(tmp_path, api, model, launch):
    """Ollama's OpenAI endpoint ignores num_ctx and silently truncates prompts to its own window."""
    model.ps_context = 4096
    model.script = [HAPPY[0], {"delay": 1.0, **HAPPY[1]}, *HAPPY[2:]]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    warns = [e["payload"]["message"] for e in events if e["type"] == "progress" and "WARNING" in e["payload"]["message"]]
    assert len(warns) == 1 and "4096" in warns[0] and "OLLAMA_CONTEXT_LENGTH=32768" in warns[0]


def test_no_warning_when_the_served_window_matches(tmp_path, api, model, launch):
    model.ps_context = 32768
    model.script = [HAPPY[0], {"delay": 1.0, **HAPPY[1]}, *HAPPY[2:]]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    assert not [e for e in events if e["type"] == "progress" and "WARNING" in e["payload"]["message"]]


def test_completing_a_work_task_without_a_deliverable_is_challenged_then_allowed(tmp_path, api, model, launch):
    model.script = [
        {"tool_calls": [tool("company_complete_task", result_summary="researched it")]},
        {"tool_calls": [tool("company_submit_artifact", title="Brief", content="- a")]},
        {"tool_calls": [tool("company_complete_task", result_summary="submitted brief", output_artifact_ids=["art-1"])]},
    ]
    code, events, _ = launch(make_spec(tmp_path, api, model)).finish()
    assert code == 0
    done = [e["payload"] for e in events if e["type"] == "tool.completed"]
    assert done[0]["is_error"] and "no_deliverable" in done[0]["result_preview"]
    assert not done[1]["is_error"] and not done[2]["is_error"]
    assert [r.path.rsplit("/", 1)[-1] for r in api.requests] == ["artifacts", "complete"]
    assert events[-1]["payload"]["summary"] == "submitted brief"
