"""The Founder's notes and the wake mechanics (debounce, coalescing, rate limit, kill switch)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.principal import Principal, PrincipalType
from app.models.autonomy import MemoryEntry, WakeRequest
from app.models.work import Task
from app.services import wakes
from tests.autonomy_helpers import pending_wakes, run_world, scripted_world, tasks_of, wake_ready
from tests.conftest import human
from tests.helpers import api, set_policy
from tests.test_agent_api import start_run


def post_note(client, cid, body="Build a landing page", **kw):
    r = client.post(api(cid, "/notes"), json={"body": body, **kw})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def wake_tasks(app, cid):
    return [
        t
        for t in tasks_of(app, cid, kind="planning")
        if (t.idempotency_key or "").startswith("wake:")
    ]


# ------------------------------------------------------------------ owner API


def test_note_crud_validation_and_permissions(
    app, client, anon_client, w, make_user, add_member, client_as
):
    cid = w.cid
    note = post_note(client, cid)
    assert note["status"] == "new" and note["triage"] is None and note["pinned"] is False
    assert note["reply_count"] == 0 and note["last_reply"] is None and note["attachments"] == []
    assert client.post(api(cid, "/notes"), json={"body": ""}).status_code == 422
    assert client.post(api(cid, "/notes"), json={"body": "x" * 10_241}).status_code == 422
    assert client.post(api(cid, "/notes"), json={"body": "ok", "extra": 1}).status_code == 422
    # attachments must be artifacts of THIS company
    assert (
        client.post(api(cid, "/notes"), json={"body": "a", "attachments": ["nope"]}).status_code
        == 404
    )
    art = client.post(api(cid, "/artifacts"), json={"title": "Brief", "content": "hi"}).json()
    ok = post_note(client, cid, "see brief", attachments=[art["id"]])
    assert ok["attachments"] == [art["id"]]

    # pin / archive
    assert client.patch(api(cid, f"/notes/{note['id']}"), json={"pinned": True}).json()["pinned"]
    assert (
        client.patch(api(cid, f"/notes/{note['id']}"), json={"status": "triaged"}).status_code
        == 409
    )  # no triage yet
    arch = client.patch(api(cid, f"/notes/{note['id']}"), json={"status": "archived"})
    assert arch.json()["status"] == "archived"
    assert client.patch(api(cid, f"/notes/{note['id']}"), json={"bogus": 1}).status_code == 422

    # list: newest first, status filter, reply preview
    items = client.get(api(cid, "/notes")).json()["items"]
    assert [i["id"] for i in items] == [ok["id"], note["id"]]
    assert [
        i["id"] for i in client.get(api(cid, "/notes"), params={"status": "new"}).json()["items"]
    ] == [ok["id"]]
    reply = client.post(api(cid, f"/notes/{ok['id']}/replies"), json={"body": "and more"})
    assert reply.status_code == 201 and reply.json()["author_type"] == "human"
    detail = client.get(api(cid, f"/notes/{ok['id']}")).json()
    assert [r["body"] for r in detail["replies"]] == ["and more"] and detail["reply_count"] == 1
    assert client.get(api(cid, f"/notes/{note['id']}x")).status_code == 404

    # viewers read but never write; non-members get 404
    viewer = make_user("viewer@example.com")
    add_member(cid, viewer.id, "viewer")
    v = client_as(human(viewer))
    assert v.get(api(cid, "/notes")).status_code == 200
    assert v.post(api(cid, "/notes"), json={"body": "hi"}).status_code == 403
    assert v.post(api(cid, f"/notes/{ok['id']}/replies"), json={"body": "hi"}).status_code == 403
    assert v.patch(api(cid, f"/notes/{ok['id']}"), json={"pinned": True}).status_code == 403
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(cid, "/notes")).status_code == 404
    assert stranger.get(api(cid, f"/notes/{ok['id']}")).status_code == 404
    # an agent (even the founder) uses the agent API, not the owner API
    sess = start_run(app, client, cid, w.founder["id"], task_title="plan", kind="planning")
    assert anon_client.get(api(cid, "/notes"), headers=sess.headers).status_code == 403
    assert anon_client.get(api(cid, "/notes")).status_code == 401


def test_a_note_cannot_be_read_across_companies(client, w):
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    note = post_note(client, w.cid)
    assert client.get(api(other, f"/notes/{note['id']}")).status_code == 404
    assert (
        client.post(api(other, f"/notes/{note['id']}/replies"), json={"body": "x"}).status_code
        == 404
    )
    assert client.get(api(other, "/notes")).json()["items"] == []


# ------------------------------------------------------------------ wake mechanics


def test_a_burst_of_notes_becomes_one_debounced_planning_task(
    app, client, w, scheduler, worker, clock
):
    cid = w.cid
    for i in range(3):
        post_note(client, cid, f"thought {i}")
    assert pending_wakes(app) == 1  # coalesced into one request
    clock.advance(30)
    scheduler.tick()
    assert wake_tasks(app, cid) == []  # still inside the 60 s debounce window
    wake_ready(clock, 45)
    report = scheduler.tick()
    assert len(report.woke_task_ids) == 1
    [task] = wake_tasks(app, cid)
    assert task.assignee_agent_id == w.founder["id"] and task.kind == "planning"
    assert "Founder's note" in task.title and task.priority == "high"
    assert "company_list_notes" in (task.description or "")
    assert pending_wakes(app) == 0
    with app.state.session_factory() as db:
        from app.models.run import CompanyEvent

        types = [e.type for e in db.scalars(select(CompanyEvent).order_by(CompanyEvent.id))]
    assert "note.created" in types and "cofounder.woke" in types  # what the live UI listens to


def test_a_new_note_while_a_wake_task_has_not_started_merges_into_it(
    app, client, w, scheduler, clock
):
    cid = w.cid
    client.post(api(cid, f"/agents/{w.founder['id']}/pause"))
    post_note(client, cid, "first")
    wake_ready(clock)
    scheduler.tick()
    assert pending_wakes(app) == 1  # a paused agent keeps its request (and is not woken)
    client.post(api(cid, f"/agents/{w.founder['id']}/resume"))
    scheduler.tick()
    [task] = wake_tasks(app, cid)
    assert task.status in ("ready", "in_progress")
    # another reason arrives before the run started -> the same task learns about it
    with app.state.session_factory() as db:
        db.get(Task, task.id).status = "backlog"  # not started
        db.commit()
    post_note(client, cid, "second")
    wake_ready(clock)
    scheduler.tick()
    assert len(wake_tasks(app, cid)) == 1 and pending_wakes(app) == 0
    with app.state.session_factory() as db:
        assert "(also)" in db.get(Task, task.id).description


def test_events_during_a_running_wake_create_one_follow_up(
    app, client, w, scheduler, worker, scripted, clock
):
    cid = w.cid
    seen = []

    def founder(call):
        if not seen:
            seen.append(call.request.task.id)
            # a new Founder note arrives while the founder is mid-run
            post_note(client, cid, "late thought")
            # the request is due but the wake task is running: it must wait
            wakes.materialize_due(app.state.session_factory, clock() + timedelta(seconds=90))
            assert len(wake_tasks(app, cid)) == 1 and pending_wakes(app) == 1
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers["Atlas"] = founder
    post_note(client, cid, "first")
    wake_ready(clock)
    run_world(app, scheduler, worker, clock)
    assert scripted.errors == []
    assert len(wake_tasks(app, cid)) == 2 and pending_wakes(app) == 0
    assert all(t.status == "done" for t in wake_tasks(app, cid))


def test_wake_rate_limit_per_hour(app, client, w, scheduler, worker, scripted, clock):
    cid = w.cid
    set_policy(client, cid, max_planning_runs_per_hour=2)
    scripted.handlers["Atlas"] = lambda call: call.api.ok(
        "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
    )
    for i in range(4):
        post_note(client, cid, f"n{i}")
        wake_ready(clock)
        scheduler.tick()
        worker.drain()
    assert len(wake_tasks(app, cid)) == 2  # the third and fourth wakes are held back...
    assert pending_wakes(app) == 1
    clock.advance(3601)  # ...until the hourly window slides
    scheduler.tick()
    worker.drain()
    assert len(wake_tasks(app, cid)) == 3 and pending_wakes(app) == 0


def test_a_paused_company_never_wakes_anyone(app, client, w, scheduler, clock):
    cid = w.cid
    assert client.post(api(cid, "/pause")).status_code == 200
    post_note(client, cid, "while paused")
    wake_ready(clock)
    scheduler.tick()
    assert wake_tasks(app, cid) == [] and pending_wakes(app) == 1
    assert client.post(api(cid, "/cofounder/wake")).status_code == 409
    client.post(api(cid, "/resume"))
    scheduler.tick()
    assert len(wake_tasks(app, cid)) == 1  # the request survived the pause


def test_manual_wake_queues_then_coalesces_and_summary_shows_it(app, client, w, scheduler, clock):
    cid = w.cid
    first = client.post(api(cid, "/cofounder/wake"))
    assert first.status_code == 200 and first.json()["status"] == "queued"
    task_id = first.json()["task_id"]
    again = client.post(api(cid, "/cofounder/wake")).json()
    assert again == {"status": "coalesced", "task_id": task_id}
    assert client.get(api(cid, "/cofounder/summary")).json()["wake_pending"] is True
    assert len(wake_tasks(app, cid)) == 1


def test_restart_mid_plan_does_not_duplicate_wake_tasks(app, client, w, scheduler, engine, clock):
    from app.engine.scheduler import Scheduler

    cid = w.cid
    post_note(client, cid, "restart me")
    wake_ready(clock)
    scheduler.tick()
    assert len(wake_tasks(app, cid)) == 1
    restarted = Scheduler(engine)  # a fresh process picks the same DB up
    restarted.start()
    restarted.tick()
    restarted.tick()
    assert len(wake_tasks(app, cid)) == 1 and pending_wakes(app) == 0
    with app.state.session_factory() as db:
        keys = list(
            db.scalars(select(Task.idempotency_key).where(Task.idempotency_key.like("wake:%")))
        )
    assert len(keys) == len(set(keys)) == 1


def test_human_reply_reopens_a_triaged_note_and_wakes_again(
    app, client, w, scheduler, worker, scripted, clock
):
    cid = w.cid

    def founder(call):
        for note in call.api.ok("GET", "/notes")["items"]:
            call.api.ok(
                "POST",
                f"/notes/{note['id']}/triage",
                json={"category": "idea", "summary": "an idea", "reply": "Noted."},
            )
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers["Atlas"] = founder
    note = post_note(client, cid, "an idea")
    wake_ready(clock)
    run_world(app, scheduler, worker, clock)
    assert client.get(api(cid, f"/notes/{note['id']}")).json()["status"] == "triaged"
    reply = client.post(api(cid, f"/notes/{note['id']}/replies"), json={"body": "actually, do it"})
    assert reply.status_code == 201
    assert client.get(api(cid, f"/notes/{note['id']}")).json()["status"] == "new"
    assert pending_wakes(app) == 1
    wake_ready(clock)
    run_world(app, scheduler, worker, clock)
    detail = client.get(api(cid, f"/notes/{note['id']}")).json()
    assert detail["status"] == "triaged" and len(wake_tasks(app, cid)) == 2
    assert [r["author_type"] for r in detail["replies"]] == ["agent", "human", "agent"]


# ------------------------------------------------------------------ triage rules (agent API)


def founder_session(app, client, w):
    return start_run(app, client, w.cid, w.founder["id"], task_title="plan", kind="planning")


def test_only_the_cofounder_triages_and_actions_must_be_real_records_of_this_company(
    app, client, w
):
    cid = w.cid
    note = post_note(client, cid)
    other_cid = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    foreign_goal = client.post(api(other_cid, "/goals"), json={"title": "Not ours"}).json()
    goal = client.post(api(cid, "/goals"), json={"title": "Ours"}).json()
    sess = founder_session(app, client, w)

    body = {
        "category": "directive",
        "summary": "do it",
        "actions": [{"type": "goal", "ref_id": goal["id"]}],
    }
    # act first: a directive cannot be triaged by an agent that created nothing in this run
    act_first = sess.post(f"/notes/{note['id']}/triage", json=body)
    assert act_first.status_code == 409
    assert act_first.json()["error"]["details"] == {"code": "act_first"}
    idea = sess.post(f"/notes/{note['id']}/triage", json={**body, "category": "idea"})
    assert idea.status_code == 200
    assert sess.post("/goals", json={"title": "Created in this run"}).status_code == 201
    # a non-founder agent is refused whatever it sends
    lead = start_run(app, client, cid, w.lead["id"], task_title="x")
    assert lead.post(f"/notes/{note['id']}/triage", json=body).status_code == 403
    assert lead.get("/notes").status_code == 403
    # invented / foreign / mistyped references are validation errors with the offending index
    for actions in (
        [{"type": "goal", "ref_id": "00000000-0000-0000-0000-000000000000"}],
        [{"type": "goal", "ref_id": foreign_goal["id"]}],
        [{"type": "project", "ref_id": goal["id"]}],  # a goal id is not a project
    ):
        r = sess.post(f"/notes/{note['id']}/triage", json={**body, "actions": actions})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["details"][0]["loc"] == ["body", "actions", 0, "ref_id"]
    assert (
        sess.post(f"/notes/{note['id']}/triage", json={**body, "category": "bogus"}).status_code
        == 422
    )
    assert (
        sess.post(
            f"/notes/{note['id']}/triage",
            json={**body, "actions": [{"type": "alien", "ref_id": "x"}]},
        ).status_code
        == 422
    )
    assert sess.post("/notes/nope/triage", json=body).status_code == 404
    # the real thing works, with labels resolved server side, and the reply is optional
    ok = sess.post(
        f"/notes/{note['id']}/triage", json={**body, "confidence": 0.9, "reply": "On it."}
    )
    assert ok.status_code == 200, ok.text
    triage = ok.json()["triage"]
    assert triage["actions"] == [{"type": "goal", "ref_id": goal["id"], "label": "Ours"}]
    assert triage["confidence"] == 0.9 and ok.json()["status"] == "triaged"
    assert ok.json()["reply_count"] == 1 and ok.json()["last_reply"]["author_type"] == "agent"
    # archived notes stay archived
    client.patch(api(cid, f"/notes/{note['id']}"), json={"status": "archived"})
    assert sess.post(f"/notes/{note['id']}/triage", json=body).status_code == 409


def test_agent_note_listing_redacts_secrets_and_shows_the_thread(app, client, w):
    cid = w.cid
    note = post_note(client, cid, "use key sk-abcdefghijklmnopqrstuvwxyz123456 for the vendor")
    client.post(api(cid, f"/notes/{note['id']}/replies"), json={"body": "password: hunter2hunter2"})
    sess = founder_session(app, client, w)
    [seen] = sess.get("/notes").json()["items"]
    assert "sk-abc" not in seen["body"] and "[redacted]" in seen["body"]
    assert "hunter2" not in seen["replies"][0]["body"]
    # the Founder's own view keeps the text
    assert "sk-abc" in client.get(api(cid, f"/notes/{note['id']}")).json()["body"]


def test_requirement_and_context_notes_become_verified_memory_other_agents_see(app, client, w):
    cid = w.cid
    req = post_note(client, cid, "Never spend money. Zero budget for this launch.")
    ctx = post_note(client, cid, "We sell to freelancers.")
    idea = post_note(client, cid, "Maybe a podcast?")
    sess = founder_session(app, client, w)
    for note, cat in ((req, "requirement"), (ctx, "context"), (idea, "idea")):
        r = sess.post(
            f"/notes/{note['id']}/triage", json={"category": cat, "summary": f"{cat} note"}
        )
        assert r.status_code == 200, r.text
    entries = {m["source"]["id"]: m for m in client.get(api(cid, "/memory")).json()["items"]}
    assert set(entries) == {req["id"], ctx["id"]}  # the idea created none
    assert entries[req["id"]]["kind"] == "requirement" and entries[ctx["id"]]["kind"] == "fact"
    for m in entries.values():
        assert m["status"] == "verified" and m["verified_by_type"] == "human"
        assert (
            m["source"]["type"] == "note"
            and m["verified_by_id"] == client.get("/api/me").json()["user"]["id"]
        )
    # re-triaging does not duplicate
    sess.post(f"/notes/{req['id']}/triage", json={"category": "requirement", "summary": "again"})
    assert len(client.get(api(cid, "/memory")).json()["items"]) == 2
    # a colleague sees them in its context (and in the run prompt data), only verified ones
    worker_session = start_run(app, client, cid, w.specialist["id"], task_title="work")
    ctx_memory = worker_session.get("/context").json()["memory"]
    assert {m["body"] for m in ctx_memory} == {
        "Never spend money. Zero budget for this launch.",
        "We sell to freelancers.",
    }


def test_a_secret_in_a_requirement_note_is_not_copied_into_memory(app, client, w):
    cid = w.cid
    note = post_note(
        client, cid, "Requirement: deploy with token ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    )
    sess = founder_session(app, client, w)
    r = sess.post(
        f"/notes/{note['id']}/triage", json={"category": "requirement", "summary": "deploy"}
    )
    assert r.status_code == 200
    assert client.get(api(cid, "/memory")).json()["items"] == []
    with app.state.session_factory() as db:
        assert db.scalar(select(MemoryEntry)) is None


def test_wake_request_rows_are_tenant_scoped(app, client, w):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    post_note(client, w.cid)
    with app.state.session_factory() as db:
        rows = list(db.scalars(select(WakeRequest)))
    assert {r.company_id for r in rows} == {w.cid}
    assert Principal(PrincipalType.AGENT, "a", "A", w.cid) and other
