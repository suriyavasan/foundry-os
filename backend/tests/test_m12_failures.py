"""M12 WP2: transient vs permanent failures, backoff, the circuit breaker and its auto-resume,
agent retry limits and the review-decision guard. Deterministic: ScriptedRuntime + the fake clock."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.engine import recovery
from app.models.audit import AuditEvent
from app.models.run import TaskRun
from app.models.work import Task
from app.services import retry_policy, reviews
from tests.autonomy_helpers import scripted_world
from tests.helpers import api, get_task, new_agent, new_task, set_policy, settle
from tests.test_agent_api import AgentSession, start_run

TRANSIENT_SCRIPT = {"result": "failed", "error_message": "429 Too Many Requests"}
PERMANENT_SCRIPT = {"result": "failed", "error_message": "the model returned garbage"}


def row(app, task_id) -> Task:
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def run_count(app, task_id) -> int:
    with app.state.session_factory() as db:
        return len(list(db.scalars(select(TaskRun.id).where(TaskRun.task_id == task_id))))


# ------------------------------------------------------------------ classification and backoff


@pytest.mark.parametrize(
    "code,message",
    [
        ("failed", "HTTP 429 Too Many Requests"),
        ("failed", "Rate limit reached for model"),
        ("failed", "rate_limit_error: slow down"),
        ("failed", "The server is overloaded, try again"),
        ("failed", "529 overloaded_error"),
        ("failed", "upstream returned 502 Bad Gateway"),
        ("failed", "503 Service Unavailable"),
        ("failed", "504 gateway timeout"),
        ("failed", "Connection reset by peer"),
        ("failed", "connection refused"),
        ("failed", "Service temporarily unavailable"),
        ("unavailable", ""),
        ("worker_lost", "The worker stopped heartbeating"),
        ("job_dead", "could not be completed"),
        ("merge_conflict", "The run's changes conflict with the project"),
    ],
)
def test_transient_failures_are_recognised(code, message):
    assert retry_policy.classify(code, message) == retry_policy.TRANSIENT


@pytest.mark.parametrize(
    "code,message",
    [
        ("failed", "the model returned garbage"),
        ("timeout", "Run exceeded 60s"),
        ("runtime_exception", "RuntimeError: boom"),
        ("failed", "wrote 15032 bytes"),  # 503 inside a number is not an HTTP status
        ("failed", "budget cap reached"),
        ("config_error", "no API key"),
    ],
)
def test_other_failures_are_permanent(code, message):
    assert retry_policy.classify(code, message) == retry_policy.PERMANENT


def test_backoff_schedules_have_no_jitter():
    assert [retry_policy.backoff("permanent", n) for n in (1, 2, 3, 4, 9)] == [0, 60, 240, 900, 900]
    assert [retry_policy.backoff("transient", n) for n in (1, 2, 3, 4, 5, 6, 7, 30)] == [
        30,
        60,
        120,
        240,
        480,
        900,
        900,
        900,
    ]


# ------------------------------------------------------------------ transient failures


def test_a_429_refunds_the_attempt_waits_and_then_succeeds(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=1)  # one attempt: a 429 must not spend it
    agent = new_agent(
        client,
        cid,
        script={
            "fail_first_attempts": 1,
            "error_message": "429 Too Many Requests",
            "summary": "ok",
        },
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    after = get_task(client, cid, task["id"])
    assert after["status"] == "ready" and after["attempt_count"] == 0
    assert after["error"]["message"] == "429 Too Many Requests"
    stored = row(app, task["id"])
    assert stored.transient_failures == 1
    assert stored.next_attempt_at == clock() + timedelta(seconds=30)
    # not dispatched before it is due (the fake clock only moves when told to)
    assert scheduler.tick().dispatch.queued_run_ids == []
    clock.advance(29)
    assert scheduler.tick().dispatch.queued_run_ids == []
    clock.advance(1)
    settle(scheduler, worker)
    done = get_task(client, cid, task["id"])
    assert done["status"] == "done" and done["attempt_count"] == 1
    cleared = row(app, task["id"])
    assert cleared.transient_failures == 0 and cleared.next_attempt_at is None
    audit = client.get(api(cid, "/activity"), params={"target_id": task["id"]}).json()["items"]
    assert any("transient failure" in e["summary"] for e in audit)


def test_transient_backoff_doubles_and_the_seventh_failure_spends_the_attempt(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=2, circuit_breaker_failures=0)
    agent = new_agent(client, cid, script=TRANSIENT_SCRIPT)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    waits = []
    for _ in range(6):
        scheduler.tick()
        worker.drain()
        stored = row(app, task["id"])
        waits.append(int((stored.next_attempt_at - clock()).total_seconds()))
        assert stored.attempt_count == 0  # never spent
        clock.advance(waits[-1])
    assert waits == [30, 60, 120, 240, 480, 900]
    # the 7th consecutive transient failure no longer refunds: a permanent-style failure
    scheduler.tick()
    worker.drain()
    stored = row(app, task["id"])
    assert stored.attempt_count == 1 and stored.transient_failures == 0
    assert stored.status == "ready" and stored.next_attempt_at is None  # attempt 1: immediate


def test_transient_failures_never_trip_the_circuit_breaker(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(
        client, cid, circuit_breaker_failures=3, max_concurrent_runs=6, management_reserved_slots=0
    )
    for i in range(5):
        agent = new_agent(client, cid, name=f"A{i}", script=TRANSIENT_SCRIPT)
        new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])
    scheduler.tick()
    assert worker.drain() == 5
    with app.state.session_factory() as db:
        failed = db.scalars(select(TaskRun).where(TaskRun.status == "failed")).all()
    assert len(failed) == 5  # five failed runs within the window, more than the threshold of 3 ...
    assert client.get(api(cid)).json()["status"] == "active"  # ... and the company keeps going
    assert client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"] == []


def test_permanent_failures_trip_the_breaker(app, client, company, scheduler, worker, clock):
    cid = company["id"]
    set_policy(
        client, cid, circuit_breaker_failures=3, max_concurrent_runs=6, management_reserved_slots=0
    )
    for i in range(3):
        agent = new_agent(client, cid, name=f"A{i}", script=PERMANENT_SCRIPT)
        new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    assert client.get(api(cid)).json()["status"] == "paused"


# ------------------------------------------------------------------ permanent failures


def test_permanent_failures_follow_the_backoff_schedule(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=4, circuit_breaker_failures=0)
    agent = new_agent(client, cid, script=PERMANENT_SCRIPT)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    seen = []
    for _ in range(3):
        scheduler.tick()
        worker.drain()
        stored = row(app, task["id"])
        seen.append(
            None
            if stored.next_attempt_at is None
            else int((stored.next_attempt_at - clock()).total_seconds())
        )
        assert stored.status == "ready"
        if seen[-1]:
            assert scheduler.tick().dispatch.queued_run_ids == []  # waits
            clock.advance(seen[-1])
    assert seen == [None, 60, 240]  # immediate once, then 60 s, then 240 s
    scheduler.tick()
    worker.drain()
    failed = row(app, task["id"])
    assert failed.status == "failed" and failed.attempt_count == 4
    assert failed.next_attempt_at is None  # terminal: nothing is waiting any more


def test_a_human_retry_clears_the_backoff_and_every_counter(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=2, circuit_breaker_failures=0)
    agent = new_agent(client, cid, script=PERMANENT_SCRIPT)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    clock.advance(0)
    scheduler.tick()
    worker.drain()  # second attempt: terminal (max 2)
    assert get_task(client, cid, task["id"])["status"] == "failed"
    with app.state.session_factory() as db:
        t = db.get(Task, task["id"])
        t.agent_retry_count, t.transient_failures = 2, 3
        db.commit()
    assert client.post(api(cid, f"/tasks/{task['id']}/retry")).status_code == 200
    reset = row(app, task["id"])
    assert (reset.agent_retry_count, reset.transient_failures, reset.attempt_count) == (0, 0, 0)
    assert reset.next_attempt_at is None


# ------------------------------------------------------------------ breaker auto-resume


def trip(client, cid, scheduler, worker, tag: str) -> None:
    """Two fresh agents fail permanently within one tick: the breaker (threshold 2) trips."""
    for i in range(2):
        agent = new_agent(client, cid, name=f"{tag}{i}", script=PERMANENT_SCRIPT)
        new_task(client, cid, title=f"{tag} task {i}", assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()


def status_of(client, cid) -> str:
    return client.get(api(cid)).json()["status"]


def actions_of(app, cid, action) -> list[AuditEvent]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action == action)
            )
        )


def test_the_breaker_resumes_after_the_cooldown_at_most_twice(
    app, client, company, scheduler, worker, clock, engine
):
    cid = company["id"]
    set_policy(
        client,
        cid,
        circuit_breaker_failures=2,
        circuit_breaker_cooldown_seconds=1800,
        max_concurrent_runs=6,
        management_reserved_slots=0,
    )
    trip(client, cid, scheduler, worker, "a")
    assert status_of(client, cid) == "paused"
    clock.advance(1799)
    assert recovery.recover(engine).auto_resumed_company_ids == []  # still cooling down
    assert status_of(client, cid) == "paused"
    clock.advance(1)
    assert recovery.recover(engine).auto_resumed_company_ids == [cid]
    assert status_of(client, cid) == "active"
    [resumed] = actions_of(app, cid, "company.auto_resumed")
    assert resumed.actor_type == "system" and "resumed itself" in resumed.summary
    # the failed runs of the first spike are outside the 10 minute window by now
    clock.advance(700)
    trip(client, cid, scheduler, worker, "b")
    assert status_of(client, cid) == "paused"
    clock.advance(1800)
    assert recovery.recover(engine).auto_resumed_company_ids == [cid]  # the second and last
    clock.advance(700)
    trip(client, cid, scheduler, worker, "c")
    assert status_of(client, cid) == "paused"
    clock.advance(1800)  # past the cooldown, still inside 24 h of both resumes
    assert recovery.recover(engine).auto_resumed_company_ids == []
    assert status_of(client, cid) == "paused"
    alerts = client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"]
    assert "circuit_breaker" in [a["kind"] for a in alerts]  # a human has to look now
    assert client.post(api(cid, "/resume")).status_code == 200
    assert status_of(client, cid) == "active"
    # and after 24 h the allowance is back
    clock.advance(60 * 60 * 24)
    trip(client, cid, scheduler, worker, "d")
    clock.advance(1800)
    assert recovery.recover(engine).auto_resumed_company_ids == [cid]


def test_a_company_a_human_paused_is_never_resumed_automatically(
    app, client, company, clock, engine
):
    cid = company["id"]
    set_policy(client, cid, circuit_breaker_cooldown_seconds=60)
    assert client.post(api(cid, "/pause")).status_code == 200
    clock.advance(86_400)
    assert recovery.recover(engine).auto_resumed_company_ids == []
    assert status_of(client, cid) == "paused"


def test_a_zero_cooldown_means_a_human_resumes(
    app, client, company, scheduler, worker, clock, engine
):
    cid = company["id"]
    set_policy(
        client,
        cid,
        circuit_breaker_failures=2,
        circuit_breaker_cooldown_seconds=0,
        max_concurrent_runs=6,
        management_reserved_slots=0,
    )
    trip(client, cid, scheduler, worker, "z")
    assert status_of(client, cid) == "paused"
    clock.advance(10 * 86_400)
    assert recovery.recover(engine).auto_resumed_company_ids == []
    assert status_of(client, cid) == "paused"


def test_a_human_pause_after_an_auto_resume_stays_paused(
    app, client, company, scheduler, worker, clock, engine
):
    cid = company["id"]
    set_policy(
        client,
        cid,
        circuit_breaker_failures=2,
        circuit_breaker_cooldown_seconds=60,
        max_concurrent_runs=6,
        management_reserved_slots=0,
    )
    trip(client, cid, scheduler, worker, "h")
    clock.advance(61)
    assert recovery.recover(engine).auto_resumed_company_ids == [cid]
    assert client.post(api(cid, "/pause")).status_code == 200
    clock.advance(3600)
    assert recovery.recover(engine).auto_resumed_company_ids == []
    assert status_of(client, cid) == "paused"


# ------------------------------------------------------------------ agent retries and reviews


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def force(app, task_id, **values) -> None:
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        for key, value in values.items():
            setattr(task, key, value)
        db.commit()


def test_an_agent_may_retry_a_failed_task_twice_then_must_escalate(app, client, w):
    cid = w.cid
    victim = new_task(client, cid, title="Flaky job", assignee_agent_id=w.specialist["id"])
    sess = start_run(app, client, cid, w.founder["id"], task_title="Plan", kind="planning")
    for attempt in (1, 2):
        force(app, victim["id"], status="failed")
        r = sess.post(f"/tasks/{victim['id']}/retry", json={"reason": "again"})
        assert r.status_code == 200, r.text
        assert row(app, victim["id"]).agent_retry_count == attempt
    force(app, victim["id"], status="failed")
    r = sess.post(f"/tasks/{victim['id']}/retry", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    assert r.json()["error"]["details"]["limit"] == "max_agent_retries"
    assert "escalate" in r.json()["error"]["message"]
    assert row(app, victim["id"]).status == "failed"
    # a human retry resets the count and the agent may retry again
    assert client.post(api(cid, f"/tasks/{victim['id']}/retry")).status_code == 200
    assert row(app, victim["id"]).agent_retry_count == 0
    force(app, victim["id"], status="failed")
    assert sess.post(f"/tasks/{victim['id']}/retry", json={}).status_code == 200


def review_session(app, client, w) -> tuple[AgentSession, str, str]:
    """The lead mid-run on the review task of a task that waits in review."""
    cid = w.cid
    target = new_task(
        client,
        cid,
        title="Write the brief",
        assignee_agent_id=w.specialist["id"],
        reviewer_agent_id=w.lead["id"],
        acceptance_criteria="One page",
    )
    force(app, target["id"], status="in_review", attempt_count=1)
    with app.state.session_factory() as db:
        review = reviews.ensure_review_task(db, db.get(Task, target["id"]), None)
        db.commit()
        review_id = review.id
    force(app, review_id, status="in_progress", attempt_count=1)
    other = start_run(app, client, cid, w.lead["id"], task_title="scratch")
    with app.state.session_factory() as db:  # point the lead's run at the review task
        run = db.get(TaskRun, other.run_id)
        run.task_id = review_id
        db.commit()
    return (
        AgentSession(
            client, other.token, run_id=other.run_id, task_id=review_id, agent_id=w.lead["id"]
        ),
        review_id,
        target["id"],
    )  # noqa: E501


def test_a_review_task_cannot_be_completed_without_a_decision(app, client, w):
    sess, review_id, target_id = review_session(app, client, w)
    r = sess.post(f"/tasks/{review_id}/complete", json={"result_summary": "looks fine"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "review_decision_required"
    assert target_id in r.json()["error"]["message"]
    assert "company_review_task" in r.json()["error"]["message"]
    assert row(app, review_id).status == "in_progress" and row(app, target_id).status == "in_review"
    # record the decision, then completing is a harmless no-op (the review task is already done)
    assert sess.post(f"/tasks/{target_id}/review", json={"decision": "accept"}).status_code == 200
    assert row(app, target_id).status == "done"
    assert (
        sess.post(f"/tasks/{review_id}/complete", json={"result_summary": "done"}).status_code
        == 200
    )
    assert row(app, review_id).status == "done"


def test_a_review_task_whose_target_was_already_decided_can_complete(app, client, w):
    sess, review_id, target_id = review_session(app, client, w)
    force(app, target_id, status="done")  # a human decided in the meantime
    r = sess.post(f"/tasks/{review_id}/complete", json={"result_summary": "nothing left"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ merge conflicts (WP4)

CONFLICT = {"code": "merge_conflict", "message": "Changes conflict with work merged since"}


def start(client, cid, scheduler, agent, **task_fields):
    """A queued run for a fresh task of ``agent`` (nothing executes it)."""
    task = new_task(client, cid, assignee_agent_id=agent["id"], **task_fields)
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1
    return task


def conflict_run(app, task_id, clock):
    """Finish the task's active run as the executor does after a merge conflict."""
    from app.models.enums import RunStatus
    from app.services import runs

    with app.state.session_factory() as db:
        run_id = db.scalars(select(TaskRun.id).where(TaskRun.task_id == task_id)).one()
    with app.state.session_factory() as db:
        outcome = runs.RunOutcome(
            RunStatus.FAILED, "merge_conflict", error=dict(CONFLICT), executed=True
        )
        assert runs.finalize_run(db, run_id, outcome, now=clock()) is not None


def raise_conflict_alert(app, cid, task_id):
    from app.services import alerts

    with app.state.session_factory() as db:
        alerts.raise_alert(
            db,
            cid,
            kind="merge_conflict",
            title="Finished work is not in the project",
            dedupe_key=f"merge_conflict:{task_id}",
            wake_founder=False,
        )
        db.commit()


def open_alert_keys(client, cid) -> set[str]:
    items = client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"]
    return {a["kind"] for a in items}


def test_a_merge_conflict_reopens_a_task_the_cli_already_finished(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    agent = new_agent(client, cid, script={"summary": "redone"})
    task = start(client, cid, scheduler, agent)
    force_done(app, task["id"])  # the CLI completed the task before the run ended
    raise_conflict_alert(app, cid, task["id"])
    conflict_run(app, task["id"], clock)
    after = get_task(client, cid, task["id"])
    assert after["status"] == "ready" and after["attempt_count"] == 1
    assert after["completed_at"] is None
    assert after["error"]["code"] == "merge_conflict"
    actions = [e.action for e in audit_events(app, cid)]
    assert "task.reopened_merge_conflict" in actions
    assert "merge_conflict" not in open_alert_keys(client, cid)  # it can retry: no alert needed
    settle(scheduler, worker)  # the retry starts from the new project files and completes
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert run_count(app, task["id"]) == 2


def force_done(app, task_id, status="done"):
    from app.core.time import utcnow

    with app.state.session_factory() as db:
        t = db.get(Task, task_id)
        t.status = status
        t.completed_at = utcnow() if status == "done" else None
        db.commit()


def audit_events(app, cid):
    with app.state.session_factory() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid)))


def test_a_merge_conflict_reopens_a_task_in_review_and_closes_its_review(app, client, w, scheduler):
    cid = w.cid
    task = start(
        client,
        cid,
        scheduler,
        w.specialist,
        title="Write the brief",
        reviewer_agent_id=w.lead["id"],
        acceptance_criteria="One page",
    )
    force_done(app, task["id"], "in_review")
    with app.state.session_factory() as db:
        review = reviews.ensure_review_task(db, db.get(Task, task["id"]), None)
        db.commit()
        review_id = review.id
    from tests.helpers import FakeClock

    conflict_run(app, task["id"], FakeClock())
    assert row(app, task["id"]).status in ("ready", "backlog")
    assert row(app, review_id).status in ("cancelled", "done")  # no review of reopened work
    assert "task.reopened_merge_conflict" in [e.action for e in audit_events(app, cid)]


def test_reopening_respects_the_attempt_cap(app, client, company, scheduler, clock):
    cid = company["id"]
    set_policy(client, cid, max_attempts_per_task=1)
    agent = new_agent(client, cid)
    task = start(client, cid, scheduler, agent)
    force_done(app, task["id"])
    conflict_run(app, task["id"], clock)
    failed = get_task(client, cid, task["id"])
    assert failed["status"] == "failed" and failed["error"]["code"] == "merge_conflict"
    assert "task_failed" in open_alert_keys(client, cid)


def test_a_cancelled_or_blocked_task_is_not_reopened(app, client, company, scheduler, clock):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = start(client, cid, scheduler, agent)
    force_done(app, task["id"], "cancelled")
    raise_conflict_alert(app, cid, task["id"])
    conflict_run(app, task["id"], clock)
    assert get_task(client, cid, task["id"])["status"] == "cancelled"
    assert "task.reopened_merge_conflict" not in [e.action for e in audit_events(app, cid)]
    assert "merge_conflict" in open_alert_keys(client, cid)  # a person still has to look


def test_a_merge_conflict_of_a_running_task_consumes_an_attempt_and_retries_at_once(
    app, client, company, scheduler, clock
):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = start(client, cid, scheduler, agent)
    conflict_run(app, task["id"], clock)  # still in_progress: the normal failure path
    after = row(app, task["id"])
    assert after.status == "ready" and after.attempt_count == 1
    assert after.transient_failures == 0 and after.next_attempt_at is None
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1


def test_merge_conflicts_never_trip_the_circuit_breaker(app, client, company, scheduler, clock):
    cid = company["id"]
    set_policy(
        client, cid, circuit_breaker_failures=2, max_concurrent_runs=6, management_reserved_slots=0
    )
    for i in range(3):
        agent = new_agent(client, cid, name=f"A{i}")
        new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])
    scheduler.tick()
    with app.state.session_factory() as db:
        task_ids = list(db.scalars(select(TaskRun.task_id)))
    assert len(task_ids) == 3
    for task_id in task_ids:
        force_done(app, task_id)
        with app.state.session_factory() as db:
            run_id = db.scalars(select(TaskRun.id).where(TaskRun.task_id == task_id)).one()
        from app.models.enums import RunStatus
        from app.services import runs

        with app.state.session_factory() as db:
            runs.finalize_run(
                db,
                run_id,
                runs.RunOutcome(RunStatus.FAILED, "merge_conflict", error=dict(CONFLICT)),
                now=clock(),
            )
    assert status_of(client, cid) == "active"
