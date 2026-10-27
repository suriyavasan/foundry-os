"""Layered loops: role rules for task creation, project-lead routing, subtask waiting, reviews as
tasks, failure analysis, escalation, help requests, supervised plan approval, loop guard."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.governance import Approval
from app.models.run import CompanyEvent
from app.models.work import Task
from tests.autonomy_helpers import pending_wakes, run_world, scripted_world, tasks_of
from tests.helpers import api, get_task, new_agent, new_task, set_policy
from tests.test_agent_api import start_run


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def project(client, cid, lead_id=None, name="Landing page", **kw):
    body = {"name": name, "status": "active", **kw}
    if lead_id:
        body["lead_agent_id"] = lead_id
    r = client.post(api(cid, "/projects"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def one(app, task_id):
    with app.state.session_factory() as db:
        return db.get(Task, task_id)


def lead_session(app, client, w, **kw):
    kw.setdefault("kind", "planning")
    return start_run(app, client, w.cid, w.lead["id"], task_title="Management run", **kw)


# ------------------------------------------------------------------ who may create what


def test_a_lead_creates_tasks_only_inside_projects_it_leads(app, client, w):
    cid = w.cid
    # (created first: a new lead is handed every project that still waits for one)
    loner = new_agent(client, cid, name="Loner", kind="lead", runtime="scripted")
    mine = project(client, cid, w.lead["id"])
    foreign = project(client, cid, w.lead["id"], name="Not Lena's")
    client.patch(api(cid, f"/projects/{foreign['id']}"), json={"lead_agent_id": w.specialist["id"]})
    sess = lead_session(app, client, w)
    r = sess.post("/tasks", json={"title": "x", "project_id": foreign["id"]})
    assert r.status_code == 403 and "lead of a project" in r.json()["error"]["message"]
    # no project given: the single project it leads is used
    ok = sess.post("/tasks", json={"title": "in my project"})
    assert ok.status_code == 201 and ok.json()["project_id"] == mine["id"]
    # two led projects: it must say which one
    other = project(client, cid, w.lead["id"], name="Second")
    r = sess.post("/tasks", json={"title": "ambiguous"})
    assert r.status_code == 403 and mine["id"] in r.json()["error"]["message"]
    assert (
        sess.post("/tasks", json={"title": "explicit", "project_id": other["id"]}).status_code
        == 201
    )
    # a lead with no project at all cannot invent top-level work
    lone = start_run(app, client, cid, loner["id"], task_title="plan", kind="planning")
    assert lone.post("/tasks", json={"title": "orphan"}).status_code == 403
    # but a subtask of its own task is fine, in or out of a project
    own = new_task(client, cid, title="Own work", assignee_agent_id=loner["id"])
    own_run = start_run(app, client, cid, loner["id"], task_title="Own run", kind="work")
    sub = own_run.post("/tasks", json={"title": "sub of mine"})
    assert sub.status_code == 201 and sub.json()["parent_task_id"] == own_run.task_id
    assert own["id"]


def test_specialists_cannot_assign_others_but_can_ask_for_help(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Do it")
    r = sess.post("/tasks", json={"title": "for lead", "assignee_agent_id": w.lead["id"]})
    assert r.status_code == 403 and "company_request_help" in r.json()["error"]["message"]
    mine = sess.post("/tasks", json={"title": "for me"})
    assert mine.status_code == 201 and mine.json()["assignee_agent_id"] == w.specialist["id"]
    # reassigning to someone else is refused as well; to itself is allowed
    task = sess.post("/tasks", json={"title": "reassign me"}).json()
    r = sess.patch(f"/tasks/{task['id']}", json={"assignee_agent_id": w.lead["id"]})
    assert r.status_code == 403
    # HR follows the specialist rule
    hr = start_run(app, client, cid, w.hr["id"], task_title="hire", kind="hiring")
    assert (
        hr.post("/tasks", json={"title": "x", "assignee_agent_id": w.lead["id"]}).status_code == 403
    )


def test_unassigned_project_tasks_route_to_the_lead_and_the_lead_spreads_its_own(app, client, w):
    cid = w.cid
    p = project(client, cid, w.lead["id"])
    t = new_task(client, cid, title="Human made, no owner", project_id=p["id"])
    assert t["assignee_agent_id"] == w.lead["id"]  # the project lead's queue
    second = new_agent(client, cid, name="Tess", runtime="scripted", manager_agent_id=w.lead["id"])
    # Sam already has an open task, so a lead-created task goes to the idle report
    new_task(client, cid, title="busy", assignee_agent_id=w.specialist["id"])
    sess = lead_session(app, client, w)
    routed = sess.post("/tasks", json={"title": "spread", "project_id": p["id"]}).json()
    assert routed["assignee_agent_id"] == second["id"]
    # tasks that need skills are left to skill-gap detection, tasks of no project stay unowned
    skilled = sess.post(
        "/tasks",
        json={"title": "skilled", "project_id": p["id"], "required_skills": ["copywriting"]},
    ).json()
    assert skilled["assignee_agent_id"] is None
    assert new_task(client, cid, title="free floating")["assignee_agent_id"] is None


def test_a_planning_run_does_not_become_the_parent_of_the_work_it_creates(app, client, w):
    cid = w.cid
    p = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    t = sess.post("/tasks", json={"title": "top level", "project_id": p["id"]}).json()
    assert t["parent_task_id"] is None and t["delegation_depth"] == 0
    work = start_run(app, client, cid, w.specialist["id"], task_title="real work", kind="work")
    sub = work.post("/tasks", json={"title": "child"}).json()
    assert sub["parent_task_id"] == work.task_id and sub["delegation_depth"] == 1


def test_manager_handoff_of_its_own_running_task(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.lead["id"], task_title="Landed on me", kind="work")
    r = sess.patch(f"/tasks/{sess.task_id}", json={"assignee_agent_id": w.specialist["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["assignee_agent_id"] == w.specialist["id"] and r.json()["status"] == "ready"
    assert r.json()["attempt_count"] == 0
    # a specialist cannot hand its task to a colleague
    spec = start_run(app, client, cid, w.specialist["id"], task_title="mine", kind="work")
    assert (
        spec.patch(f"/tasks/{spec.task_id}", json={"assignee_agent_id": w.lead["id"]}).status_code
        == 403
    )


# ------------------------------------------------------------------ waiting on subtasks


def test_a_task_waits_for_its_subtasks_then_runs_again_to_integrate(
    app, client, w, scripted, scheduler, worker, clock
):
    cid = w.cid
    log: list[str] = []

    def sam(call):
        task = call.request.task
        log.append(task.title)
        if task.title == "Big task":
            if log.count("Big task") == 1:
                for n in (1, 2):
                    call.api.ok("POST", "/tasks", json={"title": f"Part {n}"})
                context = call.api.ok("GET", "/context")
                assert len(context["task"]["subtasks"]) == 2
            else:
                context = call.api.ok("GET", "/context")
                assert [s["status"] for s in context["task"]["subtasks"]] == ["done", "done"]
                assert {s["result_summary"] for s in context["task"]["subtasks"]} == {"part done"}
        result = "part done" if task.title.startswith("Part") else "all done"
        call.api.ok("POST", f"/tasks/{task.id}/complete", json={"result_summary": result})

    scripted.handlers["Sam"] = sam
    big = new_task(client, cid, title="Big task", assignee_agent_id=w.specialist["id"])
    run_world(app, scheduler, worker, clock)
    assert scripted.errors == [], scripted.errors
    assert log.count("Big task") == 2 and sorted(t for t in log if t.startswith("Part")) == [
        "Part 1",
        "Part 2",
    ]
    assert log.index("Part 1") < len(log) - 1  # the parent's second run came after its children
    final = get_task(client, cid, big["id"])
    assert final["status"] == "done" and final["result_summary"] == "all done"
    # the wait was visible in the audit trail while it lasted
    with app.state.session_factory() as db:
        reasons = [
            e.data.get("reason")
            for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == big["id"]))
        ]
    assert "waiting on subtasks" in reasons


def test_waiting_parent_resumes_even_when_a_subtask_is_cancelled(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Parent", kind="work")
    child = sess.post("/tasks", json={"title": "Child"}).json()
    done = sess.post(f"/tasks/{sess.task_id}/complete", json={"result_summary": "plan"})
    assert done.status_code == 200
    parent = get_task(client, cid, sess.task_id)
    assert parent["status"] == "backlog" and parent["blocked_reason"] == "waiting_on_subtasks"
    client.post(api(cid, f"/tasks/{child['id']}/cancel"), json={})
    parent = get_task(client, cid, sess.task_id)
    assert parent["status"] == "ready" and parent["blocked_reason"] is None


# ------------------------------------------------------------------ reviews as tasks


def make_in_review(app, client, w, *, criteria="Do it well"):
    """Sam finishes a task with acceptance criteria: it goes to its manager (the lead)."""
    cid = w.cid
    sess = start_run(
        app, client, cid, w.specialist["id"], task_title="Write copy", acceptance_criteria=criteria
    )
    art = sess.post(
        "/artifacts", json={"title": "Copy", "kind": "document", "content": "Hello world copy"}
    )
    assert art.status_code == 201
    done = sess.post(
        f"/tasks/{sess.task_id}/complete",
        json={"result_summary": "written", "output_refs": [art.json()["id"]]},
    )
    assert done.json()["status"] == "in_review"
    return sess


def review_tasks(app, cid):
    return [
        t
        for t in tasks_of(app, cid, kind="review")
        if (t.idempotency_key or "").startswith("review:")
    ]


def test_in_review_creates_exactly_one_review_task_for_the_reviewer(app, client, w):
    sess = make_in_review(app, client, w)
    [review] = review_tasks(app, w.cid)
    assert review.assignee_agent_id == w.lead["id"] and review.status == "ready"
    assert review.input_refs == [f"task:{sess.task_id}"] and "Do it well" in review.description
    assert review.idempotency_key == f"review:{sess.task_id}:0"
    # the lead's context shows the work under review, deliverable excerpt included
    lead = start_run(
        app, client, w.cid, w.lead["id"], task_title="Review: Write copy", kind="review"
    )
    with app.state.session_factory() as db:
        row = db.get(Task, lead.task_id)
        row.input_refs = [f"task:{sess.task_id}"]
        db.commit()
    target = lead.get("/context").json()["task"]["review_target"]
    assert target["task_id"] == sess.task_id and target["acceptance_criteria"] == "Do it well"
    assert target["artifacts"][0]["excerpt"] == "Hello world copy"


def test_recording_the_review_closes_the_review_task(app, client, w):
    cid = w.cid
    sess = make_in_review(app, client, w)
    [review] = review_tasks(app, cid)
    # a human reviews before the lead ever started: the review task is cancelled as moot
    r = client.post(api(cid, f"/tasks/{sess.task_id}/review"), json={"decision": "accept"})
    assert r.json()["status"] == "done"
    assert one(app, review.id).status == "cancelled"


def test_review_by_the_lead_completes_its_running_review_task(app, client, w):
    cid = w.cid
    sess = make_in_review(app, client, w)
    [review] = review_tasks(app, cid)
    with app.state.session_factory() as db:
        row = db.get(Task, review.id)
        row.status = "in_progress"  # the lead is running it
        db.commit()
    # run session of the lead on that very review task
    from app.core.time import utcnow
    from app.models.run import TaskRun
    from app.services import agent_auth
    from tests.test_agent_api import AgentSession

    with app.state.session_factory() as db:
        run = TaskRun(
            company_id=cid, task_id=review.id, agent_id=w.lead["id"], attempt_number=1,
            status="running", runtime="scripted", idempotency_key=f"{review.id}:1:0",
            queued_at=utcnow(), started_at=utcnow(), heartbeat_at=utcnow(),
        )  # fmt: skip
        db.add(run)
        db.commit()
        token = agent_auth.issue_token(app.state.keys, run, max_run_seconds=900)
        lead = AgentSession(client, token, run_id=run.id, task_id=review.id, agent_id=w.lead["id"])
    # the lead reviews by the REVIEW task's id: it resolves to the reviewed task
    r = lead.post(f"/tasks/{review.id}/review", json={"decision": "accept", "note": "fine"})
    assert r.status_code == 200 and r.json()["id"] == sess.task_id and r.json()["status"] == "done"
    assert one(app, review.id).status == "done"
    # completing the already-completed review task is harmless (small models do exactly that)
    again = lead.post(f"/tasks/{review.id}/complete", json={"result_summary": "reviewed"})
    assert again.status_code == 200


def test_revision_loop_makes_a_new_review_task_per_revision_and_stops_at_the_limit(
    app, client, w, scripted, scheduler, worker, clock
):
    cid = w.cid
    set_policy(client, cid, max_revisions=1)

    def sam(call):
        task = call.request.task
        art = call.api.ok(
            "POST", "/artifacts", json={"title": "Copy", "kind": "document", "content": "meh"}
        )
        call.api.ok(
            "POST",
            f"/tasks/{task.id}/complete",
            json={"result_summary": "written", "output_refs": [art["id"]]},
        )

    def lena(call):
        task = call.request.task
        mode = (call.api.ok("GET", "/context")["task"]["review_target"] or {}).get("mode")
        if mode == "review":
            call.api.ok(
                "POST",
                f"/tasks/{task.id}/review",
                json={"decision": "request_revision", "note": "Make it punchier"},
            )
        else:  # a failure analysis: acknowledge
            call.api.ok("POST", f"/tasks/{task.id}/complete", json={"result_summary": "analysed"})

    scripted.handlers.update({"Sam": sam, "Lena": lena})
    t = new_task(
        client, cid, title="Write copy", assignee_agent_id=w.specialist["id"],
        acceptance_criteria="Punchy",
    )  # fmt: skip
    run_world(app, scheduler, worker, clock)
    assert scripted.errors == [], scripted.errors
    final = get_task(client, cid, t["id"])
    assert final["status"] == "failed" and final["error"]["code"] == "max_revisions"
    reviews = review_tasks(app, cid)
    assert [r.idempotency_key for r in reviews] == [f"review:{t['id']}:0", f"review:{t['id']}:1"]
    assert all(r.status == "done" for r in reviews)
    # the revision note reached the assignee
    assert final["review_note"] == "Make it punchier"


# ------------------------------------------------------------------ failure analysis


def test_exhausted_attempts_hand_the_failure_to_the_lead_who_can_retry(
    app, client, w, scripted, scheduler, worker, clock
):
    cid = w.cid
    p = project(client, cid, w.lead["id"])
    set_policy(client, cid, max_attempts_per_task=2)
    flaky = {"result": "failed", "error_message": "model exploded"}
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={"runtime_config": {"script": flaky}},
    )
    retried: list[str] = []

    def lena(call):
        task = call.request.task
        if task.kind == "review":
            target = call.api.ok("GET", "/context")["task"]["review_target"]
            assert target["mode"] == "failure" and "model exploded" in target["error"]["message"]
            if len(retried) < 2:  # (M12: an agent may retry a task twice, then it must escalate)
                out = call.api.ok(
                    "POST",
                    f"/tasks/{target['task_id']}/retry",
                    json={
                        "reason": "try Sam again, fixed",
                        "assignee_agent_id": w.specialist["id"],
                    },
                )
                retried.append(out["id"])
            call.api.ok("POST", f"/tasks/{task.id}/complete", json={"result_summary": "retried"})

    scripted.handlers["Lena"] = lena
    t = new_task(
        client, cid, title="Flaky job", assignee_agent_id=w.specialist["id"], project_id=p["id"]
    )
    run_world(app, scheduler, worker, clock, rounds=12)
    assert scripted.errors == [], scripted.errors
    analyses = [
        x for x in tasks_of(app, cid, kind="review") if x.idempotency_key.startswith("failure:")
    ]
    assert len(analyses) >= 1 and analyses[0].assignee_agent_id == w.lead["id"]
    assert (
        analyses[0].title == "Failure analysis: Flaky job"
        and "model exploded" in analyses[0].description
    )
    assert retried and retried[0] == t["id"]
    # each failure gets its own analysis task (the key carries the run count)
    assert len({a.idempotency_key for a in analyses}) == len(analyses)
    # an open alert tells the Founder, once per failed task
    alerts = client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"]
    assert [a["kind"] for a in alerts].count("task_failed") == 1


def test_only_managers_retry_or_cancel(app, client, w):
    cid = w.cid
    t = new_task(client, cid, title="x", assignee_agent_id=w.specialist["id"])
    with app.state.session_factory() as db:
        db.get(Task, t["id"]).status = "failed"
        db.commit()
    sam = start_run(app, client, cid, w.specialist["id"], task_title="mine")
    assert sam.post(f"/tasks/{t['id']}/retry", json={}).status_code == 403
    lena = lead_session(app, client, w)
    r = lena.post(f"/tasks/{t['id']}/retry", json={})
    assert r.status_code == 200, r.text
    # lead manages Sam through the org tree -> allowed; the task restarted
    assert r.json()["status"] in ("ready", "backlog") and r.json()["attempt_count"] == 0
    assert lena.post(f"/tasks/{t['id']}/retry", json={}).status_code == 409  # not failed any more
    assert (
        lena.post(f"/tasks/{t['id']}/cancel", json={"reason": "no longer needed"}).json()["status"]
        == "cancelled"
    )
    assert lena.post(f"/tasks/{t['id']}/cancel", json={}).status_code == 409


# ------------------------------------------------------------------ escalation and help


def test_escalation_wakes_the_cofounder_and_can_block_the_task(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.lead["id"], task_title="Stuck work", kind="work")
    r = sess.post("/escalations", json={"reason": "Need a decision on pricing", "block_task": True})
    assert r.status_code == 201, r.text
    assert (
        r.json()["escalated_to_agent_id"] == w.founder["id"] and r.json()["wake_status"] == "queued"
    )
    assert get_task(client, cid, sess.task_id)["status"] == "blocked"
    assert pending_wakes(app) == 1
    with app.state.session_factory() as db:
        types = [e.type for e in db.scalars(select(CompanyEvent))]
        assert "escalation.raised" in types
        from app.models.autonomy import WakeRequest

        [req] = db.scalars(select(WakeRequest))
        assert req.agent_id == w.founder["id"] and req.trigger == "escalation"
        assert "Need a decision on pricing" in req.reasons[0]["text"]
    founder = start_run(app, client, cid, w.founder["id"], task_title="plan", kind="planning")
    assert founder.post("/escalations", json={"reason": "x"}).status_code == 409


def test_request_help_lands_in_the_managers_queue_and_is_bounded(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Hard thing")
    r = sess.post("/help-requests", json={"message": "I need the brand guide"})
    assert r.status_code == 201, r.text
    help_task = get_task(client, cid, r.json()["task_id"])
    assert help_task["assignee_agent_id"] == w.lead["id"]
    assert (
        help_task["created_by_id"] == w.specialist["id"]
        and "brand guide" in help_task["description"]
    )
    assert help_task["input_refs"] == [f"task:{sess.task_id}"]
    assert r.json()["assigned_to_name"] == "Lena"
    same = sess.post("/help-requests", json={"message": "I need the brand guide"})
    assert same.status_code == 200 and same.json()["task_id"] == r.json()["task_id"]
    for i in range(2):
        assert sess.post("/help-requests", json={"message": f"more {i}"}).status_code == 201
    r = sess.post("/help-requests", json={"message": "one too many"})
    assert r.status_code == 409 and r.json()["error"]["details"]["limit"] == "open_help_requests"
    # with no manager the Co-founder is asked
    founder_report = new_agent(client, w.cid, name="Solo", runtime="scripted")
    solo = start_run(app, client, cid, founder_report["id"], task_title="alone")
    assert (
        solo.post("/help-requests", json={"message": "help"}).json()["assigned_to_name"] == "Atlas"
    )


# ------------------------------------------------------------------ limits and guards


def test_loop_guard_returns_the_existing_open_duplicate(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Looping")
    first = sess.post("/tasks", json={"title": "Same thing"})
    second = sess.post("/tasks", json={"title": "  same   THING "})
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len([t for t in tasks_of(app, cid) if t.title.lower().startswith("same")]) == 1
    with app.state.session_factory() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "task.duplicate_suppressed" in actions
    # a finished task is not an open duplicate any more
    with app.state.session_factory() as db:
        db.get(Task, first.json()["id"]).status = "done"
        db.commit()
    assert sess.post("/tasks", json={"title": "Same thing"}).status_code == 201


def test_repeated_duplicates_raise_a_loop_guard_alert(app, client, w):
    cid = w.cid
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Looping")
    set_policy(client, cid, max_tasks_per_planning_cycle=50)
    sess.post("/tasks", json={"title": "again"})
    for _ in range(6):
        sess.post("/tasks", json={"title": "again"})
    alerts = client.get(api(cid, "/alerts")).json()["items"]
    assert [a["kind"] for a in alerts] == ["loop_guard"] and alerts[0]["related_type"] == "agent"


def test_max_open_tasks_caps_agent_created_work(app, client, w):
    cid = w.cid
    set_policy(client, cid, max_open_tasks=3, max_tasks_per_planning_cycle=50)
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Busy")  # open task #1
    assert sess.post("/tasks", json={"title": "two"}).status_code == 201
    assert sess.post("/tasks", json={"title": "three"}).status_code == 201
    r = sess.post("/tasks", json={"title": "four"})
    assert r.status_code == 409 and r.json()["error"]["details"]["limit"] == "max_open_tasks"
    # humans are the authority: they can still add work
    assert client.post(api(cid, "/tasks"), json={"title": "human task"}).status_code == 201


# ------------------------------------------------------------------ supervised mode


def test_supervised_mode_holds_a_managers_plan_until_a_human_approves(app, client, w):
    cid = w.cid
    p = project(client, cid, w.lead["id"])
    set_policy(client, cid, autonomy_mode="supervised")
    sess = lead_session(app, client, w)
    a = sess.post("/tasks", json={"title": "Plan A", "project_id": p["id"]}).json()
    b = sess.post("/tasks", json={"title": "Plan B", "project_id": p["id"]}).json()
    for t in (a, b):
        assert t["status"] == "backlog" and t["blocked_reason"] == "awaiting_plan_approval"
    # a single approval for the whole run
    [approval] = client.get(api(cid, "/approvals"), params={"status": "pending"}).json()["items"]
    assert approval["approver"] == "human" and approval["subject_type"] == "agent_plan"
    assert approval["requested_by_agent_id"] == w.lead["id"]
    # a specialist's own subtask is not held (it is not a plan)
    spec = start_run(app, client, cid, w.specialist["id"], task_title="Work")
    mine = spec.post("/tasks", json={"title": "my step"}).json()
    assert mine["blocked_reason"] is None
    r = client.post(api(cid, f"/approvals/{approval['id']}/approve"), json={})
    assert r.status_code == 200
    released = [get_task(client, cid, t["id"]) for t in (a, b)]
    assert all(t["status"] == "ready" and t["blocked_reason"] is None for t in released)


def test_rejecting_a_supervised_plan_cancels_its_tasks(app, client, w):
    cid = w.cid
    p = project(client, cid, w.lead["id"])
    set_policy(client, cid, autonomy_mode="supervised")
    sess = lead_session(app, client, w)
    a = sess.post("/tasks", json={"title": "Plan A", "project_id": p["id"]}).json()
    [approval] = client.get(api(cid, "/approvals"), params={"status": "pending"}).json()["items"]
    client.post(api(cid, f"/approvals/{approval['id']}/reject"), json={"note": "no"})
    assert get_task(client, cid, a["id"])["status"] == "cancelled"
    with app.state.session_factory() as db:
        assert db.scalar(select(Approval).where(Approval.status == "rejected")) is not None


# ------------------------------------------------------------------ project leads (agent rules + safety net)


def test_agents_can_only_make_a_lead_agent_the_project_lead(app, client, w):
    cid = w.cid
    founder = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    for not_a_lead in (w.hr["id"], w.specialist["id"], w.founder["id"]):
        r = founder.post("/projects", json={"name": "X", "lead_agent_id": not_a_lead})
        assert r.status_code == 409, (not_a_lead, r.text)
        assert "company_request_hire" in r.json()["error"]["message"]
    assert founder.post("/projects", json={"name": "X", "lead_agent_id": "nope"}).status_code == 404
    ok = founder.post("/projects", json={"name": "Y", "lead_agent_id": w.lead["id"]})
    assert ok.status_code == 201
    other = founder.post("/projects", json={"name": "Z"}).json()
    assert (
        founder.patch(f"/projects/{other['id']}", json={"lead_agent_id": w.hr["id"]}).status_code
        == 409
    )
    # humans keep full control
    assert (
        client.post(
            api(cid, "/projects"), json={"name": "H", "lead_agent_id": w.hr["id"]}
        ).status_code
        == 201
    )


def test_a_project_without_a_lead_gets_one_or_a_hiring_request_after_the_grace_period(
    app, client, w, scheduler, clock
):
    cid = w.cid
    orphan = client.post(api(cid, "/projects"), json={"name": "Orphan", "status": "active"}).json()
    scheduler.tick()
    assert (
        client.get(api(cid, f"/projects/{orphan['id']}")).json()["lead_agent_id"] is None
    )  # grace period
    clock.advance(200)
    scheduler.tick()
    project = client.get(api(cid, f"/projects/{orphan['id']}")).json()
    assert project["lead_agent_id"] == w.lead["id"]  # the only lead
    assert pending_wakes(app) >= 1
    [check] = [s for s in schedules_for(app, cid) if s.name.startswith("project-health-check")]
    assert check.agent_id == w.lead["id"]


def schedules_for(app, cid):
    from app.models.schedule import Schedule

    with app.state.session_factory() as db:
        return list(db.scalars(select(Schedule).where(Schedule.company_id == cid)))


def test_no_lead_available_means_one_hiring_task_and_the_new_lead_takes_the_project(
    app, client, anon_client, scripted, scheduler, clock
):
    w = scripted_world(client, scripted, anon_client, with_team=False)
    cid = w.cid
    orphan = client.post(api(cid, "/projects"), json={"name": "Orphan", "status": "active"}).json()
    clock.advance(200)
    scheduler.tick()
    scheduler.tick()
    hiring = [
        t
        for t in tasks_of(app, cid, kind="hiring")
        if t.idempotency_key == f"hiring:lead:{orphan['id']}"
    ]
    assert len(hiring) == 1 and hiring[0].assignee_agent_id == w.hr["id"]
    assert "marketing-lead" in hiring[0].description
    # HR hires the lead (here: a human activates one): the project is handed over at once
    lead = new_agent(
        client, cid, name="Lena", kind="lead", runtime="scripted", manager_agent_id=w.founder["id"]
    )
    project = client.get(api(cid, f"/projects/{orphan['id']}")).json()
    assert project["lead_agent_id"] == lead["id"]


def test_the_cofounders_triage_is_linked_to_what_it_created_even_when_it_triaged_first(
    app, client, w
):
    cid = w.cid
    note = client.post(api(cid, "/notes"), json={"body": "Launch the page"}).json()
    founder = start_run(
        app, client, cid, w.founder["id"], task_title="Planning run", kind="planning"
    )
    # the 7B model's order: triage first, act afterwards, then complete. A directive/goal may not be
    # triaged before anything was done (enforced); an idea may, and is then linked after the fact.
    early = founder.post(f"/notes/{note['id']}/triage", json={"category": "goal", "summary": "x"})
    assert early.status_code == 409
    assert (
        founder.post(
            f"/notes/{note['id']}/triage",
            json={"category": "idea", "summary": "launch", "reply": "On it"},
        ).status_code
        == 200
    )
    goal = founder.post("/goals", json={"title": "Launch"}).json()
    proj = founder.post(
        "/projects", json={"name": "Page", "goal_id": goal["id"], "lead_agent_id": w.lead["id"]}
    ).json()
    assert (
        founder.post(
            f"/tasks/{founder.task_id}/complete", json={"result_summary": "done"}
        ).status_code
        == 200
    )
    actions = client.get(api(cid, f"/notes/{note['id']}")).json()["triage"]["actions"]
    assert {(a["type"], a["ref_id"]) for a in actions} == {
        ("goal", goal["id"]),
        ("project", proj["id"]),
    }
    assert {a["label"] for a in actions} == {"Launch", "Page"}


def test_repeating_the_same_reply_does_not_duplicate_it(app, client, w):
    cid = w.cid
    note = client.post(api(cid, "/notes"), json={"body": "hi"}).json()
    sess = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    a = sess.post(f"/notes/{note['id']}/replies", json={"body": "Understood."})
    b = sess.post(f"/notes/{note['id']}/replies", json={"body": "Understood. "})
    assert a.json()["id"] == b.json()["id"]
    assert client.get(api(cid, f"/notes/{note['id']}")).json()["reply_count"] == 1


def test_agents_only_create_work_tasks(app, client, w):
    sess = start_run(app, client, w.cid, w.specialist["id"], task_title="Work")
    for kind in ("planning", "review", "hiring", "routine"):
        t = sess.post("/tasks", json={"title": f"as {kind}", "kind": kind}).json()
        assert t["kind"] == "work"
    # humans keep every kind
    assert new_task(client, w.cid, title="h", kind="planning")["kind"] == "planning"


def test_a_need_hired_for_is_not_hired_again_and_waiting_tasks_go_to_the_hire(
    app, client, anon_client, scripted, scheduler, clock
):
    from app.models.audit import AuditEvent
    from app.models.work import Task
    from app.services import hiring

    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    lead = start_run(app, client, cid, w.lead["id"], task_title="Management run", kind="planning")
    first = lead.post(
        "/hiring-requests",
        json={"required_skills": ["strategy"], "reason": "need it", "task_id": lead.task_id},
    )
    assert first.status_code == 201
    hiring_task_id = first.json()["hiring_task_id"]
    # the same need asked again while the first is open: the same task
    again = lead.post(
        "/hiring-requests",
        json={"required_skills": ["Strategy"], "reason": "again", "task_id": lead.task_id},
    )
    assert again.status_code == 200 and again.json()["hiring_task_id"] == hiring_task_id
    # HR finishes: the hire is recorded against the hiring task and the task is done
    with app.state.session_factory() as db:
        db.get(Task, hiring_task_id).status = "done"
        db.add(
            AuditEvent(
                company_id=cid, actor_type="agent", actor_id=w.hr["id"], actor_label="Harper",
                action="hire.created", target_type="agent", target_id=w.specialist["id"],
                summary="hired", data={"task_id": hiring_task_id},
            )
        )  # fmt: skip
        db.commit()
    # asking again hires nobody new: the finished task is returned
    third = lead.post(
        "/hiring-requests",
        json={"required_skills": ["strategy"], "reason": "once more", "task_id": lead.task_id},
    )
    assert third.status_code == 200 and third.json()["hiring_task_id"] == hiring_task_id
    # a task that needs the skill and has no owner goes to the people hired for it
    waiting = new_task(client, cid, title="Needs strategy", required_skills=["strategy"])
    scheduler.tick()
    assert get_task(client, cid, waiting["id"])["assignee_agent_id"] == w.specialist["id"]
    assert hiring.agents_hired_for  # exported helper


def test_the_platforms_lead_hiring_request_is_cancelled_once_the_project_has_a_lead(
    app, client, anon_client, scripted, scheduler, clock
):
    w = scripted_world(client, scripted, anon_client, with_team=False)
    cid = w.cid
    orphan = client.post(api(cid, "/projects"), json={"name": "Orphan", "status": "active"}).json()
    clock.advance(200)
    scheduler.tick()
    [req] = [
        t
        for t in tasks_of(app, cid, kind="hiring")
        if t.idempotency_key == f"hiring:lead:{orphan['id']}"
    ]
    assert req.status in ("ready", "in_progress", "backlog")
    lead = new_agent(client, cid, name="Lena", kind="lead", runtime="scripted")
    assert client.get(api(cid, f"/projects/{orphan['id']}")).json()["lead_agent_id"] == lead["id"]
    assert get_task(client, cid, req.id)["status"] == "cancelled"


def test_a_goal_without_a_project_nudges_the_cofounder_a_bounded_number_of_times(
    app, client, w, scheduler, clock
):
    cid = w.cid
    goal = client.post(api(cid, "/goals"), json={"title": "Launch", "status": "active"}).json()
    from app.models.autonomy import WakeRequest

    with (
        app.state.session_factory() as db
    ):  # (the goal itself already queued a "goal created" wake)
        db.query(WakeRequest).delete()
        db.commit()
    scheduler.tick()
    assert pending_wakes(app) == 0  # too early: the Co-founder is probably working on it
    clock.advance(240)
    scheduler.tick()
    assert pending_wakes(app) == 1
    with app.state.session_factory() as db:
        [req] = db.scalars(select(WakeRequest))
        assert (
            "has no project yet" in req.reasons[-1]["text"]
            and goal["id"] in req.reasons[-1]["text"]
        )
    for _ in range(
        4
    ):  # materialize, "run" and finish the wake task; the nudge repeats only every 20 min
        clock.advance(1300)
        scheduler.tick()
        with app.state.session_factory() as db:
            for t in db.scalars(
                select(Task).where(Task.status.in_(["ready", "backlog", "in_progress"]))
            ):
                if (t.idempotency_key or "").startswith("wake:"):
                    t.status = "done"
            db.commit()
    with app.state.session_factory() as db:
        nudges = [e for e in db.scalars(select(AuditEvent)) if e.action == "goal.nudged"]
    assert len(nudges) == 3  # bounded
    # a goal with a project is left alone
    client.post(api(cid, "/projects"), json={"name": "P", "goal_id": goal["id"]})
    before = len(nudges)
    clock.advance(1300)
    scheduler.tick()
    with app.state.session_factory() as db:
        assert (
            len([e for e in db.scalars(select(AuditEvent)) if e.action == "goal.nudged"]) == before
        )
