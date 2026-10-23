"""Crash recovery and schedules: restarts, lost workers, expired leases, exactly-once firing."""

from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import func, select, update

from app.engine import recovery
from app.engine.scheduler import Scheduler
from app.models.ledger import BudgetReservation
from app.models.run import Job, TaskRun
from app.models.schedule import Schedule, ScheduleFire
from app.models.work import Task
from app.services import jobs, schedules
from tests.helpers import api, get_task, new_agent, new_task, settle

# ------------------------------------------------------------------ recovery


def _simulate_crashed_worker(app, clock, *, heartbeat_age: int) -> str:
    """Leave a run 'running' with a stale heartbeat and a leased job, as a SIGKILL would."""
    with app.state.session_factory() as db:
        run = db.scalars(select(TaskRun)).one()
        run.status = "running"
        run.worker_id = "dead-worker"
        run.started_at = clock()
        run.heartbeat_at = clock() - timedelta(seconds=heartbeat_age)
        db.execute(
            update(Job).values(
                status="leased", leased_by="dead-worker", lease_expires_at=clock(), attempts=1
            )
        )
        db.commit()
        return run.id


def test_stale_running_run_is_failed_and_the_task_retried(
    app, client, company, scheduler, worker, engine, clock
):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    run_id = _simulate_crashed_worker(app, clock, heartbeat_age=120)

    clock.advance(1)
    report = recovery.recover(engine)
    assert report.stale_run_ids == [run_id]
    with app.state.session_factory() as db:
        run = db.get(TaskRun, run_id)
        assert run.status == "failed" and run.exit_reason == "worker_lost"
        assert run.error["code"] == "worker_lost"
        res = db.scalars(select(BudgetReservation)).one()
        assert res.status == "released"  # not charged for work we cannot account for
    recovered = get_task(client, cid, task["id"])
    assert recovered["status"] == "ready" and recovered["error"]["code"] == "worker_lost"
    assert recovered["attempt_count"] == 1
    # the retry goes through the normal path and completes
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    statuses = [
        r["status"]
        for r in reversed(client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"])
    ]
    assert statuses == ["failed", "succeeded"]


def test_fresh_heartbeat_is_left_alone(app, client, company, scheduler, engine, clock):
    cid = company["id"]
    agent = new_agent(client, cid)
    new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    run_id = _simulate_crashed_worker(app, clock, heartbeat_age=10)
    assert recovery.recover(engine).stale_run_ids == []
    with app.state.session_factory() as db:
        assert db.get(TaskRun, run_id).status == "running"
    clock.advance(25)  # now 35 s without a heartbeat
    assert recovery.recover(engine).stale_run_ids == [run_id]


def test_worker_that_finishes_after_recovery_does_not_double_settle(
    app, client, company, scheduler, engine, worker, clock
):
    from app.models.enums import RunStatus
    from app.services import runs

    cid = company["id"]
    agent = new_agent(client, cid)
    new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    run_id = _simulate_crashed_worker(app, clock, heartbeat_age=120)
    recovery.recover(engine)
    with app.state.session_factory() as db:  # the zombie worker wakes up and reports success
        late = runs.finalize_run(
            db, run_id, runs.RunOutcome(RunStatus.SUCCEEDED, "completed"), now=clock()
        )
    assert late is None
    with app.state.session_factory() as db:
        assert db.get(TaskRun, run_id).status == "failed"
        assert db.scalar(select(func.count(BudgetReservation.id))) == 1


def test_expired_job_lease_is_requeued_and_picked_up_again(
    app, client, company, scheduler, worker, engine, clock
):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    leased = jobs.lease_next(engine.session_factory, "crashy", clock(), lease_seconds=60)
    assert leased is not None
    assert not worker.run_once()  # leased by someone else
    clock.advance(30)
    with app.state.session_factory() as db:
        assert jobs.requeue_expired(db, clock()) == 0  # lease still valid
    clock.advance(31)
    with app.state.session_factory() as db:
        assert jobs.requeue_expired(db, clock()) == 1
        row = db.scalars(select(Job)).one()
        assert row.status == "queued" and row.leased_by is None and row.attempts == 1
    assert worker.drain() == 1  # run was still queued, so it now executes
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_exhausted_job_lease_dies_and_its_queued_run_is_failed(
    app, client, company, scheduler, engine, clock
):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    with app.state.session_factory() as db:
        db.execute(
            update(Job).values(
                status="leased", attempts=5, max_attempts=5, lease_expires_at=clock()
            )
        )
        db.commit()
    clock.advance(5)
    report = recovery.recover(engine)
    assert report.requeued_jobs == 1 and len(report.dead_job_run_ids) == 1
    with app.state.session_factory() as db:
        assert db.scalars(select(Job.status)).one() == "dead"
        run = db.scalars(select(TaskRun)).one()
        assert run.status == "failed" and run.exit_reason == "job_dead"
    assert get_task(client, cid, task["id"])["status"] == "ready"  # retry path


def test_scheduler_start_runs_recovery(app, client, company, scheduler, engine, clock):
    cid = company["id"]
    agent = new_agent(client, cid)
    new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    _simulate_crashed_worker(app, clock, heartbeat_age=300)
    fresh = Scheduler(engine)  # "restart"
    report = fresh.start()
    assert len(report.stale_run_ids) == 1


# ------------------------------------------------------------------ schedules API


def test_schedule_validation(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    base = {"name": "Standup", "agent_id": agent["id"]}
    ok = client.post(api(cid, "/schedules"), json={**base, "interval_seconds": 3600})
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["kind"] == "routine" and body["enabled"] is True and body["timezone"] == "UTC"
    assert body["cron"] is None and body["next_run_at"] and body["last_fired_at"] is None
    for bad in (
        {**base},  # neither
        {**base, "interval_seconds": 3600, "cron": "0 9 * * *"},  # both
        {**base, "interval_seconds": 59},
        {**base, "cron": "0 9 * *"},  # 4 fields
        {**base, "cron": "61 9 * * *"},
        {**base, "cron": "0 9 * * *", "timezone": "Mars/Base"},
        {**base, "interval_seconds": 60, "kind": "weekly"},
        {**base, "interval_seconds": 60, "task_template": {"priority": "asap"}},
        {**base, "interval_seconds": 60, "task_template": {"surprise": 1}},
        {"name": "x", "agent_id": "nope", "interval_seconds": 60},
    ):
        r = client.post(api(cid, "/schedules"), json=bad)
        assert r.status_code in (404, 422), bad


def test_schedule_update_recomputes_next_run(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    s = client.post(
        api(cid, "/schedules"),
        json={"name": "S", "agent_id": agent["id"], "interval_seconds": 3600},
    ).json()
    r = client.patch(api(cid, f"/schedules/{s['id']}"), json={"enabled": False})
    assert r.json()["enabled"] is False and r.json()["next_run_at"] is None
    r = client.patch(
        api(cid, f"/schedules/{s['id']}"),
        json={"enabled": True, "cron": "30 8 * * 1", "timezone": "Europe/Berlin"},
    )
    assert (
        r.status_code == 200
        and r.json()["cron"] == "30 8 * * 1"
        and r.json()["interval_seconds"] is None
    )
    assert r.json()["next_run_at"]
    assert (
        client.patch(
            api(cid, f"/schedules/{s['id']}"), json={"interval_seconds": 60, "cron": "* * * * *"}
        ).status_code
        == 422
    )
    assert (
        client.get(api(cid, "/schedules"), params={"enabled": "true"}).json()["items"][0]["id"]
        == s["id"]
    )
    assert client.get(api(cid, "/schedules"), params={"kind": "heartbeat"}).json()["items"] == []


# ------------------------------------------------------------------ firing


def _make_schedule(client, cid, agent_id, **fields):
    body = {"name": "Daily digest", "agent_id": agent_id, "interval_seconds": 60, **fields}
    r = client.post(api(cid, "/schedules"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _tasks(app):
    with app.state.session_factory() as db:
        return list(db.scalars(select(Task).order_by(Task.created_at)))


def test_interval_schedule_fires_a_templated_task_and_it_runs(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    agent = new_agent(client, cid)
    sched = _make_schedule(
        client, cid, agent["id"],
        task_template={"title": "Write the digest", "priority": "high", "description": "d", "acceptance_criteria": "short"},
    )  # fmt: skip
    assert scheduler.tick().fired_task_ids == []  # not due yet
    clock.advance(90)  # > one interval, with slack for real time elapsed during setup
    report = scheduler.tick()
    assert len(report.fired_task_ids) == 1
    [task] = _tasks(app)
    assert (task.title, task.priority, task.kind) == ("Write the digest", "high", "routine")
    assert task.assignee_agent_id == agent["id"] and task.created_by_type == "system"
    assert task.status == "in_progress"  # fired and dispatched in the same tick
    worker.drain()
    assert get_task(client, cid, task.id)["status"] == "done"
    after = client.get(api(cid, f"/schedules/{sched['id']}")).json()
    assert after["last_fired_at"] and after["next_run_at"] > after["last_fired_at"]
    assert client.get(api(cid, "/overview")).json()["routines"]["total"] == 1


def test_heartbeat_schedule_makes_planning_tasks_and_skips_while_one_is_open(
    app, client, company, scheduler, clock
):
    cid = company["id"]
    founder = client.get(api(cid, "/overview")).json()["founder"]
    client.patch(api(cid, f"/agents/{founder['id']}"), json={"runtime": "unassigned"})
    _make_schedule(client, cid, founder["id"], kind="heartbeat", name="Founder heartbeat")
    clock.advance(90)  # > one interval, with slack for real time elapsed during setup
    scheduler.tick()
    clock.advance(90)  # > one interval, with slack for real time elapsed during setup
    scheduler.tick()  # the first heartbeat task is still open (no runtime) -> skipped, not piled up
    tasks = _tasks(app)
    assert len(tasks) == 1 and tasks[0].kind == "planning"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(ScheduleFire.id))) == 2  # both slots recorded
        assert (
            db.scalar(select(func.count(ScheduleFire.id)).where(ScheduleFire.task_id.is_not(None)))
            == 1
        )


def test_after_downtime_only_the_latest_missed_slot_fires(app, client, company, scheduler, clock):
    cid = company["id"]
    agent = new_agent(client, cid, runtime="unassigned")
    _make_schedule(client, cid, agent["id"], interval_seconds=60)
    clock.advance(60 * 25 + 5)  # 25 slots missed
    scheduler.tick()
    assert len(_tasks(app)) == 1
    with app.state.session_factory() as db:
        fire = db.scalars(select(ScheduleFire)).one()
        sched = db.scalars(select(Schedule)).one()
        # the slot that fired is the *latest* one at or before "now", not the first missed one
        assert clock() - timedelta(seconds=60) < fire.scheduled_for <= clock()
        assert sched.next_run_at > clock()
    scheduler.tick()
    assert len(_tasks(app)) == 1  # nothing more until the next slot


def test_two_scheduler_instances_fire_each_slot_once(app, client, company, engine, clock):
    cid = company["id"]
    agent = new_agent(client, cid, runtime="unassigned")
    _make_schedule(client, cid, agent["id"])
    clock.advance(90)  # > one interval, with slack for real time elapsed during setup
    a, b = Scheduler(engine), Scheduler(engine)
    a.tick()
    b.tick()  # a second instance, same instant
    assert len(_tasks(app)) == 1
    clock.advance(61)
    barrier = threading.Barrier(4)

    def go():
        barrier.wait(timeout=10)
        schedules.fire_due(engine.session_factory, clock())

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert len(_tasks(app)) == 2  # the concurrent tick added exactly one more
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(ScheduleFire.id))) == 2


def test_restart_does_not_refire_a_slot(app, client, company, engine, clock):
    cid = company["id"]
    agent = new_agent(client, cid, runtime="unassigned")
    sched = _make_schedule(client, cid, agent["id"])
    clock.advance(90)  # > one interval, with slack for real time elapsed during setup
    Scheduler(engine).start()
    assert len(_tasks(app)) == 1
    # simulate a crash that lost the schedule's progress: next_run_at is rewound
    with app.state.session_factory() as db:
        slot = db.scalars(select(ScheduleFire.scheduled_for)).one()
        db.execute(update(Schedule).where(Schedule.id == sched["id"]).values(next_run_at=slot))
        db.commit()
    Scheduler(engine).start()  # the (schedule, slot) unique key + task idempotency key hold
    assert len(_tasks(app)) == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(ScheduleFire.id))) == 1


def test_cron_schedule_respects_timezone(app, client, company, scheduler, clock):
    from datetime import UTC, datetime

    cid = company["id"]
    agent = new_agent(client, cid, runtime="unassigned")
    clock.set(datetime(2027, 1, 12, 13, 30, tzinfo=UTC))  # 08:30 in New York (EST)
    _make_schedule(
        client, cid, agent["id"], name="9am NY", interval_seconds=None, cron="0 9 * * *",
        timezone="America/New_York",
    )  # fmt: skip
    # the schedule was created against the real clock; pin its next slot for the fake one
    with app.state.session_factory() as db:
        assert db.scalars(select(Schedule.next_run_at)).one() is not None
        db.execute(update(Schedule).values(next_run_at=datetime(2027, 1, 12, 14, 0, tzinfo=UTC)))
        db.commit()
    scheduler.tick()
    assert _tasks(app) == []  # 13:30Z < 14:00Z (09:00 EST)
    clock.set(datetime(2027, 1, 12, 14, 0, 30, tzinfo=UTC))
    scheduler.tick()
    assert len(_tasks(app)) == 1
    with app.state.session_factory() as db:
        assert db.scalars(select(Schedule.next_run_at)).one() == datetime(
            2027, 1, 13, 14, 0, tzinfo=UTC
        )
        assert db.scalars(select(ScheduleFire.scheduled_for)).one() == datetime(
            2027, 1, 12, 14, 0, tzinfo=UTC
        )


def test_cron_next_runs_across_dst(client):
    from datetime import UTC, datetime

    nxt = schedules.compute_next(
        interval_seconds=None, cron="0 9 * * *", timezone="America/New_York",
        after=datetime(2027, 3, 13, 15, 0, tzinfo=UTC),  # day before US DST starts (Mar 14)
    )  # fmt: skip
    assert nxt == datetime(2027, 3, 14, 13, 0, tzinfo=UTC)  # 09:00 EDT = 13:00Z (was 14:00Z)


def test_disabled_and_paused_companies_do_not_fire(app, client, company, scheduler, clock):
    cid = company["id"]
    agent = new_agent(client, cid, runtime="unassigned")
    sched = _make_schedule(client, cid, agent["id"])
    client.patch(api(cid, f"/schedules/{sched['id']}"), json={"enabled": False})
    clock.advance(600)
    scheduler.tick()
    assert _tasks(app) == []
    client.patch(api(cid, f"/schedules/{sched['id']}"), json={"enabled": True})
    with app.state.session_factory() as db:  # make it due "now"
        db.execute(update(Schedule).values(next_run_at=clock()))
        db.commit()
    client.post(api(cid, "/pause"))
    scheduler.tick()
    assert _tasks(app) == []
    client.post(api(cid, "/resume"))
    scheduler.tick()
    assert len(_tasks(app)) == 1


def test_schedules_are_tenant_scoped(client, client_as, make_user, company):
    from tests.conftest import human

    cid = company["id"]
    agent = new_agent(client, cid)
    s = _make_schedule(client, cid, agent["id"])
    outsider = client_as(human(make_user("sch@x.test")))
    assert outsider.get(api(cid, "/schedules")).status_code == 404
    assert (
        outsider.patch(api(cid, f"/schedules/{s['id']}"), json={"enabled": False}).status_code
        == 404
    )
    other = client.post("/api/companies", json={"name": "Elsewhere"}).json()["id"]
    assert client.get(api(other, f"/schedules/{s['id']}")).status_code == 404
    foreign = new_agent(client, other, name="F")
    assert (
        client.post(
            api(cid, "/schedules"),
            json={"name": "x", "agent_id": foreign["id"], "interval_seconds": 60},
        ).status_code
        == 404
    )
