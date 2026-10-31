"""M18 A3-A5: faster unblocking (a block says what unblocks it, the heartbeat looks again every 6 h),
retries that remember how the last attempt ended, one re-plan per task, and the decide-don't-ask
prompt doctrine in every prompt a coding CLI gets."""

from __future__ import annotations

import pytest

from app.models.work import Task
from app.runtimes.cli import prompt as cli_prompt
from app.services import retry_policy
from tests.autonomy_helpers import run_world, scripted_world
from tests.helpers import api, new_agent, new_task, set_policy
from tests.test_agent_api import start_run
from tests.test_m12_failures import force, row
from tests.test_m12_routines import (
    finish_heartbeat_tasks,
    fire,
    only_heartbeat,
    raise_alert,
    skip_reasons,
)


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


# ------------------------------------------------------------------ A4: blocking says why/when


def test_blocking_a_task_requires_an_unblock_condition(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Needs a key")
    url = f"/tasks/{sess.task_id}"
    r = sess.patch(url, json={"status": "blocked", "reason": "no API key"})
    assert r.status_code == 422 and "unblock_condition" in r.text
    assert "company_ask_founder" in r.json()["error"]["message"]  # the better tool for a question
    assert (
        sess.patch(url, json={"status": "blocked", "unblock_condition": "   "}).status_code == 422
    )
    too_long = sess.patch(url, json={"status": "blocked", "unblock_condition": "x" * 301})
    assert too_long.status_code == 422
    assert row(app, sess.task_id).status == "in_progress"  # nothing happened
    ok = sess.patch(
        url,
        json={"status": "blocked", "reason": "no API key", "unblock_condition": "a key is added"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "blocked"
    assert row(app, sess.task_id).error["unblock_condition"] == "a key is added"
    # the lead who is woken for the blocked task reads the condition
    from tests.test_m12_watchdog import wake_reasons

    texts = " ".join(r["text"] for r in wake_reasons(app, w.lead["id"]))
    assert "unblocks when: a key is added" in texts
    # a person blocking from the UI is unchanged (no condition required)
    t = new_task(client, cid, title="On hold", assignee_agent_id=w.specialist["id"])
    assert (
        client.post(api(cid, f"/tasks/{t['id']}/block"), json={"reason": "wait"}).status_code == 200
    )


def test_the_heartbeat_looks_again_after_six_hours_even_when_nothing_changed(app, client, w, clock):
    cid = w.cid
    only_heartbeat(app, cid)
    raise_alert(app, cid, "Disk is full")
    assert len(fire(app, clock)) == 1  # it runs and remembers what it looked at
    finish_heartbeat_tasks(app, cid)
    assert fire(app, clock, 31) == []  # unchanged: skipped
    assert fire(app, clock, 300) == []  # 5 h 31 min since it last looked: still skipped
    assert any("nothing new since the last heartbeat" in s for s in skip_reasons(app))
    assert len(fire(app, clock, 40)) == 1  # past 6 h: it looks at the still-open alert again


# ------------------------------------------------------------------ A5: retries with memory


def test_a_retry_carries_how_the_previous_attempt_ended(
    app, client, w, scheduler, worker, clock, scripted
):
    cid = w.cid
    set_policy(client, cid, max_attempts_per_task=3, circuit_breaker_failures=0)
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={
            "runtime_config": {
                "script": {
                    "result": "failed",
                    "error_message": "the model returned garbage sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
                }
            }
        },
    )
    t = new_task(client, cid, title="Write the plan", assignee_agent_id=w.specialist["id"])
    for _ in range(4):  # the permanent backoff is 0 s, 60 s, 240 s
        scheduler.tick()
        worker.drain()
        clock.advance(300)
    stored = row(app, t["id"])
    assert stored.status == "failed" and stored.attempt_count == 3
    mine = [r for r in scripted.requests if r.task.id == t["id"]]
    assert len(mine) == 3
    assert mine[0].task.carry_over is None  # the first attempt has no past
    for later in mine[1:]:
        text = later.task.carry_over
        assert "previous attempt" in text and "the model returned garbage" in text
        assert "permanent failure" in text
        assert "sk-ant-api03" not in text  # redacted
    last = stored.last_attempt
    assert last["failure_class"] == retry_policy.PERMANENT and last["attempt"] == 3
    assert len(last["message"]) <= 1000 and last["run_id"]
    assert "sk-ant-api03" not in last["message"]
    # the analyst of the failure is told it may retry with a new approach (once)
    from tests.autonomy_helpers import tasks_of

    [analysis] = [x for x in tasks_of(app, cid, kind="review") if "failure:" in x.idempotency_key]
    assert "new_instructions" in analysis.description


def test_a_transient_failure_is_remembered_too_and_success_forgets_it(
    app, client, w, scheduler, worker, clock, scripted
):
    cid = w.cid
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={
            "runtime_config": {
                "script": {"result": "failed", "error_message": "429 Too Many Requests"}
            }
        },
    )
    t = new_task(client, cid, title="Flaky", assignee_agent_id=w.specialist["id"])
    scheduler.tick()
    worker.drain()
    last = row(app, t["id"]).last_attempt
    assert last["failure_class"] == retry_policy.TRANSIENT and "429" in last["message"]
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={"runtime_config": {"script": {"result": "succeeded"}}},
    )
    clock.advance(60)
    run_world(app, scheduler, worker, clock, rounds=6)
    done = row(app, t["id"])
    assert done.status in ("done", "in_review") and done.last_attempt is None


def test_the_carry_over_reaches_the_company_agent_spec_and_the_cli_prompt():
    from app.runtimes.base import TaskSpec

    spec = TaskSpec(
        "t1", "Title", "orig", None, "work", "normal", [], None, carry_over="CARRY TEXT"
    )
    assert spec.carry_over == "CARRY TEXT"
    inputs = cli_prompt.PromptInputs(
        company_name="Acme",
        mission=None,
        strategic_goal=None,
        constraints=[],
        agent_name="Sam",
        role_title="Writer",
        responsibilities=None,
        instructions=None,
        task_id="t1",
        task_title="Title",
        task_description="orig",
        acceptance_criteria=None,
        review_note=None,
        carry_over="The previous attempt (number 1) ended with a permanent failure (x).",
        tool_prefix="mcp__company__",
    )
    text = cli_prompt.build(inputs)
    assert "BEFORE YOU START" in text and "previous attempt (number 1)" in text


def test_replan_once_resets_the_attempts_and_adds_the_new_approach(app, client, w):
    cid = w.cid
    victim = new_task(client, cid, title="Flaky job", assignee_agent_id=w.specialist["id"])
    sess = start_run(app, client, cid, w.founder["id"], task_title="Plan", kind="planning")
    force(app, victim["id"], status="failed", attempt_count=3, description="Original brief")
    # an empty or oversized instruction is refused
    assert (
        sess.post(f"/tasks/{victim['id']}/retry", json={"new_instructions": "  "}).status_code
        == 422
    )
    assert (
        sess.post(f"/tasks/{victim['id']}/retry", json={"new_instructions": "x" * 2001}).status_code
        == 422
    )
    r = sess.post(
        f"/tasks/{victim['id']}/retry", json={"new_instructions": "Split it in two parts"}
    )
    assert r.status_code == 200, r.text
    after = row(app, victim["id"])
    assert after.replans == 1 and after.attempt_count == 0 and after.status in ("backlog", "ready")
    assert after.description == "Original brief\n\nRevised approach:\nSplit it in two parts"
    # a second re-plan is refused: beyond that, humans only
    force(app, victim["id"], status="failed", attempt_count=3)
    again = sess.post(
        f"/tasks/{victim['id']}/retry", json={"new_instructions": "Try yet another way"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "limit_exceeded"
    assert again.json()["error"]["details"]["limit"] == "max_replans"
    assert row(app, victim["id"]).status == "failed" and row(app, victim["id"]).replans == 1
    # a human retry is still possible
    assert client.post(api(cid, f"/tasks/{victim['id']}/retry")).status_code == 200
    assert row(app, victim["id"]).replans == 0  # a human retry resets every counter


# ------------------------------------------------------------------ A3: prompt doctrine


def test_the_cli_prompts_use_the_decide_dont_ask_doctrine():
    inputs = cli_prompt.PromptInputs(
        company_name="Acme",
        mission=None,
        strategic_goal=None,
        constraints=[],
        agent_name="Sam",
        role_title="Writer",
        responsibilities=None,
        instructions=None,
        task_id="t1",
        task_title="Title",
        task_description=None,
        acceptance_criteria=None,
        review_note=None,
        tool_prefix="mcp__company__",
    )
    for text in (
        cli_prompt.build(inputs),
        cli_prompt.with_role_brief(
            cli_prompt.build(inputs), "Do the thing.", ["get_context"], "mcp__company__"
        ),
    ):
        assert "mcp__company__ask_founder" in text and "mcp__company__record_decision" in text
        assert "never ask a human for input" not in text.lower()
        assert "company_request_approval first" not in text
        assert "checked automatically" in text
        assert "unblock_condition" in text or "Decide and act yourself" in text


def test_every_role_may_ask_the_founder_and_a_specialist_records_decisions(app, client, w):
    from app.services import agent_toolkit

    with app.state.session_factory() as db:
        from app.models.agent import Agent

        for agent in (w.founder, w.hr, w.lead, w.specialist):
            tools = agent_toolkit.company_tools_for(db, db.get(Agent, agent["id"]))
            assert "company_ask_founder" in tools, agent["name"]
        specialist = agent_toolkit.company_tools_for(db, db.get(Agent, w.specialist["id"]))
        assert "company_record_decision" in specialist
    assert "ask_founder" in __import__("app.runtimes.cli.base", fromlist=["x"]).CLI_MCP_TOOLS
    _ = Task, new_agent
