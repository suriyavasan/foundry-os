"""Dispatcher + worker end to end with the deterministic ScriptedRuntime."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.enums import ReservationStatus
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.run import Job, TaskRun
from app.runtimes.scripted import ScriptedRuntime
from app.services import jobs
from tests.helpers import (
    api,
    get_task,
    new_agent,
    new_task,
    run_in_thread,
    set_policy,
    settle,
    wait_until,
)


def runs_of(client, cid, task_id):
    return list(reversed(client.get(api(cid, f"/tasks/{task_id}/runs")).json()["items"]))


def run_status(app, run_id):
    with app.state.session_factory() as db:
        return db.get(TaskRun, run_id).status


def only_run_id(app) -> str:
    with app.state.session_factory() as db:
        return db.scalars(select(TaskRun.id)).one()


# ------------------------------------------------------------------ success paths


def test_ready_task_runs_to_done_with_events_and_usage(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(
        client,
        cid,
        model_provider="local",
        model_name="qwen2.5:7b",
        script={
            "summary": "all good",
            "events": [{"type": "message", "payload": {"text": "hello"}}],
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "cost_micros": 4200,
                "cost_kind": "verified",
            },
        },
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    assert task["status"] == "ready"
    report = scheduler.tick()
    assert len(report.dispatch.queued_run_ids) == 1
    assert get_task(client, cid, task["id"])["status"] == "in_progress"
    assert worker.drain() == 1

    done = get_task(client, cid, task["id"])
    assert done["status"] == "done" and done["result_summary"] == "all good"
    assert done["attempt_count"] == 1 and done["started_at"] and done["completed_at"]
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "succeeded" and run["attempt_number"] == 1
    assert run["runtime"] == "scripted" and run["worker_id"] == "test-worker"
    assert run["input_tokens"] == 120 and run["output_tokens"] == 30
    assert run["cost_micros"] == 4200 and run["cost_kind"] == "verified"
    assert run["idempotency_key"] == f"{task['id']}:1:0"
    assert run["exit_reason"] == "completed" and run["finished_at"] and run["heartbeat_at"]
    events = client.get(api(cid, f"/runs/{run['id']}/events")).json()["items"]
    assert [(e["seq"], e["type"]) for e in events] == [
        (1, "run.started"),
        (2, "message"),
        (3, "run.completed"),
    ]
    with app.state.session_factory() as db:
        [usage] = db.scalars(select(UsageRecord))
        assert (usage.provider, usage.model, usage.cost_micros) == ("local", "qwen2.5:7b", 4200)
        assert usage.dedupe_key == f"run:{run['id']}" and usage.cost_kind == "verified"
        [res] = db.scalars(select(BudgetReservation))
        assert res.status == ReservationStatus.SETTLED and res.settled_micros == 4200
        assert res.run_id == run["id"] and res.amount_micros == 50_000
    # the job queue is drained and the dedupe key is the run id
    with app.state.session_factory() as db:
        [job] = db.scalars(select(Job))
        assert job.status == "done" and job.dedupe_key == run["id"] and job.attempts == 1


def test_unknown_cost_is_settled_at_the_reservation_as_an_estimate(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(client, cid, script={"usage": {"input_tokens": 5, "output_tokens": 5}})
    new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        run = db.scalars(select(TaskRun)).one()
        assert run.cost_kind == "estimated" and run.cost_micros == 50_000  # never "verified"
        usage = db.scalars(select(UsageRecord)).one()
        assert usage.cost_kind == "estimated" and usage.cost_micros == 50_000


def test_task_budget_sets_the_reservation(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    new_task(client, cid, assignee_agent_id=agent["id"], budget_micros=123_000)
    scheduler.tick()
    with app.state.session_factory() as db:
        assert db.scalars(select(BudgetReservation.amount_micros)).one() == 123_000


def test_acceptance_criteria_route_through_review(app, client, company, scheduler, worker):
    cid = company["id"]
    founder = client.get(api(cid, "/overview")).json()["founder"]
    agent = new_agent(client, cid, manager_agent_id=founder["id"])
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria="has tests")
    settle(scheduler, worker)
    in_review = get_task(client, cid, task["id"])
    assert in_review["status"] == "in_review"
    r = client.post(
        api(cid, f"/tasks/{task['id']}/review"), json={"decision": "accept", "note": "lgtm"}
    )
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert (
        client.post(
            api(cid, f"/tasks/{task['id']}/review"), json={"decision": "accept"}
        ).status_code
        == 409
    )


def test_acceptance_criteria_without_a_reviewer_complete_directly(
    client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(client, cid)  # no manager -> no resolvable reviewer
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria="has tests")
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_reviewer_cannot_be_the_assignee(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(
        client,
        cid,
        assignee_agent_id=agent["id"],
        reviewer_agent_id=agent["id"],
        acceptance_criteria="x",
    )
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_revision_loop_is_bounded_by_max_revisions(client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_revisions=1)
    founder = client.get(api(cid, "/overview")).json()["founder"]
    agent = new_agent(client, cid, manager_agent_id=founder["id"])
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria="perfect")
    review = lambda note: client.post(  # noqa: E731
        api(cid, f"/tasks/{task['id']}/review"), json={"decision": "request_revision", "note": note}
    )
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "in_review"
    r = review("first pass is rough")
    assert r.json()["status"] == "ready" and r.json()["revision_count"] == 1
    assert r.json()["attempt_count"] == 0  # a revision gets a fresh set of attempts
    settle(scheduler, worker)
    again = get_task(client, cid, task["id"])
    assert again["status"] == "in_review"
    keys = [r["idempotency_key"] for r in runs_of(client, cid, task["id"])]
    assert keys == [f"{task['id']}:1:0", f"{task['id']}:2:1"]
    r = review("still not right")
    assert r.json()["status"] == "failed" and r.json()["error"]["code"] == "max_revisions"
    assert r.json()["revision_count"] == 2


# ------------------------------------------------------------------ failure / retry


def test_failed_runs_retry_then_succeed(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid, script={"fail_first_attempts": 2, "error_message": "flaky"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    after_first = get_task(client, cid, task["id"])
    assert after_first["status"] == "ready" and after_first["attempt_count"] == 1
    assert after_first["error"]["code"] == "failed" and after_first["error"]["message"] == "flaky"
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    statuses = [r["status"] for r in runs_of(client, cid, task["id"])]
    assert statuses == ["failed", "failed", "succeeded"]


def test_task_fails_after_max_attempts(app, client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=2)
    agent = new_agent(client, cid, script={"result": "failed", "error_message": "always broken"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    failed = get_task(client, cid, task["id"])
    assert failed["status"] == "failed" and failed["attempt_count"] == 2
    assert failed["error"]["message"] == "always broken" and failed["error"]["run_id"]
    assert len(runs_of(client, cid, task["id"])) == 2
    # nothing more is ever dispatched for it
    assert scheduler.tick().dispatch.queued_run_ids == []
    with app.state.session_factory() as db:  # every failed run settled its reservation
        assert db.scalar(select(func.count()).where(BudgetReservation.status == "held")) == 0
    # a human can retry it: fresh attempts
    client.post(api(cid, f"/tasks/{task['id']}/retry"))
    settle(scheduler, worker)
    assert len(runs_of(client, cid, task["id"])) == 4
    keys = [r["idempotency_key"] for r in runs_of(client, cid, task["id"])]
    assert len(set(keys)) == 4  # retry never collides with earlier attempts


def test_runtime_exception_fails_the_run_without_leaking_details(
    client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(client, cid, script={"raise_exception": "boom api_key=sk-secret-123"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    assert worker.drain() == 1  # the worker survives a crashing runtime
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "failed" and run["error"]["code"] == "runtime_exception"
    assert "sk-secret-123" not in str(run) and "RuntimeError" in run["error"]["message"]
    assert "sk-secret-123" not in str(get_task(client, cid, task["id"]))


def test_runtime_that_cannot_execute_is_never_dispatched(app, client, company, scheduler, worker):
    cid = company["id"]
    unassigned = new_agent(client, cid, name="NoRuntime", runtime="unassigned")
    hermes_like = new_agent(client, cid, name="Hermes", runtime="hermes")
    t1 = new_task(client, cid, assignee_agent_id=unassigned["id"])
    t2 = new_task(client, cid, assignee_agent_id=hermes_like["id"])
    from app.runtimes.hermes import HermesAdapter

    app.state.runtimes.register(HermesAdapter("http://hermes.invalid", "key-123456789012345"))
    report = scheduler.tick()
    assert report.dispatch.queued_run_ids == [] and report.dispatch.waiting_for_runtime >= 2
    for t in (t1, t2):
        assert get_task(client, cid, t["id"])["status"] == "ready"
    overview = client.get(api(cid, "/overview")).json()
    assert overview["tasks"]["waiting_for_runtime"] == 2
    assert overview["tasks"]["by_status"]["ready"] == 2


def test_unhealthy_runtime_is_skipped_until_healthy(client, company, scheduler, engine, scripted):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    from app.runtimes.base import HealthStatus, RuntimeHealth

    scripted.check_health = lambda: RuntimeHealth(HealthStatus.UNAVAILABLE, "down")  # type: ignore[method-assign]
    engine.health._cache.clear()
    assert scheduler.tick().dispatch.queued_run_ids == []
    assert get_task(client, cid, task["id"])["status"] == "ready"
    scripted.check_health = lambda: RuntimeHealth(HealthStatus.OK, "up")  # type: ignore[method-assign]
    engine.health._cache.clear()
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1


# ------------------------------------------------------------------ timeout / cancel


def test_timeout_marks_run_timed_out_and_retries(app, client, company, scheduler, worker, clock):
    cid = company["id"]
    set_policy(client, cid, max_run_seconds=60)
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    run_id = wait_until(lambda: only_run_id(app) if _running(app) else None, what="run to start")
    clock.advance(61)  # past max_run_seconds, no sleeping
    thread.join(timeout=5)
    assert not thread.is_alive()
    [run] = runs_of(client, cid, task["id"])
    assert run["id"] == run_id and run["status"] == "timed_out" and run["exit_reason"] == "timeout"
    assert run["error"] == {"code": "timeout", "message": "Run exceeded 60s"}
    after = get_task(client, cid, task["id"])
    assert after["status"] == "ready" and after["error"]["code"] == "timeout"


def _running(app) -> bool:
    with app.state.session_factory() as db:
        return db.scalar(select(TaskRun.status)) == "running"


def test_cancel_run_mid_flight(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    wait_until(lambda: _running(app), what="run to start")
    run_id = only_run_id(app)
    r = client.post(api(cid, f"/runs/{run_id}/cancel"), json={"reason": "wrong approach"})
    assert r.status_code == 200 and r.json()["cancel_requested"] is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    run = client.get(api(cid, f"/runs/{run_id}")).json()
    assert run["status"] == "cancelled" and run["exit_reason"] == "cancelled_by_user"
    # a user cancelling a run blocks the task (so it is not instantly re-dispatched)
    blocked = get_task(client, cid, task["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["code"] == "run_cancelled"
    assert scheduler.tick().dispatch.queued_run_ids == []
    assert client.post(api(cid, f"/runs/{run_id}/cancel")).status_code == 409  # already over
    client.post(api(cid, f"/tasks/{task['id']}/unblock"))
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1


def test_cancelling_a_task_cancels_its_running_run(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    wait_until(lambda: _running(app), what="run to start")
    assert client.post(api(cid, f"/tasks/{task['id']}/cancel")).json()["status"] == "cancelled"
    thread.join(timeout=5)
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "cancelled" and run["cancel_reason"] == "task_cancelled"
    assert get_task(client, cid, task["id"])["status"] == "cancelled"  # untouched by the run end


def test_cancel_queued_run_is_immediate_and_releases_budget(app, client, company, scheduler):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    run_id = only_run_id(app)
    r = client.post(api(cid, f"/runs/{run_id}/cancel"))
    assert r.json()["status"] == "cancelled"
    with app.state.session_factory() as db:
        assert db.scalars(select(BudgetReservation.status)).one() == ReservationStatus.RELEASED
    assert get_task(client, cid, task["id"])["status"] == "blocked"


def test_worker_shutdown_cancels_runs_and_refunds_the_attempt(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    thread = run_in_thread(worker.run_once)
    wait_until(lambda: _running(app), what="run to start")
    worker.shutdown.set()  # what SIGTERM does
    thread.join(timeout=5)
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "cancelled" and run["exit_reason"] == "worker_shutdown"
    again = get_task(client, cid, task["id"])
    assert again["status"] == "ready" and again["attempt_count"] == 0


# ------------------------------------------------------------------ budgets


def test_budget_exceeded_blocks_the_task_and_releases_on_budget_change(
    client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, daily_budget_micros=10_000)  # less than one default reservation
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    report = scheduler.tick()
    assert report.dispatch.queued_run_ids == [] and report.dispatch.budget_blocked_task_ids == [
        task["id"]
    ]
    blocked = get_task(client, cid, task["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["code"] == "budget_exceeded"
    assert blocked["error"]["limit"] == "company_daily" and blocked["error"]["retry_after"]
    assert (
        scheduler.tick().dispatch.queued_run_ids == []
    )  # not retried while the budget is unchanged
    activity = client.get(api(cid, "/activity"), params={"target_id": task["id"]}).json()["items"]
    assert any(e["action"] == "task.blocked" and "budget" in e["summary"] for e in activity)
    overview = client.get(api(cid, "/overview")).json()
    assert overview["tasks"]["blocked_by_budget"] == 1
    # raising the budget re-evaluates blocked-by-budget tasks
    set_policy(client, cid, daily_budget_micros=500_000)
    assert get_task(client, cid, task["id"])["status"] == "ready"
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_budget_block_clears_when_the_period_rolls_over(client, company, scheduler, worker, clock):
    cid = company["id"]
    set_policy(client, cid, daily_budget_micros=60_000)
    agent = new_agent(client, cid)
    first = new_task(client, cid, title="first", assignee_agent_id=agent["id"])
    second = new_task(client, cid, title="second", assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()  # first runs (reserved 50k, settled at the estimate 50k)
    assert get_task(client, cid, first["id"])["status"] == "done"
    scheduler.tick()
    blocked = get_task(client, cid, second["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["code"] == "budget_exceeded"
    clock.advance(hours=25)  # tomorrow
    scheduler.tick()  # recovery (>30 s since last) releases tasks whose retry_after passed
    settle(scheduler, worker)
    assert get_task(client, cid, second["id"])["status"] == "done"


def test_agent_budget_blocks_only_that_agents_tasks(client, company, scheduler, worker):
    cid = company["id"]
    poor = new_agent(client, cid, name="Poor", daily_budget_micros=1_000)
    rich = new_agent(client, cid, name="Rich")
    t_poor = new_task(client, cid, assignee_agent_id=poor["id"])
    t_rich = new_task(client, cid, assignee_agent_id=rich["id"])
    settle(scheduler, worker)
    assert get_task(client, cid, t_poor["id"])["status"] == "blocked"
    assert get_task(client, cid, t_rich["id"])["status"] == "done"


def test_exhausted_task_budget_blocks_a_retry(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(
        client,
        cid,
        script={
            "fail_first_attempts": 1,
            "usage": {"cost_micros": 100_000, "cost_kind": "verified"},
        },
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"], budget_micros=100_000)
    scheduler.tick()
    worker.drain()
    assert get_task(client, cid, task["id"])["status"] == "ready"  # failed once, 100k spent
    scheduler.tick()
    blocked = get_task(client, cid, task["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["limit"] == "task_budget"


# ------------------------------------------------------------------ dispatch policy


def test_max_concurrent_runs_is_respected(app, client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=2)
    agents = [new_agent(client, cid, name=f"A{i}") for i in range(4)]
    tasks = [
        new_task(client, cid, title=f"t{i}", assignee_agent_id=a["id"])
        for i, a in enumerate(agents)
    ]
    report = scheduler.tick()
    assert len(report.dispatch.queued_run_ids) == 2
    statuses = sorted(get_task(client, cid, t["id"])["status"] for t in tasks)
    assert statuses == ["in_progress", "in_progress", "ready", "ready"]
    assert scheduler.tick().dispatch.queued_run_ids == []  # queued runs still count
    assert worker.drain() == 2
    assert len(scheduler.tick().dispatch.queued_run_ids) == 2
    worker.drain()
    assert all(get_task(client, cid, t["id"])["status"] == "done" for t in tasks)


def test_one_run_per_agent_at_a_time(client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=5)
    agent = new_agent(client, cid)
    for i in range(3):
        new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1
    settle(scheduler, worker)
    assert all(t["status"] == "done" for t in client.get(api(cid, "/tasks")).json()["items"])


def test_dispatch_order_is_priority_then_age(client, company, scheduler):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=1)
    agent = new_agent(client, cid)
    low = new_task(client, cid, title="low", priority="low", assignee_agent_id=agent["id"])
    normal = new_task(client, cid, title="normal", assignee_agent_id=agent["id"])
    urgent = new_task(client, cid, title="urgent", priority="urgent", assignee_agent_id=agent["id"])
    high = new_task(client, cid, title="high", priority="high", assignee_agent_id=agent["id"])
    scheduler.tick()
    assert get_task(client, cid, urgent["id"])["status"] == "in_progress"
    for t in (low, normal, high):
        assert get_task(client, cid, t["id"])["status"] == "ready"


def test_dependent_tasks_run_in_order(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    a = new_task(client, cid, title="a", assignee_agent_id=agent["id"])
    b = new_task(client, cid, title="b", assignee_agent_id=agent["id"], depends_on=[a["id"]])
    c = new_task(client, cid, title="c", assignee_agent_id=agent["id"], depends_on=[b["id"]])
    settle(scheduler, worker)
    fa, fb, fc = (get_task(client, cid, t["id"]) for t in (a, b, c))
    assert fa["status"] == fb["status"] == fc["status"] == "done"
    assert fa["completed_at"] <= fb["completed_at"] <= fc["completed_at"]
    assert fa["started_at"] <= fb["started_at"] <= fc["started_at"]


def test_paused_agent_is_not_dispatched(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    client.post(api(cid, f"/agents/{agent['id']}/pause"))
    assert scheduler.tick().dispatch.queued_run_ids == []
    client.post(api(cid, f"/agents/{agent['id']}/resume"))
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_scripted_runtime_artifacts_are_stored(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(
        client,
        cid,
        script={"artifacts": [{"title": "Plan", "content": "# The plan", "kind": "report"}]},
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    done = get_task(client, cid, task["id"])
    [artifact] = client.get(api(cid, "/artifacts"), params={"task_id": task["id"]}).json()["items"]
    assert done["output_refs"] == [artifact["id"]]
    assert artifact["created_by_type"] == "agent" and artifact["kind"] == "report"
    content = client.get(api(cid, f"/artifacts/{artifact['id']}/versions/1/content"))
    assert content.status_code == 200 and content.text == "# The plan"


# ------------------------------------------------------------------ events, security, jobs


def test_run_events_are_redacted_before_they_are_stored(
    app, client, company, scheduler, worker, scripted
):
    cid = company["id"]
    # (runtime_config may not carry secret-looking keys, so script this one programmatically)
    scripted._default = {
        "events": [
            {"type": "tool.completed", "payload": {
                "api_key": "sk-live-1", "output": "ok",
                "headers": {"Authorization": "Bearer abc", "accept": "json"},
                "items": [{"password": "hunter2", "n": 1}],
            }}
        ]
    }  # fmt: skip
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    [run] = runs_of(client, cid, task["id"])
    events = client.get(api(cid, f"/runs/{run['id']}/events")).json()["items"]
    payload = next(e for e in events if e["type"] == "tool.completed")["payload"]
    assert payload["api_key"] == "[redacted]" and payload["output"] == "ok"
    assert payload["headers"] == {"Authorization": "[redacted]", "accept": "json"}
    assert payload["items"] == [{"password": "[redacted]", "n": 1}]
    assert "sk-live-1" not in client.get(api(cid, f"/runs/{run['id']}/events")).text
    # and the live outbox never carried the raw payload at all
    from app.models.run import CompanyEvent

    with app.state.session_factory() as db:
        blob = " ".join(str(e.payload) for e in db.scalars(select(CompanyEvent)))
    assert "sk-live-1" not in blob and "hunter2" not in blob


def test_run_token_is_valid_only_during_the_run(app, client, company, scheduler, worker):
    """The runtime gets a token that works against the agent API while it runs, not after."""
    cid = company["id"]
    seen: dict = {}

    class CallsBack(ScriptedRuntime):
        id = "callsback"

        def execute(self, request, sink, cancel):
            headers = {"Authorization": f"Bearer {request.run_token}"}
            seen["token"] = request.run_token
            seen["context"] = client.get("/api/agent/v1/context", headers=headers).status_code
            sub = client.post(
                "/api/agent/v1/tasks", json={"title": "from inside the run"}, headers=headers
            )
            seen["created"] = sub.status_code
            seen["workspace"] = request.workspace_dir
            seen["api_url"] = request.agent_api_url
            return super().execute(request, sink, cancel)

    app.state.runtimes.register(CallsBack())
    agent = new_agent(client, cid, runtime="callsback")
    new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert seen["context"] == 200 and seen["created"] == 201
    after = client.get(
        "/api/agent/v1/context", headers={"Authorization": f"Bearer {seen['token']}"}
    )
    assert after.status_code == 401
    import os

    assert os.path.isdir(seen["workspace"]) and cid in seen["workspace"]
    # the token is never persisted anywhere readable
    assert seen["token"] not in client.get(api(cid, "/activity")).text
    with app.state.session_factory() as db:
        from app.models.run import RunEvent

        assert seen["token"] not in " ".join(str(e.payload) for e in db.scalars(select(RunEvent)))


def test_redelivered_job_does_not_run_twice(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    run_id = only_run_id(app)
    assert worker.executor.execute(run_id) == "succeeded"
    assert worker.executor.execute(run_id) == "skipped"  # job delivered again
    assert len(runs_of(client, cid, task["id"])) == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(UsageRecord.id))) == 1


def test_failed_jobs_back_off_then_die(app, engine, worker, clock):
    with app.state.session_factory() as db:
        job = jobs.enqueue(db, kind="execute_run", payload={}, max_attempts=3, run_after=clock())
        db.commit()
        job_id = job.id
    delays = []
    for attempt in (1, 2, 3):
        assert worker.run_once()  # payload has no run_id -> KeyError -> job.fail
        with app.state.session_factory() as db:
            row = db.get(Job, job_id)
            assert row.attempts == attempt
            if attempt < 3:
                assert row.status == "queued" and row.last_error.startswith("KeyError")
                delays.append((row.run_after - clock()).total_seconds())
                clock.advance(row.run_after.timestamp() - clock().timestamp() + 1)
            else:
                assert row.status == "dead"
    assert delays == [5, 10]  # exponential backoff
    assert not worker.run_once()  # dead jobs are never leased again


def test_unknown_job_kinds_go_straight_to_dead(app, engine, clock):
    from app.engine.worker import Worker

    with app.state.session_factory() as db:
        jobs.enqueue(db, kind="mystery", payload={}, run_after=clock())
        db.commit()
    w = Worker(engine, worker_id="w2")
    leased = jobs.lease_next(engine.session_factory, "w2", clock())
    assert leased and leased.kind == "mystery"
    w.handle(leased)
    with app.state.session_factory() as db:
        assert db.scalars(select(Job.status)).one() == "dead"


def test_job_leasing_is_exclusive(app, engine, clock):
    with app.state.session_factory() as db:
        jobs.enqueue(db, kind="execute_run", payload={"run_id": "x"}, run_after=clock())
        db.commit()
    first = jobs.lease_next(engine.session_factory, "w1", clock())
    second = jobs.lease_next(engine.session_factory, "w2", clock())
    assert first is not None and second is None
    assert jobs.lease_next(engine.session_factory, "w3", clock()) is None


def test_job_dedupe_key_returns_existing_job(app):
    with app.state.session_factory() as db:
        a = jobs.enqueue(db, kind="k", payload={}, dedupe_key="same")
        b = jobs.enqueue(db, kind="k", payload={"x": 1}, dedupe_key="same")
        db.commit()
        assert a.id == b.id
        assert db.scalar(select(func.count(Job.id))) == 1


def test_live_outbox_carries_the_run_story_in_order(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(
        client, cid, script={"events": [{"type": "message", "payload": {"text": "hi"}}]}
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    from app.models.run import CompanyEvent

    with app.state.session_factory() as db:
        rows = list(db.scalars(select(CompanyEvent).order_by(CompanyEvent.id)))
    types = [e.type for e in rows]
    for needed in (
        "task.created",
        "run.queued",
        "run.started",
        "run.event",
        "agent.activity",
        "run.finished",
    ):
        assert needed in types, needed
    assert types.index("run.queued") < types.index("run.started") < types.index("run.finished")
    ids = [e.id for e in rows]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(e.company_id == cid for e in rows)
    finished = next(e for e in rows if e.type == "run.finished")
    assert finished.payload["task_id"] == task["id"] and finished.payload["status"] == "succeeded"


def test_agent_live_state_follows_the_run(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid, script={"block_until_cancel": True,
                                           "events": [{"type": "message", "payload": {"summary": "reading the brief"}}]})  # fmt: skip
    live = lambda: client.get(api(cid, f"/agents/{agent['id']}/activity-now")).json()  # noqa: E731
    assert live()["state"] == "idle"
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    queued = live()
    assert queued["state"] == "working" and queued["summary"].startswith("Queued")
    thread = run_in_thread(worker.run_once)
    wait_until(lambda: live()["summary"] == "reading the brief", what="activity summary")
    working = live()
    assert working["current_task"] == {"id": task["id"], "title": task["title"]}
    assert working["current_run"]["status"] == "running" and working["since"]
    listing = client.get(api(cid, "/agents")).json()["items"]
    assert next(a for a in listing if a["id"] == agent["id"])["live"]["state"] == "working"
    assert client.get(api(cid, f"/agents/{agent['id']}")).json()["live"]["state"] == "working"
    client.post(api(cid, f"/tasks/{task['id']}/cancel"))
    thread.join(timeout=5)
    assert live()["state"] == "idle"


def test_model_provider_allow_list_is_enforced_at_dispatch(client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, allowed_model_providers=["ollama"])
    ok = new_agent(client, cid, name="Local", model_provider="ollama")
    nope = new_agent(client, cid, name="Cloud", model_provider="openai")
    t_ok = new_task(client, cid, assignee_agent_id=ok["id"])
    t_no = new_task(client, cid, assignee_agent_id=nope["id"])
    settle(scheduler, worker)
    assert get_task(client, cid, t_ok["id"])["status"] == "done"
    blocked = get_task(client, cid, t_no["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["code"] == "provider_not_allowed"
    # widening the allow-list (a human decision) frees the task
    set_policy(client, cid, allowed_model_providers=["ollama", "openai"])
    settle(scheduler, worker)
    assert get_task(client, cid, t_no["id"])["status"] == "done"
    # an empty list means any provider
    set_policy(client, cid, allowed_model_providers=[])
    assert client.get(api(cid, "/policy")).json()["allowed_model_providers"] == []
