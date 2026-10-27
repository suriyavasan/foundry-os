"""M12 WP1: routines that act instead of only reporting - the attention-based heartbeat with its
fingerprint, the deterministic standup digest appended to the standup / weekly review, and the
scheduler calling the watchdog every 60 seconds."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.autonomy import AutonomyIntervention
from app.models.ledger import UsageRecord
from app.models.schedule import Schedule
from app.models.work import Task
from app.services import alerts, autonomy_watchdog, builtin_schedules, digest, schedules, wakes
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, new_task, set_policy
from tests.test_m12_watchdog import (
    audits,
    clear_wakes,
    interventions,
    make_goal,
    make_project,
    set_row,
)


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def only_heartbeat(app, cid) -> Schedule:
    """The wall-clock cron routines would fire whenever the suite runs near their time."""
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            if s.name != "heartbeat":
                s.enabled = False
        db.commit()
        return db.scalar(
            select(Schedule).where(Schedule.company_id == cid, Schedule.name == "heartbeat")
        )


def raise_alert(app, cid, title="Disk is full") -> str:
    with app.state.session_factory() as db:
        alert = alerts.raise_alert(
            db, cid, kind="test_alert", title=title, dedupe_key=title, wake_founder=False
        )
        db.commit()
        return alert.id


def fire(app, clock, minutes=31) -> list[str]:
    clock.advance(minutes * 60)
    return schedules.fire_due(app.state.session_factory, clock())


def finish_heartbeat_tasks(app, cid) -> None:
    with app.state.session_factory() as db:
        for t in db.scalars(select(Task).where(Task.company_id == cid, Task.kind == "planning")):
            t.status = "done"
        db.commit()


def skip_reasons(app) -> list[str]:
    return [e.summary for e in audits(app, "schedule.fired")]


# ------------------------------------------------------------------ needs_attention


def test_needs_attention_lists_what_needs_a_decision_not_what_is_running(app, client, w):
    cid = w.cid
    with app.state.session_factory() as db:
        assert wakes.attention_items(db, cid) == [] and not wakes.needs_attention(db, cid)
    # running work, an active goal with a project, a lead-led project: nobody needs to look
    g = make_goal(client, cid)
    p = make_project(client, cid, w.lead["id"], goal_id=g["id"])
    new_task(client, cid, title="Running", project_id=p["id"], assignee_agent_id=w.specialist["id"])
    with app.state.session_factory() as db:
        assert wakes.attention_items(db, cid) == []
        assert wakes.has_open_work(db, cid)
    # ...but these do
    lonely = make_goal(client, cid, "No project yet")
    orphan = make_project(client, cid, name="Leaderless")
    blocked = new_task(client, cid, title="Stuck", assignee_agent_id=w.specialist["id"])
    client.post(api(cid, f"/tasks/{blocked['id']}/block"), json={"reason": "x"})
    failed = new_task(client, cid, title="Broken", assignee_agent_id=w.specialist["id"])
    set_row(app, Task, failed["id"], status="failed", completed_at=wakes.utcnow())
    note = client.post(api(cid, "/notes"), json={"body": "Think about pricing"}).json()
    alert_id = raise_alert(app, cid)
    with app.state.session_factory() as db:
        items = set(wakes.attention_items(db, cid))
    assert items == {
        f"goal:{lonely['id']}",
        f"project:{orphan['id']}",
        f"blocked:{blocked['id']}",
        f"failed:{failed['id']}",
        f"note:{note['id']}",
        f"alert:{alert_id}",
    }
    assert not hasattr(wakes, "has_work")  # replaced


def test_a_failure_whose_analysis_is_finished_no_longer_needs_attention(app, client, w):
    cid = w.cid
    failed = new_task(client, cid, title="Broken", assignee_agent_id=w.specialist["id"])
    set_row(app, Task, failed["id"], status="failed", completed_at=wakes.utcnow())
    with app.state.session_factory() as db:
        assert wakes.attention_items(db, cid) == [f"failed:{failed['id']}"]
        db.add(
            Task(
                company_id=cid,
                title="Failure analysis",
                kind="review",
                status="done",
                idempotency_key=f"failure:{failed['id']}:1",
                created_by_type="system",
            )
        )
        db.commit()
        assert wakes.attention_items(db, cid) == []


# ------------------------------------------------------------------ heartbeat fingerprint


def test_the_heartbeat_skips_while_the_fingerprint_is_unchanged_and_fires_on_a_new_alert(
    app, client, w, clock
):
    cid = w.cid
    only_heartbeat(app, cid)
    assert fire(app, clock) == []
    assert any("nothing to review" in s for s in skip_reasons(app))
    first = raise_alert(app, cid, "Disk is full")
    [task_id] = fire(app, clock)  # something needs attention: the heartbeat runs
    with app.state.session_factory() as db:
        row = db.scalar(
            select(AutonomyIntervention).where(
                AutonomyIntervention.kind == "heartbeat_fingerprint",
                AutonomyIntervention.company_id == cid,
            )
        )
        assert row.subject_id == cid and row.subject_type == "company"
        stored = row.data["fingerprint"]
        assert stored == wakes.attention_fingerprint([f"alert:{first}"]) and row.count == 0
    finish_heartbeat_tasks(app, cid)
    assert fire(app, clock) == []  # same attention items: it already looked at them
    assert any("nothing new since the last heartbeat" in s for s in skip_reasons(app))
    assert fire(app, clock) == []
    raise_alert(app, cid, "A new problem")
    [again] = fire(app, clock)  # a new alert changes the fingerprint
    assert again != task_id
    with app.state.session_factory() as db:
        row = db.scalar(
            select(AutonomyIntervention).where(AutonomyIntervention.kind == "heartbeat_fingerprint")
        )
        assert row.data["fingerprint"] != stored and row.count == 1


def test_the_fingerprint_is_forgotten_when_nothing_needs_attention(app, client, w, clock):
    cid = w.cid
    only_heartbeat(app, cid)
    raise_alert(app, cid, "Transient")
    assert len(fire(app, clock)) == 1
    finish_heartbeat_tasks(app, cid)
    with app.state.session_factory() as db:
        alerts.resolve_by_key(db, cid, "Transient")
        db.commit()
    assert fire(app, clock) == []  # nothing to review: the stored fingerprint is dropped
    assert interventions(app, cid, "heartbeat_fingerprint") == []
    raise_alert(app, cid, "Transient")  # the very same situation comes back (a new alert id)
    assert len(fire(app, clock)) == 1


def test_running_work_and_active_goals_no_longer_keep_the_heartbeat_busy(app, client, w, clock):
    cid = w.cid
    only_heartbeat(app, cid)
    g = make_goal(client, cid)
    p = make_project(client, cid, w.lead["id"], goal_id=g["id"])
    new_task(client, cid, title="Busy", project_id=p["id"], assignee_agent_id=w.specialist["id"])
    clear_wakes(app)
    assert fire(app, clock) == []
    assert any("nothing to review" in s for s in skip_reasons(app))


# ------------------------------------------------------------------ digest


def seed_company(app, client, w, clock) -> dict:
    """A company with every kind of stuck item the digest reports."""
    cid = w.cid
    now = clock()
    p = make_project(client, cid, w.lead["id"], name="Landing page")
    done = new_task(client, cid, title="Positioning", project_id=p["id"])
    new_task(client, cid, title="Page copy", project_id=p["id"])
    set_row(app, Task, done["id"], status="done")
    review = new_task(client, cid, title="Headline draft", assignee_agent_id=w.specialist["id"])
    set_row(app, Task, review["id"], status="in_review", updated_at=now - timedelta(hours=2))
    blocked = new_task(client, cid, title="Needs credentials", assignee_agent_id=w.specialist["id"])
    client.post(api(cid, f"/tasks/{blocked['id']}/block"), json={"reason": "no key"})
    waiting = new_task(client, cid, title="Parent job", assignee_agent_id=w.specialist["id"])
    set_row(
        app,
        Task,
        waiting["id"],
        status="ready",
        blocked_reason="waiting_on_subtasks",
        updated_at=now - timedelta(hours=3),
    )
    ready = new_task(client, cid, title="Never picked up", assignee_agent_id=w.specialist["id"])
    set_row(app, Task, ready["id"], status="ready", updated_at=now - timedelta(minutes=45))
    broken = new_task(client, cid, title="Exploded", assignee_agent_id=w.specialist["id"])
    set_row(app, Task, broken["id"], status="failed", completed_at=now - timedelta(hours=1))
    set_policy(client, cid, monthly_budget_micros=5_000_000)
    with app.state.session_factory() as db:
        db.add(
            UsageRecord(
                company_id=cid,
                agent_id=w.specialist["id"],
                cost_micros=250_000,
                cost_kind="estimated",
                dedupe_key="digest-test-1",
                occurred_at=now,
            )
        )
        db.commit()
    return {
        "project": p,
        "review": review,
        "blocked": blocked,
        "waiting": waiting,
        "ready": ready,
        "broken": broken,
    }


def test_the_digest_contains_the_stuck_items_progress_failures_spend_and_interventions(
    app, client, w, clock
):
    cid = w.cid
    s = seed_company(app, client, w, clock)
    from tests.test_m12_watchdog import run

    run(app, cid, clock, 6)  # a watchdog step to report
    with app.state.session_factory() as db:
        text = digest.build(db, cid, clock())
    assert len(text.encode()) <= 3 * 1024
    assert "Platform digest" in text and "no model" in text
    assert "Landing page" in text and "1/2 tasks done" in text and "Lena" in text
    assert f"[task {s['review']['id']}]" in text and "in review over 1 h" in text
    assert "Headline draft" in text
    assert "blocked: 'Needs credentials'" in text
    assert "waiting on subtasks over 2 h: 'Parent job'" in text
    assert "ready but not dispatched for over 30 min: 'Never picked up'" in text
    assert "Exploded" in text and "tasks failed for good" in text
    assert "$5.00 monthly budget" in text and "estimated $0.25 (estimates, not charges)" in text
    assert "watchdog steps in the last 24 h: review_stall x1" in text
    with app.state.session_factory() as db:  # deterministic: same data, same text
        assert digest.build(db, cid, clock()) == text


def test_an_empty_company_has_a_short_honest_digest(app, client, w, clock):
    with app.state.session_factory() as db:
        text = digest.build(db, w.cid, clock())
    assert "no open projects" in text and "nothing is stuck" in text and "none" in text


def test_the_digest_never_exceeds_three_kilobytes(app, client, w, clock):
    cid = w.cid
    now = clock()
    for i in range(8):
        make_project(client, cid, w.lead["id"], name=f"Project number {i} " + "x" * 150)
    for i in range(30):
        t = new_task(
            client, cid, title=f"Task {i} " + "y" * 250, assignee_agent_id=w.specialist["id"]
        )
        set_row(app, Task, t["id"], status="in_review", updated_at=now - timedelta(hours=5))
    with app.state.session_factory() as db:
        text = digest.build(db, cid, now)
    assert len(text.encode()) <= 3 * 1024
    assert text.splitlines()[0].startswith("## Platform digest")


def test_the_digest_is_scoped_to_its_company(app, client, w, clock):
    seed_company(app, client, w, clock)
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    with app.state.session_factory() as db:
        text = digest.build(db, other["id"], clock())
    assert "Landing page" not in text and "Headline draft" not in text


# ------------------------------------------------------------------ standup / weekly


def test_the_standup_and_weekly_review_carry_the_digest_flag_and_ask_for_actions(app, client, w):
    with app.state.session_factory() as db:
        by_name = {
            s.name: s for s in db.scalars(select(Schedule).where(Schedule.company_id == w.cid))
        }
    for name in ("daily-standup", "weekly-strategy-review"):
        template = by_name[name].task_template
        assert template["digest"] is True and "up to 5 actions" in template["description"]
    assert "digest" not in by_name["daily-budget-review"].task_template
    assert "digest" not in by_name["heartbeat"].task_template
    assert builtin_schedules.COFOUNDER_SCHEDULES[1]["template"]["digest"] is True


def test_the_digest_is_appended_to_the_standup_task(app, client, w, clock):
    cid = w.cid
    seed_company(app, client, w, clock)
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            s.enabled = s.name == "daily-standup"
            s.next_run_at = clock() - timedelta(minutes=1) if s.enabled else None
        db.commit()
    created = schedules.fire_due(app.state.session_factory, clock())
    assert len(created) == 1
    [task] = [t for t in tasks_of(app, cid) if t.id == created[0]]
    assert task.title == "Daily standup" and task.kind == "routine"
    assert "up to 5 actions" in task.description  # the template text...
    assert "## Platform digest" in task.description  # ...followed by the digest
    assert "Headline draft" in task.description and "Needs credentials" in task.description
    assert task.description.index("up to 5 actions") < task.description.index("## Platform digest")
    digest_part = task.description.split("## Platform digest")[1]
    assert len(("## Platform digest" + digest_part).encode()) <= 3 * 1024


def test_a_template_without_the_flag_gets_no_digest(app, client, w, clock):
    cid = w.cid
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            s.enabled = s.name == "daily-budget-review"
            s.next_run_at = clock() - timedelta(minutes=1) if s.enabled else None
        db.commit()
    new_task(client, cid, title="Something open", assignee_agent_id=w.specialist["id"])
    [tid] = schedules.fire_due(app.state.session_factory, clock())
    [task] = [t for t in tasks_of(app, cid) if t.id == tid]
    assert "Platform digest" not in (task.description or "")


# ------------------------------------------------------------------ scheduler


def test_the_scheduler_runs_the_watchdog_every_sixty_seconds(app, scheduler, clock, monkeypatch):
    calls: list = []

    def fake(factory, now):
        calls.append(now)
        return {"review_stall": 2}

    monkeypatch.setattr(autonomy_watchdog, "run_all", fake)
    first = scheduler.tick()
    assert len(calls) == 1 and first.watchdog == {"review_stall": 2}
    clock.advance(30)
    assert scheduler.tick().watchdog == {} and len(calls) == 1
    clock.advance(31)
    assert scheduler.tick().watchdog == {"review_stall": 2} and len(calls) == 2
    assert calls[1] - calls[0] == timedelta(seconds=61)  # the injected clock, not the wall clock


def test_a_crashing_watchdog_does_not_break_the_tick(app, client, w, scheduler, clock, monkeypatch):
    def boom(factory, now):
        raise RuntimeError("watchdog bug")

    monkeypatch.setattr(autonomy_watchdog, "run_all", boom)
    t = new_task(client, w.cid, title="Still dispatched", assignee_agent_id=w.specialist["id"])
    report = scheduler.tick()  # no exception; dispatching still happened
    assert report.watchdog == {} and report.dispatch.queued_run_ids
    with app.state.session_factory() as db:
        assert db.get(Task, t["id"]).status == "in_progress"


def test_the_watchdog_does_not_run_during_the_emergency_stop(app, scheduler, monkeypatch):
    calls: list = []
    monkeypatch.setattr(autonomy_watchdog, "run_all", lambda f, n: calls.append(n) or {})
    monkeypatch.setattr(scheduler, "_stopped", lambda: True)
    scheduler.tick()
    assert calls == []


def test_watchdog_steps_through_the_scheduler_are_audited_with_the_fake_clock(
    app, client, w, scheduler, clock
):
    cid = w.cid
    t = new_task(
        client,
        cid,
        title="Write copy",
        assignee_agent_id=w.specialist["id"],
        acceptance_criteria="One paragraph",
    )
    set_row(app, Task, t["id"], status="in_review")
    scheduler.tick()  # (first tick: too fresh)
    clock.advance(6 * 60)
    report = scheduler.tick()
    assert report.watchdog.get("review_stall") == 1
    [event] = [e for e in audits(app, "autonomy.intervened") if e.company_id == cid]
    assert isinstance(event, AuditEvent) and event.data["kind"] == "review_stall"
