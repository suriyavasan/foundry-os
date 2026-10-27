"""M12 WP1: the closure loops - the hooks on task transitions (blocked, failed planning run, project
finished), reviews (close every open review task, re-issue), and projects (goal review wake,
completion by the system)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.models.autonomy import WakeRequest
from app.models.work import Project, Task
from app.services import projects as project_service
from app.services import reviews, tasks, wakes
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, new_agent, new_task, set_policy
from tests.test_agent_api import start_run
from tests.test_m12_watchdog import (
    audits,
    clear_wakes,
    interventions,
    make_goal,
    make_project,
    review_tasks,
    run,
    set_row,
    stall_alerts,
    wake_reasons,
)


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def fail(app, task_id: str, code: str = "boom") -> None:
    """Fail a task through the real state machine (so the hooks run)."""
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "in_progress"
        tasks.transition(db, task, tasks.S.FAILED, principal=None, error={"code": code})
        db.commit()


def finish(app, task_id: str, status: str) -> None:
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "in_progress"
        tasks.transition(db, task, tasks.S(status), principal=None)
        db.commit()


def put_in_review(app, client, w, **kw) -> str:
    """A task that finished its run and entered review the real way (a review task is created)."""
    t = new_task(
        client,
        w.cid,
        title="Write copy",
        assignee_agent_id=w.specialist["id"],
        acceptance_criteria="One paragraph",
        **kw,
    )
    with app.state.session_factory() as db:
        task = db.get(Task, t["id"])
        tasks.transition(db, task, tasks.S.IN_PROGRESS, principal=None)
        tasks.finish_success(
            db, task, summary="done", output_refs=None, principal=None, run_id=None
        )
        db.commit()
    return t["id"]


# ------------------------------------------------------------------ blocked -> wake


def test_a_task_blocked_by_an_agent_wakes_the_project_lead_at_once(app, client, w):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    clear_wakes(app)
    sess = start_run(
        app, client, cid, w.specialist["id"], task_title="Needs a key", project_id=p["id"]
    )
    r = sess.patch(f"/tasks/{sess.task_id}", json={"status": "blocked", "reason": "no API key"})
    assert r.status_code == 200, r.text
    [reason] = wake_reasons(app, w.lead["id"])
    assert reason["trigger"] == "blocked" and "no API key" in reason["text"]
    assert reason["ref_type"] == "task" and reason["ref_id"] == sess.task_id
    assert wake_reasons(app, w.specialist["id"]) == []  # never the agent that blocked it
    assert wake_reasons(app, w.founder["id"]) == []


def test_blocking_wakes_the_assignees_manager_else_the_cofounder_never_the_blocker(app, client, w):
    cid = w.cid
    clear_wakes(app)
    sess = start_run(app, client, cid, w.specialist["id"], task_title="No project")
    sess.patch(f"/tasks/{sess.task_id}", json={"status": "blocked", "reason": "stuck"})
    assert [x["trigger"] for x in wake_reasons(app, w.lead["id"])] == ["blocked"]
    clear_wakes(app)
    mine = start_run(app, client, cid, w.lead["id"], task_title="Lead's own", kind="work")
    mine.patch(f"/tasks/{mine.task_id}", json={"status": "blocked", "reason": "need a decision"})
    assert [x["trigger"] for x in wake_reasons(app, w.founder["id"])] == ["blocked"]
    assert wake_reasons(app, w.lead["id"]) == []


def test_a_human_blocking_a_task_wakes_nobody(app, client, w):
    cid = w.cid
    t = new_task(client, cid, title="Hold", assignee_agent_id=w.specialist["id"])
    clear_wakes(app)
    assert (
        client.post(api(cid, f"/tasks/{t['id']}/block"), json={"reason": "wait"}).status_code == 200
    )
    assert wake_reasons(app, w.lead["id"]) == [] and wake_reasons(app, w.founder["id"]) == []


# ------------------------------------------------------------------ failed planning run


def wake_task_for(app, cid, agent_id) -> Task:
    return [
        t
        for t in tasks_of(app, cid, kind="planning", assignee_agent_id=agent_id)
        if (t.idempotency_key or "").startswith("wake:")
    ][-1]


def materialize(app, minutes: float = 3.0) -> None:
    wakes.materialize_due(app.state.session_factory, utcnow() + timedelta(minutes=minutes))


def test_a_failed_planning_run_requeues_its_reasons_twice_then_alerts(app, client, w):
    cid = w.cid
    set_policy(client, cid, max_planning_runs_per_hour=50)
    founder = w.founder["id"]
    with app.state.session_factory() as db:
        wakes.request_wake(db, cid, founder, "goal", "Goal created: Launch", delay_seconds=0)
        wakes.request_wake(db, cid, founder, "note", "A note arrived", delay_seconds=0)
        db.commit()
    for attempt in (1, 2):
        materialize(app, attempt * 3)
        failed = wake_task_for(app, cid, founder)
        assert "Goal created: Launch" in failed.description
        fail(app, failed.id)
        with app.state.session_factory() as db:
            [req] = db.scalars(select(WakeRequest).where(WakeRequest.agent_id == founder))
            assert req.trigger == "retry"
            assert {r["text"] for r in req.reasons} == {"Goal created: Launch", "A note arrived"}
            assert req.not_before >= utcnow() + timedelta(seconds=100)  # after the 120 s delay
        assert stall_alerts(app, cid) == []
    assert interventions(app, cid, "wake_requeue")[0].count == 2
    materialize(app, 9)
    fail(app, wake_task_for(app, cid, founder).id)  # the third failure: no more retries
    assert wake_reasons(app, founder) == []
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:wake_requeue:{founder}" and alert.severity == "critical"


def test_a_planning_run_that_succeeds_resets_the_retry_counter(app, client, w):
    cid = w.cid
    set_policy(client, cid, max_planning_runs_per_hour=50)
    founder = w.founder["id"]
    with app.state.session_factory() as db:
        wakes.request_wake(db, cid, founder, "goal", "Goal created: Launch", delay_seconds=0)
        db.commit()
    materialize(app, 3)
    fail(app, wake_task_for(app, cid, founder).id)
    assert interventions(app, cid, "wake_requeue")[0].count == 1
    materialize(app, 6)
    finish(app, wake_task_for(app, cid, founder).id, "done")
    assert interventions(app, cid, "wake_requeue") == []


def test_only_wake_tasks_are_requeued(app, client, w):
    cid = w.cid
    clear_wakes(app)
    t = new_task(client, cid, title="Plain", assignee_agent_id=w.specialist["id"])
    fail(app, t["id"])
    assert wake_reasons(app, w.specialist["id"]) == []
    assert interventions(app, cid, "wake_requeue") == []


# ------------------------------------------------------------------ project finished


def test_a_project_whose_tasks_all_failed_wakes_the_lead(app, client, w):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    t = new_task(
        client, cid, title="Doomed", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    clear_wakes(app)
    fail(app, t["id"])
    # the failure-analysis task (an open review-kind task of the project) goes to the lead first
    [analysis] = [
        x for x in tasks_of(app, cid, kind="review") if x.idempotency_key.startswith("failure:")
    ]
    assert analysis.assignee_agent_id == w.lead["id"]
    assert wake_reasons(app, w.lead["id"]) == []
    finish(app, analysis.id, "done")  # ...and when that is closed nothing is open any more
    [reason] = wake_reasons(app, w.lead["id"])
    assert reason["trigger"] == "project_done" and reason["ref_id"] == p["id"]
    assert "ended without a finished result" in reason["text"] and "1 failed" in reason["text"]


def test_all_cancelled_wakes_the_lead_and_a_finished_project_keeps_its_text(app, client, w):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    t = new_task(
        client, cid, title="Dropped", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    clear_wakes(app)
    assert (
        client.post(api(cid, f"/tasks/{t['id']}/cancel"), json={"reason": "no"}).status_code == 200
    )
    [reason] = wake_reasons(app, w.lead["id"])
    assert "ended without a finished result" in reason["text"]
    clear_wakes(app)
    done = new_task(
        client, cid, title="Good", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    finish(app, done["id"], "done")
    [reason] = wake_reasons(app, w.lead["id"])
    assert "is finished" in reason["text"] and "ended without" not in reason["text"]


def test_a_project_without_an_active_lead_wakes_the_cofounder(app, client, w):
    cid = w.cid
    p = make_project(client, cid)
    t = new_task(
        client, cid, title="Orphan work", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    clear_wakes(app)
    finish(app, t["id"], "done")
    assert [x["trigger"] for x in wake_reasons(app, w.founder["id"])] == ["project_done"]


# ------------------------------------------------------------------ reviews


def test_leaving_review_closes_every_open_review_task(app, client, w):
    cid = w.cid
    tid = put_in_review(app, client, w)
    with app.state.session_factory() as db:
        task = db.get(Task, tid)
        again = reviews.reissue_review(db, task, 1)
        db.commit()
        assert again is not None
    open_before = [t for t in review_tasks(app, cid) if t.status not in ("done", "cancelled")]
    assert len(open_before) == 2
    started = open_before[1]
    set_row(app, Task, started.id, status="in_progress")  # the reviewer is running this one
    r = client.post(api(cid, f"/tasks/{tid}/review"), json={"decision": "accept", "note": "good"})
    assert r.status_code == 200, r.text
    statuses = {t.idempotency_key: t.status for t in review_tasks(app, cid)}
    assert statuses[f"review:{tid}:0"] == "cancelled"  # never started
    assert statuses[f"review:{tid}:0:r1"] == "done"  # running: completed with the target


def test_a_revision_request_closes_the_review_tasks_and_the_next_review_is_new(app, client, w):
    cid = w.cid
    tid = put_in_review(app, client, w)
    r = client.post(
        api(cid, f"/tasks/{tid}/review"), json={"decision": "request_revision", "note": "shorter"}
    )
    assert r.status_code == 200, r.text
    assert all(t.status in ("done", "cancelled") for t in review_tasks(app, cid))
    with app.state.session_factory() as db:
        task = db.get(Task, tid)
        task.status = "in_progress"
        tasks.finish_success(db, task, summary="v2", output_refs=None, principal=None, run_id=None)
        db.commit()
    open_now = [t for t in review_tasks(app, cid) if t.status not in ("done", "cancelled")]
    assert [t.idempotency_key for t in open_now] == [f"review:{tid}:1"]


def test_reissue_review_rules(app, client, w):
    cid = w.cid
    tid = put_in_review(app, client, w)
    with app.state.session_factory() as db:
        task = db.get(Task, tid)
        first = reviews.reissue_review(db, task, 2)
        assert first is not None and first.idempotency_key == f"review:{tid}:0:r2"
        assert first.assignee_agent_id == w.lead["id"] and first.kind == "review"
        assert reviews.reissue_review(db, task, 2).id == first.id  # idempotent per attempt
        # another reviewer is recorded on the task (only the resolved reviewer may decide)
        moved = reviews.reissue_review(db, task, 3, reviewer_id=w.founder["id"])
        assert moved.assignee_agent_id == w.founder["id"]
        assert task.reviewer_agent_id == w.founder["id"]
        # never the assignee, never a retired agent
        assert reviews.reissue_review(db, task, 4, reviewer_id=w.specialist["id"]) is None
        db.commit()
    assert client.post(api(cid, f"/agents/{w.lead['id']}/retire")).status_code == 200
    with app.state.session_factory() as db:
        task = db.get(Task, tid)
        assert reviews.reissue_review(db, task, 5, reviewer_id=w.lead["id"]) is None  # retired
        task.status = "done"
        assert reviews.reissue_review(db, task, 9) is None  # not in review any more
        db.rollback()


def test_a_retired_reviewer_sends_the_review_straight_to_the_cofounder(app, client, w, clock):
    cid = w.cid
    tid = put_in_review(app, client, w)
    [r0] = review_tasks(app, cid)
    assert client.post(api(cid, f"/agents/{w.lead['id']}/retire")).status_code == 200
    run(app, cid, clock, 1)  # D7 cancels the review task of the retired reviewer
    with app.state.session_factory() as db:
        assert db.get(Task, r0.id).status == "cancelled"
    run(app, cid, clock, 6)  # D1: nobody can review it any more -> the Co-founder at once
    [r1] = [t for t in review_tasks(app, cid) if t.idempotency_key.endswith(":r1")]
    assert r1.assignee_agent_id == w.founder["id"]
    with app.state.session_factory() as db:
        assert db.get(Task, tid).reviewer_agent_id == w.founder["id"]


# ------------------------------------------------------------------ projects


def test_ending_a_project_wakes_the_cofounder_about_its_goal(app, client, w):
    cid = w.cid
    goal = make_goal(client, cid, "Launch the site")
    p = make_project(client, cid, w.lead["id"], goal_id=goal["id"])
    clear_wakes(app)
    r = client.patch(api(cid, f"/projects/{p['id']}"), json={"status": "completed"})
    assert r.status_code == 200, r.text
    reasons = {x["trigger"]: x for x in wake_reasons(app, w.founder["id"])}
    assert "project" in reasons and "goal_review" in reasons
    review = reasons["goal_review"]
    assert goal["id"] in review["text"] and "has no open projects" in review["text"]
    assert review["ref_type"] == "goal" and review["ref_id"] == goal["id"]


def test_no_goal_wake_while_the_goal_has_another_open_project_or_is_settled(app, client, w):
    cid = w.cid
    goal = make_goal(client, cid)
    first = make_project(client, cid, w.lead["id"], goal_id=goal["id"])
    make_project(client, cid, w.lead["id"], goal_id=goal["id"], name="Second")
    clear_wakes(app)
    client.patch(api(cid, f"/projects/{first['id']}"), json={"status": "completed"})
    assert "goal_review" not in {x["trigger"] for x in wake_reasons(app, w.founder["id"])}
    done_goal = make_goal(client, cid, "Done already", status="achieved")
    q = make_project(client, cid, w.lead["id"], goal_id=done_goal["id"], name="Q")
    clear_wakes(app)
    client.patch(api(cid, f"/projects/{q['id']}"), json={"status": "cancelled"})
    assert "goal_review" not in {x["trigger"] for x in wake_reasons(app, w.founder["id"])}


def test_the_system_completes_a_project_and_wakes_for_the_goal(app, client, w):
    cid = w.cid
    goal = make_goal(client, cid)
    p = make_project(client, cid, w.lead["id"], goal_id=goal["id"])
    clear_wakes(app)
    with app.state.session_factory() as db:
        project = db.get(Project, p["id"])
        assert project_service.complete_by_system(db, project, reason="all work finished") is True
        assert project_service.complete_by_system(db, project, reason="again") is False
        db.commit()
    assert client.get(api(cid, f"/projects/{p['id']}")).json()["status"] == "completed"
    [event] = audits(app, "project.auto_completed")
    assert event.actor_type == "system" and "all work finished" in event.summary
    assert {"project", "goal_review"} <= {x["trigger"] for x in wake_reasons(app, w.founder["id"])}
    from app.models.schedule import Schedule

    with app.state.session_factory() as db:
        checks = list(db.scalars(select(Schedule).where(Schedule.name.like("project-health%"))))
    assert checks and all(not c.enabled for c in checks)  # its health check ended with it


# ------------------------------------------------------------------ retired lead


def test_a_retired_leads_project_gets_a_new_lead_through_the_safety_net(
    app, client, w, clock, scheduler
):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    new_lead = new_agent(client, cid, name="Leo", kind="lead", runtime="scripted")
    assert client.post(api(cid, f"/agents/{w.lead['id']}/retire")).status_code == 200
    run(app, cid, clock, 3)  # the watchdog clears the retired lead...
    assert client.get(api(cid, f"/projects/{p['id']}")).json()["lead_agent_id"] is None
    scheduler.tick()  # ...and the existing safety net hands the project to the new lead
    assert client.get(api(cid, f"/projects/{p['id']}")).json()["lead_agent_id"] == new_lead["id"]
    assert stall_alerts(app, cid) == []
