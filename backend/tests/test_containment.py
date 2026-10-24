"""Execution containment (docs/specs/m6 section 3): stall detection, runaway caps and the global
emergency stop."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.owner_token import OWNER_COOKIE
from app.core.time import utcnow
from app.main import create_app
from app.models.run import TaskRun
from app.runtimes.base import ExecutionEvent
from app.runtimes.registry import RuntimeRegistry
from app.runtimes.scripted import ScriptedRuntime
from tests.guardrail_helpers import make_team, run_as
from tests.helpers import (
    api,
    get_task,
    new_agent,
    new_task,
    run_in_thread,
    set_policy,
    wait_until,
)


def runs_of(client, cid, task_id):
    return list(reversed(client.get(api(cid, f"/tasks/{task_id}/runs")).json()["items"]))


def only_run(app) -> TaskRun:
    with app.state.session_factory() as db:
        return db.scalars(select(TaskRun)).one()


def is_running(app, run_id: str | None = None) -> bool:
    with app.state.session_factory() as db:
        stmt = select(TaskRun.status)
        if run_id:
            stmt = stmt.where(TaskRun.id == run_id)
        return "running" in set(db.scalars(stmt))


class SteppedRuntime(ScriptedRuntime):
    """Emits an event, then waits at each gate until the test lets it go on."""

    id = "stepped"

    def __init__(self) -> None:
        super().__init__()
        self.gates = [threading.Event() for _ in range(3)]
        self.reached = [threading.Event() for _ in range(3)]

    def execute(self, request, sink, cancel):
        def emit(seq: int, type_: str) -> None:
            sink.emit(
                ExecutionEvent(seq=seq, type=type_, payload={"summary": f"step {seq}"}, ts=utcnow())
            )

        emit(1, "run.started")
        for i, gate in enumerate(self.gates):
            self.reached[i].set()
            while not gate.wait(0.01):
                if cancel.is_set():
                    emit(10 + i, "run.cancelled")
                    return self._cancelled(lambda t, p: None)
            emit(2 + i, "message")
        return super().execute(request, sink, cancel)


# ------------------------------------------------------------------ stall detection


def test_a_run_without_events_for_stall_seconds_is_cancelled_as_stalled(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, stall_seconds=30, circuit_breaker_failures=1)
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    wait_until(lambda: is_running(app), what="the run to start")
    clock.advance(20)  # quiet, but not for long enough
    time.sleep(0.15)
    assert is_running(app)
    clock.advance(11)  # 31 s without an event
    thread.join(timeout=5)
    assert not thread.is_alive()
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "failed" and run["exit_reason"] == "stalled"
    assert run["error"]["code"] == "stalled" and "30s" in run["error"]["message"]
    # a stall counts toward the circuit breaker (set to trip on the first failure)
    assert client.get(api(cid)).json()["status"] == "paused"
    assert get_task(client, cid, task["id"])["error"]["code"] == "stalled"


def test_events_keep_a_run_alive_past_the_stall_window(
    app, client, company, scheduler, clock, engine
):
    from app.engine.worker import Worker

    cid = company["id"]
    stepped = SteppedRuntime()
    app.state.runtimes.register(stepped)
    set_policy(client, cid, stall_seconds=30)
    agent = new_agent(client, cid, runtime="stepped")
    new_task(client, cid, assignee_agent_id=agent["id"])
    worker = Worker(engine, worker_id="t", monitor_interval=0.01, kinds=("execute_run",))
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    assert stepped.reached[0].wait(5)
    for i in range(3):  # 3 x 25 s of wall time, never more than 25 s between events
        clock.advance(25)
        time.sleep(0.1)
        assert is_running(app), f"cancelled at step {i}"
        stepped.gates[i].set()
        if i < 2:
            assert stepped.reached[i + 1].wait(5)
    thread.join(timeout=5)
    assert only_run(app).status == "succeeded"


# ------------------------------------------------------------------ runaway caps


def _runaway(app, client, cid, scheduler, worker, events, **policy):
    set_policy(client, cid, **policy)
    agent = new_agent(
        client, cid, script={"events": events, "block_until_cancel": True, "block_cap_seconds": 10}
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    thread.join(timeout=10)
    assert not thread.is_alive()
    [run] = runs_of(client, cid, task["id"])
    return run


@pytest.mark.parametrize(
    ("policy", "events", "reason"),
    [
        (
            {"max_tool_calls_per_run": 3},
            [{"type": "tool.started", "payload": {"name": "web_search"}}] * 5,
            "runaway_tool_calls",
        ),
        (
            {"max_artifacts_per_run": 2},
            [{"type": "artifact", "payload": {"artifact_id": "a", "title": "x"}}] * 3,
            "runaway_artifacts",
        ),
        (
            {"max_tokens_per_run": 1000},
            [{"type": "usage", "payload": {"input_tokens": 700, "output_tokens": 400}}],
            "runaway_tokens",
        ),
        (
            {},
            [
                {
                    "type": "tool.completed",
                    "payload": {
                        "name": "company_create_task",
                        "is_error": True,
                        "result_preview": '{"error": "limit_exceeded"}',
                    },
                }
            ]
            * 3,
            "runaway_tasks_created",
        ),
    ],
)
def test_runaway_runs_are_cancelled_and_fail(
    app, client, company, scheduler, worker, policy, events, reason
):
    run = _runaway(app, client, company["id"], scheduler, worker, events, **policy)
    assert run["status"] == "failed" and run["exit_reason"] == reason
    assert run["error"]["code"] == reason


def test_a_run_inside_its_caps_is_left_alone(app, client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_tool_calls_per_run=10)
    agent = new_agent(
        client,
        cid,
        script={"events": [{"type": "tool.started", "payload": {"name": "x"}}] * 10},
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.run_once()
    assert runs_of(client, cid, task["id"])[0]["status"] == "succeeded"


# ------------------------------------------------------------------ emergency stop


def owner_stop(client, reason: str | None = "test") -> dict:
    r = client.post("/api/system/emergency-stop", json={"reason": reason} if reason else {})
    assert r.status_code == 200, r.text
    return r.json()


def test_emergency_stop_cancels_every_run_in_every_company_and_disables_dispatch(
    app, client, company, scheduler, worker, engine
):
    cid_a = company["id"]
    cid_b = client.post("/api/companies", json={"name": "Second Co"}).json()["id"]
    from app.engine.worker import Worker

    worker_b = Worker(engine, worker_id="w2", monitor_interval=0.01, kinds=("execute_run",))
    tasks = {}
    for cid in (cid_a, cid_b):
        agent = new_agent(client, cid, script={"block_until_cancel": True, "block_cap_seconds": 20})
        tasks[cid] = new_task(client, cid, assignee_agent_id=agent["id"])
    queued_cid = cid_a
    scheduler.tick()
    threads = [run_in_thread(worker.run_once), run_in_thread(worker_b.run_once)]
    wait_until(lambda: _count_running(app) == 2, what="both runs to start")
    # a third task waits in the queue (a queued run) when the stop is pressed
    extra_agent = new_agent(client, queued_cid, name="Extra", script={"block_until_cancel": True})
    extra = new_task(client, queued_cid, assignee_agent_id=extra_agent["id"])
    scheduler.tick()

    stopped = owner_stop(client, "something is on fire")
    assert stopped["active"] is True and stopped["reason"] == "something is on fire"
    assert stopped["activated_by"] and stopped["activated_at"] and stopped["runs_cancelled"] == 3
    for t in threads:
        t.join(timeout=8)
        assert not t.is_alive()
    for cid in (cid_a, cid_b):
        [run] = runs_of(client, cid, tasks[cid]["id"])
        assert run["status"] == "cancelled" and run["exit_reason"] == "emergency_stop"
    [queued_run] = runs_of(client, queued_cid, extra["id"])
    assert queued_run["status"] == "cancelled" and queued_run["exit_reason"] == "emergency_stop"
    # tasks are not failed; they wait for the resume
    assert get_task(client, cid_a, tasks[cid_a]["id"])["status"] == "ready"

    # dispatch is off, schedules and wakes do not fire
    assert scheduler.tick().dispatch.queued_run_ids == []
    status = client.get("/api/system/status").json()
    assert status["emergency_stop"]["active"] is True

    # resume: the owner lifts it and work flows again
    resumed = client.post("/api/system/resume", json={})
    assert resumed.status_code == 200 and resumed.json()["active"] is False
    assert client.get("/api/system/status").json()["emergency_stop"]["active"] is False
    assert len(scheduler.tick().dispatch.queued_run_ids) >= 1


def _count_running(app) -> int:
    with app.state.session_factory() as db:
        return len([s for s in db.scalars(select(TaskRun.status)) if s == "running"])


def test_the_stop_survives_a_restart(app, client, company, make_settings):
    cid = company["id"]
    owner_stop(client)
    # a brand-new application on the same database still knows
    other = create_app(make_settings(), runtime_registry=RuntimeRegistry([ScriptedRuntime()]))
    with TestClient(other) as c2:
        c2.cookies.set(OWNER_COOKIE, other.state.owner_token.get())
        assert c2.get("/api/system/status").json()["emergency_stop"]["active"] is True
        from app.engine.context import build_context
        from app.engine.dispatcher import dispatch_tick

        agent = new_agent(c2, cid, script={})
        new_task(c2, cid, assignee_agent_id=agent["id"])
        ctx = build_context(
            make_settings(),
            session_factory=other.state.session_factory,
            registry=other.state.runtimes,
            keys=other.state.keys,
            storage=other.state.storage,
        )
        assert dispatch_tick(ctx).queued_run_ids == []
        assert c2.post("/api/system/resume", json={}).json()["active"] is False


def test_agents_can_never_press_or_lift_the_stop_even_with_a_run_token(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    worker = run_as(app, client, cid, team.worker)
    founder = run_as(app, client, cid, team.founder)
    for sess in (worker, founder):
        for path in ("/api/system/emergency-stop", "/api/system/resume"):
            assert client.post(path, headers=sess.headers, json={}).status_code == 403, path
        assert client.get("/api/system/status", headers=sess.headers).status_code == 403
    owner_stop(client)
    for sess in (worker, founder):
        r = client.post("/api/system/resume", headers=sess.headers, json={})
        assert r.status_code == 403
    assert client.get("/api/system/status").json()["emergency_stop"]["active"] is True
    # while stopped the agent API answers 503 (no new tasks, no tool calls)
    r = worker.get("/context")
    assert r.status_code == 503 and r.json()["error"]["code"] == "emergency_stop"
    assert worker.post("/tasks", json={"title": "sneaky"}).status_code == 503


def test_only_an_owner_can_stop(app, client, company, make_user, add_member, client_as):
    from tests.conftest import human

    cid = company["id"]
    viewer = make_user("viewer@example.com")
    add_member(cid, viewer.id, "viewer")
    as_viewer = client_as(human(viewer))
    assert as_viewer.post("/api/system/emergency-stop", json={}).status_code == 403
    assert as_viewer.post("/api/system/resume", json={}).status_code == 403
    assert client.get("/api/system/status").json()["emergency_stop"]["active"] is False
    # idempotent for the owner
    first = owner_stop(client, "first")
    again = owner_stop(client, "second")
    assert again["reason"] == "first" and again["activated_at"] == first["activated_at"]


def test_a_stopped_instance_does_not_materialize_schedules_or_wakes(
    app, client, company, scheduler, clock
):
    cid = company["id"]
    new_agent(client, cid, script={})
    owner_stop(client)
    clock.advance(24 * 3600)  # every built-in schedule is overdue
    report = scheduler.tick()
    assert report.fired_task_ids == [] and report.woke_task_ids == []
    assert report.dispatch.queued_run_ids == []
