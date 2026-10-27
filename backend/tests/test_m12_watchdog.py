"""M12 WP1: the autonomy watchdog (detectors D1 to D9). Each detector fires, respects its spacing and
step limit, escalates to a critical ``autonomy_stalled`` alert, and does nothing for a paused company
or one whose AI is not ready. Conditions are built directly in the database (no agent runs) and the
watchdog is called with the fake clock, so everything is deterministic."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.models.audit import AuditEvent
from app.models.autonomy import Alert, AutonomyIntervention, WakeRequest
from app.models.work import Project, Task
from app.services import ai_setup, autonomy_watchdog
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, new_agent, new_task, set_policy
from tests.test_agent_api import start_run


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


# ------------------------------------------------------------------ helpers


def run(app, cid, clock, minutes: float = 0.0) -> dict[str, int]:
    """Advance the fake clock and run every detector once for the company."""
    clock.advance(minutes * 60)
    return autonomy_watchdog.run_company(app.state.session_factory, cid, clock())


def set_row(app, model, row_id, **fields) -> None:
    with app.state.session_factory() as db:
        row = db.get(model, row_id)
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()


def clear_wakes(app) -> None:
    """The woken agent has handled its wake (a pending wake makes the watchdog wait)."""
    with app.state.session_factory() as db:
        db.execute(delete(WakeRequest))
        db.commit()


def wake_reasons(app, agent_id, *, with_alerts: bool = False) -> list[dict]:
    """Why the agent is waiting to be woken (the wake an alert itself causes is left out)."""
    with app.state.session_factory() as db:
        return [
            r
            for req in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id))
            for r in req.reasons
            if with_alerts or r["trigger"] != "alert"
        ]


def interventions(app, cid, kind=None) -> list[AutonomyIntervention]:
    with app.state.session_factory() as db:
        stmt = select(AutonomyIntervention).where(AutonomyIntervention.company_id == cid)
        if kind:
            stmt = stmt.where(AutonomyIntervention.kind == kind)
        return list(db.scalars(stmt.order_by(AutonomyIntervention.created_at)))


def stall_alerts(app, cid, status="open") -> list[Alert]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(Alert).where(
                    Alert.company_id == cid,
                    Alert.kind == "autonomy_stalled",
                    Alert.status == status,
                )
            )
        )


def audits(app, action) -> list[AuditEvent]:
    with app.state.session_factory() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def make_project(client, cid, lead_id=None, name="Landing page", **kw) -> dict:
    body = {"name": name, "status": "active", **kw}
    if lead_id:
        body["lead_agent_id"] = lead_id
    r = client.post(api(cid, "/projects"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_goal(client, cid, title="Launch", status="active") -> dict:
    r = client.post(api(cid, "/goals"), json={"title": title, "status": status})
    assert r.status_code == 201, r.text
    return r.json()


def review_tasks(app, cid) -> list[Task]:
    return [t for t in tasks_of(app, cid, kind="review") if t.idempotency_key.startswith("review:")]


# ------------------------------------------------------------------ scenario builders (one per detector)
# Each returns the minutes to advance so that its condition is old enough for the detector.


def stuck_in_review(app, client, w, clock) -> dict:
    t = new_task(
        client,
        w.cid,
        title="Write copy",
        assignee_agent_id=w.specialist["id"],
        acceptance_criteria="One paragraph",
    )
    set_row(app, Task, t["id"], status="in_review")  # in review, but no review task was made
    return {"task": t, "minutes": 6}


def blocked_by_agent(app, client, w, clock) -> dict:
    sess = start_run(app, client, w.cid, w.specialist["id"], task_title="Needs a key", kind="work")
    r = sess.patch(f"/tasks/{sess.task_id}", json={"status": "blocked", "reason": "no API key"})
    assert r.status_code == 200, r.text
    clear_wakes(app)  # the hook's immediate wake to the lead is tested in test_m12_closure
    return {"task_id": sess.task_id, "minutes": 125}


def waiting_for_hire(app, client, w, clock) -> dict:
    p = make_project(client, w.cid, w.lead["id"])
    t = new_task(
        client, w.cid, title="Design logo", project_id=p["id"], required_skills=["illustration"]
    )
    assert t["assignee_agent_id"] is None
    set_row(app, Task, t["id"], status="backlog", blocked_reason="waiting_for_hire")
    return {"task": t, "project": p, "minutes": 11}


def project_without_tasks(app, client, w, clock) -> dict:
    p = make_project(client, w.cid, w.lead["id"])
    clear_wakes(app)  # (the lead was woken when it got the project)
    return {"project": p, "minutes": 11}


def finished_project(app, client, w, clock, *, goal=None) -> dict:
    kw = {"goal_id": goal["id"]} if goal else {}
    p = make_project(client, w.cid, w.lead["id"], **kw)
    t = new_task(client, w.cid, title="Ship it", project_id=p["id"])
    set_row(app, Task, t["id"], status="done")
    clear_wakes(app)
    return {"project": p, "task": t, "minutes": 11}


def goal_without_open_projects(app, client, w, clock) -> dict:
    g = make_goal(client, w.cid)
    p = make_project(client, w.cid, w.lead["id"], goal_id=g["id"])
    set_row(app, Project, p["id"], status="completed")
    clear_wakes(app)
    return {"goal": g, "project": p, "minutes": 11}


def retired_assignee(app, client, w, clock) -> dict:
    p = make_project(client, w.cid, w.lead["id"])
    t = new_task(
        client, w.cid, title="Orphaned", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    assert client.post(api(w.cid, f"/agents/{w.specialist['id']}/retire")).status_code == 200
    clear_wakes(app)
    return {"task": t, "project": p, "minutes": 1}


def unowned_task(app, client, w, clock) -> dict:
    p = make_project(client, w.cid)  # a project nobody leads
    t = new_task(client, w.cid, title="Nobody's", project_id=p["id"])
    assert t["assignee_agent_id"] is None
    clear_wakes(app)
    return {"task": t, "project": p, "minutes": 16}


def idle_company(app, client, w, clock) -> dict:
    set_policy(client, w.cid, initiative_interval_seconds=600)
    clear_wakes(app)
    return {"minutes": 11}


SCENARIOS = {
    "review_stall": stuck_in_review,
    "blocked_task": blocked_by_agent,
    "hire_stall": waiting_for_hire,
    "project_no_work": project_without_tasks,
    "project_closure": finished_project,
    "goal_review": goal_without_open_projects,
    "retired_agent": retired_assignee,
    "unowned_task": unowned_task,
    "initiative": idle_company,
}


# ------------------------------------------------------------------ safety: paused / not AI-ready


@pytest.mark.parametrize("kind", list(SCENARIOS))
def test_every_detector_does_nothing_for_a_paused_company(app, client, w, clock, kind):
    cid = w.cid
    info = SCENARIOS[kind](app, client, w, clock)
    assert client.post(api(cid, "/pause")).status_code == 200
    before_tasks = len(tasks_of(app, cid))
    assert run(app, cid, clock, info["minutes"]) == {}
    assert interventions(app, cid, kind) == [] and stall_alerts(app, cid) == []
    assert audits(app, "autonomy.intervened") == []
    assert len(tasks_of(app, cid)) == before_tasks
    # resumed, the same condition is acted on: the scenario really was stuck
    assert client.post(api(cid, "/resume")).status_code == 200
    clear_wakes(app)
    run(app, cid, clock, 0)
    assert interventions(app, cid, kind), f"{kind} did not fire after the resume"


@pytest.mark.parametrize("kind", list(SCENARIOS))
def test_every_detector_does_nothing_while_the_ai_is_not_ready(
    app, client, w, clock, monkeypatch, kind
):
    cid = w.cid
    info = SCENARIOS[kind](app, client, w, clock)
    monkeypatch.setattr(ai_setup, "ENFORCED", True)  # no provider configured: not ready
    assert run(app, cid, clock, info["minutes"]) == {}
    assert interventions(app, cid, kind) == [] and audits(app, "autonomy.intervened") == []
    monkeypatch.setattr(ai_setup, "ENFORCED", False)
    run(app, cid, clock, 0)
    assert interventions(app, cid, kind), f"{kind} did not fire once the AI was ready"


def test_a_failing_detector_does_not_stop_the_others(app, client, w, clock, monkeypatch):
    cid = w.cid
    info = stuck_in_review(app, client, w, clock)
    seen: list[str] = []

    def boom(db, company_id, now):
        seen.append("boom")
        raise RuntimeError("detector bug")

    patched = (("boom", boom), *autonomy_watchdog.DETECTORS)
    monkeypatch.setattr(autonomy_watchdog, "DETECTORS", patched)
    result = run(app, cid, clock, info["minutes"])
    assert seen == ["boom"] and result["boom"] == 0
    assert result["review_stall"] == 1 and review_tasks(app, cid)  # the others still ran


# ------------------------------------------------------------------ D1 review stall


def test_d1_reissues_then_goes_to_the_cofounder_then_alerts(app, client, w, clock):
    cid = w.cid
    info = stuck_in_review(app, client, w, clock)
    task_id = info["task"]["id"]
    assert run(app, cid, clock, 1)["review_stall"] == 0  # too fresh
    assert run(app, cid, clock, 5)["review_stall"] == 1
    [r1] = review_tasks(app, cid)
    assert r1.idempotency_key == f"review:{task_id}:0:r1" and r1.assignee_agent_id == w.lead["id"]
    assert r1.input_refs == [f"task:{task_id}"] and "without a decision" in r1.description
    [event] = audits(app, "autonomy.intervened")
    assert event.target_id == task_id and event.actor_type == "system"
    assert event.data["kind"] == "review_stall" and event.data["step"] == 1
    set_row(app, Task, r1.id, status="done")  # the reviewer finished without a decision
    assert run(app, cid, clock, 2)["review_stall"] == 0  # spacing: 5 min since step 1
    assert run(app, cid, clock, 4)["review_stall"] == 1
    r2 = [t for t in review_tasks(app, cid) if t.idempotency_key.endswith(":r2")][0]
    assert r2.assignee_agent_id == w.founder["id"]
    with app.state.session_factory() as db:
        assert db.get(Task, task_id).reviewer_agent_id == w.founder["id"]  # may record the decision
    assert audits(app, "task.reviewer_changed")
    assert any(r["trigger"] == "stall" for r in wake_reasons(app, w.founder["id"]))
    set_row(app, Task, r2.id, status="done")
    clear_wakes(app)
    assert stall_alerts(app, cid) == []
    run(app, cid, clock, 6)  # ladder used up: one critical alert, no third review task
    [alert] = stall_alerts(app, cid)
    assert alert.severity == "critical" and alert.dedupe_key == f"stall:review_stall:{task_id}:0"
    assert alert.related_type == "task" and alert.related_id == task_id
    run(app, cid, clock, 30)
    assert len(stall_alerts(app, cid)) == 1 and len(review_tasks(app, cid)) == 2
    # the review is finally recorded (task leaves in_review): the ladder is forgotten, alert resolved
    set_row(app, Task, task_id, status="done")
    run(app, cid, clock, 1)
    assert interventions(app, cid, "review_stall") == [] and stall_alerts(app, cid) == []
    assert len(stall_alerts(app, cid, "resolved")) == 1


def test_d1_waits_for_an_open_review_task_however_long_it_takes(app, client, w, clock):
    cid = w.cid
    info = stuck_in_review(app, client, w, clock)
    run(app, cid, clock, info["minutes"])  # step 1 made an open review task
    again = run(app, cid, clock, 60)
    assert again["review_stall"] == 0 and len(review_tasks(app, cid)) == 1


def test_a_review_task_that_fails_enters_the_ladder_at_once(app, client, w, clock):
    from app.services import tasks

    cid = w.cid
    t = new_task(
        client,
        cid,
        title="Write copy",
        assignee_agent_id=w.specialist["id"],
        acceptance_criteria="One paragraph",
    )
    with app.state.session_factory() as db:  # the real path: in_review creates the review task
        task = db.get(Task, t["id"])
        tasks.transition(db, task, tasks.S.IN_PROGRESS, principal=None)
        tasks.finish_success(
            db, task, summary="done", output_refs=None, principal=None, run_id=None
        )
        db.commit()
    [r0] = review_tasks(app, cid)
    assert r0.assignee_agent_id == w.lead["id"]
    with app.state.session_factory() as db:
        review = db.get(Task, r0.id)
        review.status = "in_progress"
        tasks.transition(db, review, tasks.S.FAILED, principal=None, error={"code": "x"})
        db.commit()
    # no waiting for the watchdog tick: the failure hook re-issued the review right away
    assert [t.idempotency_key.rsplit(":", 1)[-1] for t in review_tasks(app, cid)] == ["0", "r1"]


# ------------------------------------------------------------------ D2 blocked by an agent


def test_d2_wakes_the_cofounder_after_two_hours_and_alerts_after_a_day(app, client, w, clock):
    cid = w.cid
    info = blocked_by_agent(app, client, w, clock)
    assert run(app, cid, clock, 60)["blocked_task"] == 0  # one hour: too early
    assert run(app, cid, clock, 61)["blocked_task"] == 1  # over 2 h
    [reason] = wake_reasons(app, w.founder["id"])
    assert reason["trigger"] == "stall" and "blocked for over 2 hours" in reason["text"]
    assert "no API key" in reason["text"]
    clear_wakes(app)
    assert run(app, cid, clock, 600)["blocked_task"] == 0  # spacing: nothing before 24 h
    assert stall_alerts(app, cid) == []
    run(app, cid, clock, 740)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:blocked_task:{info['task_id']}"
    run(app, cid, clock, 600)
    assert len(stall_alerts(app, cid)) == 1 and wake_reasons(app, w.founder["id"]) == []


def test_d2_leaves_a_task_a_person_blocked_alone(app, client, w, clock):
    cid = w.cid
    t = new_task(client, cid, title="On hold", assignee_agent_id=w.specialist["id"])
    assert (
        client.post(api(cid, f"/tasks/{t['id']}/block"), json={"reason": "waiting"}).status_code
        == 200
    )
    clear_wakes(app)
    assert run(app, cid, clock, 300)["blocked_task"] == 0
    assert wake_reasons(app, w.founder["id"]) == []


# ------------------------------------------------------------------ D3 hire stall


def test_d3_gives_the_task_to_the_leads_least_loaded_report(app, client, w, clock):
    cid = w.cid
    tess = new_agent(client, cid, name="Tess", runtime="scripted", manager_agent_id=w.lead["id"])
    new_task(client, cid, title="Sam is busy", assignee_agent_id=w.specialist["id"])
    info = waiting_for_hire(app, client, w, clock)
    assert run(app, cid, clock, 3)["hire_stall"] == 0
    assert run(app, cid, clock, 8)["hire_stall"] == 1
    with app.state.session_factory() as db:
        task = db.get(Task, info["task"]["id"])
        assert task.assignee_agent_id == tess["id"] and task.blocked_reason is None
    [event] = [e for e in audits(app, "task.assigned") if "skills fallback" in e.summary]
    assert event.data["fallback"] == "skills" and event.actor_type == "system"


def test_d3_assigns_the_lead_when_it_has_no_reports(app, client, w, clock):
    cid = w.cid
    loner = new_agent(client, cid, name="Loner", kind="lead", runtime="scripted")
    p = make_project(client, cid, loner["id"], name="Solo")
    t = new_task(client, cid, title="Needs art", project_id=p["id"], required_skills=["art"])
    set_row(app, Task, t["id"], status="backlog", blocked_reason="waiting_for_hire")
    run(app, cid, clock, 11)
    with app.state.session_factory() as db:
        assert db.get(Task, t["id"]).assignee_agent_id == loner["id"]


def test_d3_without_a_lead_wakes_the_cofounder_twice_then_alerts(app, client, w, clock):
    cid = w.cid
    t = new_task(client, cid, title="Loose", required_skills=["art"])
    set_row(app, Task, t["id"], status="backlog", blocked_reason="waiting_for_hire")
    assert run(app, cid, clock, 11)["hire_stall"] == 1
    assert [r["trigger"] for r in wake_reasons(app, w.founder["id"])] == ["stall"]
    clear_wakes(app)
    assert run(app, cid, clock, 5)["hire_stall"] == 0  # spacing
    assert run(app, cid, clock, 6)["hire_stall"] == 1  # second and last step
    clear_wakes(app)
    run(app, cid, clock, 11)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:hire_stall:{t['id']}"
    run(app, cid, clock, 60)
    assert len(stall_alerts(app, cid)) == 1 and wake_reasons(app, w.founder["id"]) == []


def test_d3_waits_while_a_hiring_task_is_open(app, client, w, clock):
    from app.models.enums import TaskKind

    cid = w.cid
    info = waiting_for_hire(app, client, w, clock)
    with app.state.session_factory() as db:
        db.add(
            Task(
                company_id=cid,
                title="Hire: illustration",
                description="Required skills: illustration\nSource task: x",
                kind=TaskKind.HIRING.value,
                status="ready",
                assignee_agent_id=w.hr["id"],
                created_by_type="system",
            )
        )
        db.commit()
    assert run(app, cid, clock, info["minutes"])["hire_stall"] == 0
    assert interventions(app, cid, "hire_stall") == []


# ------------------------------------------------------------------ D4 project without work


def test_d4_wakes_the_lead_twice_then_the_cofounder_then_alerts(app, client, w, clock):
    cid = w.cid
    info = project_without_tasks(app, client, w, clock)
    pid = info["project"]["id"]
    assert run(app, cid, clock, 5)["project_no_work"] == 0
    assert run(app, cid, clock, 6)["project_no_work"] == 1
    [r] = wake_reasons(app, w.lead["id"])
    assert r["trigger"] == "stall" and pid in r["text"] and "no tasks" in r["text"]
    assert run(app, cid, clock, 30)["project_no_work"] == 0  # the lead's wake is still waiting
    clear_wakes(app)
    assert run(app, cid, clock, 1)["project_no_work"] == 1  # 20+ min since step 1
    assert len(wake_reasons(app, w.lead["id"])) == 1
    clear_wakes(app)
    assert run(app, cid, clock, 10)["project_no_work"] == 0  # spacing
    assert run(app, cid, clock, 11)["project_no_work"] == 1  # step 3: the Co-founder
    assert wake_reasons(app, w.lead["id"]) == []
    [r] = wake_reasons(app, w.founder["id"])
    assert "still has no tasks" in r["text"] and w.lead["name"] in r["text"]
    clear_wakes(app)
    run(app, cid, clock, 21)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:project_no_work:{pid}" and alert.related_id == pid
    # a task finally appears: the ladder is forgotten and the alert resolved
    new_task(client, cid, title="First", project_id=pid)
    run(app, cid, clock, 1)
    assert interventions(app, cid, "project_no_work") == [] and stall_alerts(app, cid) == []


def test_d4_skips_a_project_whose_lead_is_already_planning(app, client, w, clock):
    cid = w.cid
    project_without_tasks(app, client, w, clock)
    with app.state.session_factory() as db:
        db.add(
            WakeRequest(
                company_id=cid,
                agent_id=w.lead["id"],
                trigger="assignment",
                reasons=[{"trigger": "assignment", "text": "x"}],
                not_before=clock(),
            )
        )
        db.commit()
    assert run(app, cid, clock, 30)["project_no_work"] == 0  # a wake is already queued: no pile-on
    assert audits(app, "autonomy.intervened") == []
    clear_wakes(app)
    assert run(app, cid, clock, 0)["project_no_work"] == 1  # it is not consumed: acts once idle


# ------------------------------------------------------------------ D5 finished, not closed


def test_d5_wakes_the_lead_then_the_cofounder_then_completes_the_project(app, client, w, clock):
    cid = w.cid
    goal = make_goal(client, cid)
    info = finished_project(app, client, w, clock, goal=goal)
    pid = info["project"]["id"]
    assert run(app, cid, clock, 5)["project_closure"] == 0  # not quiet for 10 minutes yet
    assert run(app, cid, clock, 6)["project_closure"] == 1
    [r] = wake_reasons(app, w.lead["id"])
    assert (
        r["trigger"] == "project_done" and "is finished and the project is still open" in r["text"]
    )
    clear_wakes(app)
    assert run(app, cid, clock, 11)["project_closure"] == 1  # step 2: the Co-founder
    assert [x["trigger"] for x in wake_reasons(app, w.founder["id"])] == ["stall"]
    clear_wakes(app)
    assert run(app, cid, clock, 5)["project_closure"] == 0
    assert run(app, cid, clock, 6)["project_closure"] == 1  # step 3: the system completes it
    with app.state.session_factory() as db:
        assert db.get(Project, pid).status == "completed"
    [event] = audits(app, "project.auto_completed")
    assert event.target_id == pid and event.actor_type == "system" and event.data["auto"] is True
    triggers = [x["trigger"] for x in wake_reasons(app, w.founder["id"])]
    assert "project" in triggers and "goal_review" in triggers  # the goal review follows
    assert stall_alerts(app, cid) == []
    run(app, cid, clock, 1)
    assert interventions(app, cid, "project_closure") == []  # the project is closed: forgotten


def test_d5_alerts_instead_of_completing_when_the_policy_is_off(app, client, w, clock):
    cid = w.cid
    set_policy(client, cid, project_auto_complete=False)
    info = finished_project(app, client, w, clock)
    pid = info["project"]["id"]
    for minutes in (11, 11, 11):
        clear_wakes(app)
        run(app, cid, clock, minutes)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:project_closure:{pid}"
    with app.state.session_factory() as db:
        assert db.get(Project, pid).status == "active"
    assert audits(app, "project.auto_completed") == []
    run(app, cid, clock, 30)
    assert len(stall_alerts(app, cid)) == 1


def test_d5_never_completes_a_project_without_a_finished_task(app, client, w, clock):
    cid = w.cid
    info = finished_project(app, client, w, clock)
    set_row(app, Task, info["task"]["id"], status="failed")
    for minutes in (11, 11, 11):
        clear_wakes(app)
        run(app, cid, clock, minutes)
    with app.state.session_factory() as db:
        assert db.get(Project, info["project"]["id"]).status == "active"
    assert len(stall_alerts(app, cid)) == 1


# ------------------------------------------------------------------ D6 goal without open projects


def test_d6_wakes_the_cofounder_twice_then_alerts(app, client, w, clock):
    cid = w.cid
    info = goal_without_open_projects(app, client, w, clock)
    gid = info["goal"]["id"]
    assert run(app, cid, clock, 5)["goal_review"] == 0
    assert run(app, cid, clock, 6)["goal_review"] == 1
    [r] = wake_reasons(app, w.founder["id"])
    assert (
        r["trigger"] == "goal_review" and gid in r["text"] and "achieved or abandoned" in r["text"]
    )
    clear_wakes(app)
    assert run(app, cid, clock, 10)["goal_review"] == 0  # spacing 20 min
    assert run(app, cid, clock, 11)["goal_review"] == 1
    clear_wakes(app)
    run(app, cid, clock, 21)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:goal_review:{gid}"
    # the Co-founder finally marks it achieved: nothing is tracked any more
    r = client.patch(api(cid, f"/goals/{gid}"), json={"status": "achieved"})
    assert r.status_code == 200
    clear_wakes(app)
    run(app, cid, clock, 1)
    assert interventions(app, cid, "goal_review") == [] and stall_alerts(app, cid) == []


def test_d6_leaves_a_goal_with_an_open_project_or_none_at_all(app, client, w, clock):
    cid = w.cid
    make_project(client, cid, w.lead["id"], goal_id=make_goal(client, cid, "Has project")["id"])
    make_goal(client, cid, "Bare goal")  # (wakes.nudge_goals_without_projects owns this case)
    clear_wakes(app)
    assert run(app, cid, clock, 60)["goal_review"] == 0


# ------------------------------------------------------------------ D7 retired agents


def test_d7_routes_a_retired_agents_open_tasks_to_the_project_lead(app, client, w, clock):
    cid = w.cid
    info = retired_assignee(app, client, w, clock)
    assert run(app, cid, clock, 1)["retired_agent"] >= 1
    with app.state.session_factory() as db:
        task = db.get(Task, info["task"]["id"])
        assert task.assignee_agent_id == w.lead["id"]
    assert any("its agent was retired" in e.summary for e in audits(app, "task.assigned"))
    assert interventions(app, cid, "retired_agent")[0].count == 1
    assert run(app, cid, clock, 5)["retired_agent"] == 0  # idempotent


def test_d7_unassigns_when_there_is_no_lead_and_cancels_routine_tasks(app, client, w, clock):
    cid = w.cid
    loose = new_task(client, cid, title="Loose", assignee_agent_id=w.specialist["id"])
    routine = new_task(
        client, cid, title="Routine", assignee_agent_id=w.specialist["id"], kind="routine"
    )
    assert client.post(api(cid, f"/agents/{w.specialist['id']}/retire")).status_code == 200
    run(app, cid, clock, 1)
    with app.state.session_factory() as db:
        assert db.get(Task, loose["id"]).assignee_agent_id is None
        assert db.get(Task, routine["id"]).status == "cancelled"
    assert audits(app, "task.unassigned")


def test_d7_clears_a_retired_lead_so_the_safety_net_finds_a_new_one(
    app, client, w, clock, scheduler
):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    other = new_agent(client, cid, name="Leo", kind="lead", runtime="scripted")
    assert client.post(api(cid, f"/agents/{w.lead['id']}/retire")).status_code == 200
    assert client.get(api(cid, f"/projects/{p['id']}")).json()["lead_agent_id"] == w.lead["id"]
    clock.advance(200)
    scheduler.tick()  # watchdog clears the lead, then the dispatcher's safety net picks Leo
    assert client.get(api(cid, f"/projects/{p['id']}")).json()["lead_agent_id"] == other["id"]
    assert any("lost its lead" in e.summary for e in audits(app, "project.updated"))


# ------------------------------------------------------------------ D8 unowned work


def test_d8_wakes_the_cofounder_twice_then_alerts(app, client, w, clock):
    cid = w.cid
    info = unowned_task(app, client, w, clock)
    tid = info["task"]["id"]
    assert run(app, cid, clock, 0)["unowned_task"] == 0
    assert run(app, cid, clock, 16)["unowned_task"] == 1
    [r] = wake_reasons(app, w.founder["id"])
    assert r["trigger"] == "stall" and tid in r["text"]
    clear_wakes(app)
    assert run(app, cid, clock, 10)["unowned_task"] == 0
    assert run(app, cid, clock, 11)["unowned_task"] == 1
    clear_wakes(app)
    run(app, cid, clock, 21)
    [alert] = stall_alerts(app, cid)
    assert alert.dedupe_key == f"stall:unowned_task:{tid}"
    run(app, cid, clock, 60)
    assert len(stall_alerts(app, cid)) == 1


def test_d8_gives_the_task_to_the_project_lead_when_there_is_one(app, client, w, clock):
    cid = w.cid
    p = make_project(client, cid, w.lead["id"])
    t = new_task(client, cid, title="Floating", project_id=p["id"])
    set_row(app, Task, t["id"], assignee_agent_id=None)  # (lost its owner)
    clear_wakes(app)
    run(app, cid, clock, 16)
    with app.state.session_factory() as db:
        assert db.get(Task, t["id"]).assignee_agent_id == w.lead["id"]


def test_d8_leaves_a_persons_own_unassigned_todo_alone(app, client, w, clock):
    cid = w.cid
    new_task(client, cid, title="Someday")  # created by the human, no project
    clear_wakes(app)
    assert run(app, cid, clock, 120)["unowned_task"] == 0
    assert wake_reasons(app, w.founder["id"]) == []


# ------------------------------------------------------------------ D9 initiative


def test_d9_wakes_an_idle_company_with_its_mission_and_is_capped_per_day(app, client, w, clock):
    cid = w.cid
    set_policy(client, cid, initiative_interval_seconds=600)
    clear_wakes(app)
    assert run(app, cid, clock, 5)["initiative"] == 0  # not idle long enough
    assert run(app, cid, clock, 6)["initiative"] == 1
    [r] = wake_reasons(app, w.founder["id"])
    assert r["trigger"] == "initiative" and "Ship" in r["text"] and "ONE goal" in r["text"]
    for _ in range(3):  # three more in the same day, 11 minutes apart
        clear_wakes(app)
        assert run(app, cid, clock, 11)["initiative"] == 1
    clear_wakes(app)
    assert run(app, cid, clock, 11)["initiative"] == 0  # the daily cap of 4
    assert interventions(app, cid, "initiative")[0].count == 4
    assert len(audits(app, "autonomy.intervened")) == 4
    assert run(app, cid, clock, 24 * 60)["initiative"] == 1  # a new day


def test_d9_respects_the_policy_open_work_and_notes(app, client, w, clock):
    cid = w.cid
    set_policy(client, cid, initiative_interval_seconds=600)
    clear_wakes(app)
    set_policy(client, cid, initiative_enabled=False)
    assert run(app, cid, clock, 60)["initiative"] == 0
    set_policy(client, cid, initiative_enabled=True)
    task = new_task(client, cid, title="In flight", assignee_agent_id=w.specialist["id"])
    clear_wakes(app)
    assert run(app, cid, clock, 60)["initiative"] == 0  # open work
    set_row(app, Task, task["id"], status="done")
    client.post(api(cid, "/notes"), json={"body": "Think about pricing"})
    clear_wakes(app)
    assert run(app, cid, clock, 60)["initiative"] == 0  # an unprocessed note is the Co-founder's
    assert interventions(app, cid, "initiative") == []


# ------------------------------------------------------------------ bookkeeping


def test_interventions_are_tenant_scoped(app, client, w, clock):
    cid = w.cid
    stuck_in_review(app, client, w, clock)
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    run(app, cid, clock, 6)
    run(app, other["id"], clock, 0)
    assert interventions(app, cid) and interventions(app, other["id"]) == []
    assert all(row.company_id == cid for row in interventions(app, cid))
