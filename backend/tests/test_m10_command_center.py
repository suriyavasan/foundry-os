"""M10 section 5: the Command Center. Slash commands run with the human's own permissions; free
text is a Founder's note whose answer is guaranteed (fallback output, one automatic retry, manual retry)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.time import utcnow
from app.models.autonomy import FounderNote, WakeRequest
from app.models.commands import CommandExchange
from app.models.company import Company
from app.services import command_center
from tests.autonomy_helpers import run_world, scripted_world, tasks_of, wake_ready
from tests.conftest import human
from tests.helpers import agent_of_family, api, new_agent, new_task
from tests.test_agent_api import start_run
from tests.test_autonomy_notes import answer_new_notes


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def cmd(client, cid, text):
    r = client.post(api(cid, "/command"), json={"input": text})
    assert r.status_code == 201, r.text
    return r.json()


def feed(client, cid, **params):
    r = client.get(api(cid, "/command-center/feed"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ slash commands


def test_every_slash_command_answers_instantly_with_a_structured_output(client, w):
    cid = w.cid
    task = new_task(client, cid, title="Draft the page", assignee_agent_id=w.specialist["id"])
    for text, kind in (
        ("/help", "table"),
        ("/status", "list"),
        ("/goals", "text"),
        ("/agents", "table"),
        ("/tasks", "table"),
        ("/tasks blocked", "text"),
        ("/spend", "table"),
        ("/spend month", "table"),
        ("/approvals", "text"),
        ("/standards", "text"),
    ):
        ex = cmd(client, cid, text)
        assert ex["input"] == text and ex["input_kind"] == "command" and ex["status"] == "done", (
            text
        )
        assert ex["output"]["kind"] == kind and ex["output"]["title"], text
        assert ex["note_id"] is None and ex["run_id"] is None
    help_ = cmd(client, cid, "/help")["output"]
    assert help_["columns"] == ["Command", "What it does"]
    listed = {row[0].split()[0] for row in help_["rows"]}
    assert listed == {
        "/help",
        "/status",
        "/goals",
        "/agents",
        "/tasks",
        "/spend",
        "/approvals",
        "/standards",
        "/pause",
        "/resume",
        "/goal",
        "/task",
        "/hire",
        "/approve",
        "/reject",
    }
    tasks = cmd(client, cid, "/tasks")["output"]
    assert tasks["columns"] == ["Task", "Status", "Assignee", "Priority"]
    assert ["Draft the page", "ready", "Sam", "normal"] in tasks["rows"]
    assert {"label": "Tasks", "panel": "tasks"} in tasks["links"]
    agents = cmd(client, cid, "/agents")["output"]
    assert {row[0] for row in agents["rows"]} >= {"Sam", "Lena", "Harper"}
    status = cmd(client, cid, "/status")["output"]
    assert any(r[0].startswith("Spend today: $0.00") and "verified" in r[0] for r in status["rows"])
    assert any(r[0].startswith("Team: 4 agents") for r in status["rows"])
    spend = cmd(client, cid, "/spend")["output"]
    assert [r[0] for r in spend["rows"]][:3] == [
        "Verified (a charge)",
        "Estimated (not a charge)",
        "Unknown (no price)",
    ]
    assert task["id"]


def test_errors_are_outputs_not_silence(client, w):
    cid = w.cid
    for text, needle in (
        ("/nope", "not a command"),
        ("/tasks sideways", "Unknown status"),
        ("/spend year", "Use /spend day"),
        ("/goal", "Usage"),
        ("/task", "Usage"),
        ("/task Do it @nobody", "No single active agent"),
        ("/hire", "Usage"),
        ("/hire wizard", "Unknown role template"),
        ("/approve", "Usage"),
        ("/approve not-an-id", "not found"),
    ):
        ex = cmd(client, cid, text)
        assert ex["status"] == "done" and ex["output"]["kind"] == "error", text
        assert needle.lower() in ex["output"]["text"].lower(), (text, ex["output"])
    assert "Did you mean /status" in cmd(client, cid, "/stat")["output"]["text"]


def test_write_commands_do_what_the_services_do(client, w):
    cid = w.cid
    ex = cmd(client, cid, "/goal Reach 100 customers by June")
    assert ex["output"]["title"] == "Goal created"
    [action] = ex["actions"]
    assert action["type"] == "goal" and action["label"] == "Reach 100 customers by June"
    goals = client.get(api(cid, "/goals")).json()["items"]
    assert [g["id"] for g in goals] == [action["ref_id"]]
    ex = cmd(client, cid, "/task Write the landing page @sam")
    [action] = ex["actions"]
    task = client.get(api(cid, f"/tasks/{action['ref_id']}")).json()
    assert (
        task["title"] == "Write the landing page"
        and task["assignee_agent_id"] == w.specialist["id"]
    )
    assert {"label": "Task", "panel": f"task:{task['id']}"} in ex["output"]["links"]
    unassigned = cmd(client, cid, "/task Think about pricing")["actions"][0]["ref_id"]
    assert client.get(api(cid, f"/tasks/{unassigned}")).json()["assignee_agent_id"] is None
    ex = cmd(client, cid, "/hire software-engineer needs a backend person")
    [action] = ex["actions"]
    hiring = client.get(api(cid, f"/tasks/{action['ref_id']}")).json()
    assert hiring["kind"] == "hiring" and hiring["assignee_agent_id"] == w.hr["id"]
    assert (
        "software-engineer" in hiring["description"] and "backend person" in hiring["description"]
    )
    assert cmd(client, cid, "/pause")["output"]["title"] == "Paused"
    assert client.get(api(cid)).json()["status"] == "paused"
    assert cmd(client, cid, "/pause")["output"]["kind"] == "error"  # already paused
    assert cmd(client, cid, "/resume")["output"]["title"] == "Resumed"
    assert client.get(api(cid)).json()["status"] == "active"


def test_approve_and_reject_decide_real_approvals(app, client, w):
    cid = w.cid
    session = start_run(app, client, cid, w.lead["id"])
    made = []
    for title in ("Buy ads", "Buy domain"):
        r = session.post(
            "/approvals",
            json={"kind": "external_purchase", "title": title, "amount_micros": 5_000_000},
        )
        assert r.status_code == 201, r.text
        made.append(r.json()["approval"]["id"] if "approval" in r.json() else r.json()["id"])
    pending = cmd(client, cid, "/approvals")["output"]
    assert pending["columns"][0] == "Id" and {row[0] for row in pending["rows"]} == set(made)
    ok = cmd(client, cid, f"/approve {made[0]}")
    assert ok["output"]["title"] == "Approved" and ok["actions"][0]["ref_id"] == made[0]
    no = cmd(client, cid, f"/reject {made[1]} too expensive")
    assert no["output"]["title"] == "Rejected"
    statuses = {a["id"]: a["status"] for a in client.get(api(cid, "/approvals")).json()["items"]}
    assert statuses[made[0]] == "approved" and statuses[made[1]] == "rejected"
    assert cmd(client, cid, f"/approve {made[0]}")["output"]["kind"] == "error"  # already decided


# ------------------------------------------------------------------ permissions


def roles(client, w, make_user, add_member, client_as):
    out = {}
    for role in ("admin", "member", "viewer"):
        u = make_user(f"{role}@x.test")
        add_member(w.cid, u.id, role)
        out[role] = client_as(human(u))
    return out


def test_commands_run_with_the_humans_own_permissions(client, w, make_user, add_member, client_as):
    cid = w.cid
    people = roles(client, w, make_user, add_member, client_as)
    reads = (
        "/help",
        "/status",
        "/goals",
        "/agents",
        "/tasks",
        "/spend",
        "/approvals",
        "/standards",
    )
    writes = (
        "/pause",
        "/goal Take over",
        "/task Sneaky @sam",
        "/hire software-engineer",
        "/approve x",
    )
    for role in ("member", "viewer"):
        for text in reads:
            assert cmd(people[role], cid, text)["output"]["kind"] != "error", (role, text)
        for text in writes:
            ex = cmd(people[role], cid, text)
            assert ex["output"]["kind"] == "error", (role, text)
            assert ex["output"]["text"]  # it says why
    assert client.get(api(cid, "/goals")).json()["items"] == []  # nothing happened
    assert client.get(api(cid)).json()["status"] == "active"
    assert tasks_of(client.app, cid, title="Sneaky") == []
    # an admin may do what an admin may do
    assert cmd(people["admin"], cid, "/goal Admin goal")["output"]["title"] == "Goal created"
    # free text is a note: viewers and members cannot write notes
    for role in ("member", "viewer"):
        r = people[role].post(api(cid, "/command"), json={"input": "hello there"})
        assert r.status_code == 403, role


def test_agents_and_strangers_cannot_use_the_command_center(app, client, w, make_user, client_as):
    cid = w.cid
    session = start_run(app, client, cid, w.lead["id"])
    for method, path, body in (
        ("post", api(cid, "/command"), {"input": "/status"}),
        ("get", api(cid, "/command-center/feed"), None),
        ("post", api(cid, "/command-center/x/retry"), None),
    ):
        r = getattr(client, method)(
            path, headers=session.headers, **({"json": body} if body else {})
        )
        assert r.status_code == 403, (path, r.text)
    stranger = client_as(human(make_user("s@x.test")))
    assert stranger.post(api(cid, "/command"), json={"input": "/status"}).status_code == 404
    assert stranger.get(api(cid, "/command-center/feed")).status_code == 404
    assert stranger.post(api(cid, "/command-center/x/retry")).status_code == 404


def test_the_feed_is_per_company_newest_first_and_paginated(client, w, make_user, client_as):
    cid = w.cid
    for i in range(5):
        cmd(client, cid, f"/tasks {'blocked' if i % 2 else ''}".strip())
    cmd(client, cid, "a free thought")
    page = feed(client, cid, limit=4)
    assert len(page["items"]) == 4 and page["next_cursor"]
    assert (
        page["items"][0]["input"] == "a free thought" and page["items"][0]["input_kind"] == "note"
    )
    rest = feed(client, cid, limit=4, cursor=page["next_cursor"])
    ids = [e["id"] for e in page["items"]] + [e["id"] for e in rest["items"]]
    assert len(ids) == len(set(ids)) == 6 and rest["next_cursor"] is None
    other = client_as(human(make_user("o@x.test")))
    oc = other.post("/api/companies", json={"name": "Other"}).json()["id"]
    assert feed(other, oc)["items"] == []
    assert (
        client.get(api(cid, "/command-center/feed"), params={"cursor": "garbage"}).status_code
        == 400
    )


# ------------------------------------------------------------------ free text


def test_free_text_is_a_note_that_wakes_the_cofounder_fast_and_with_priority(app, client, w):
    cid = w.cid
    ex = cmd(client, cid, "I think we should focus on freelancers")
    assert ex["input_kind"] == "note" and ex["status"] == "pending" and ex["output"] is None
    assert ex["note_id"] and ex["run_id"] is None and ex["actions"] == []
    note = client.get(api(cid, f"/notes/{ex['note_id']}")).json()
    assert note["body"] == "I think we should focus on freelancers" and note["status"] == "new"
    with app.state.session_factory() as db:
        [wake] = db.scalars(select(WakeRequest)).all()
        assert wake.trigger == "command"
        assert wake.not_before <= utcnow() + timedelta(
            seconds=8
        )  # 5 s, not the note debounce (60 s)
    # a plain Founder's note (the notes API) keeps the 60 s debounce
    client.post(api(cid, "/notes"), json={"body": "another"})
    with app.state.session_factory() as db:
        [wake] = db.scalars(select(WakeRequest)).all()
        assert wake.not_before <= utcnow() + timedelta(seconds=8)  # earliest due time wins


def test_the_wake_task_for_a_command_is_urgent(app, client, w, scheduler, scripted, worker):
    cid = w.cid
    seen = []

    def founder(call):
        seen.append(call.request.task.priority)
        answer_new_notes(call)
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = founder
    cmd(client, cid, "hello Atlas")
    clock = scheduler.ctx.clock
    wake_ready(clock, 10)
    run_world(app, scheduler, worker, clock)
    assert seen == ["urgent"]


def run_founder(app, scheduler, worker, clock):
    wake_ready(clock, 10)
    run_world(app, scheduler, worker, clock)


def test_the_exchange_moves_pending_processing_answered_with_action_chips(
    app, client, w, scheduler, scripted, worker, clock
):
    cid = w.cid
    snapshots = []

    def founder(call):
        ex = feed(client, cid)["items"][0]
        snapshots.append((ex["status"], ex["run_id"] == call.request.run_id))
        goal = call.api.ok("POST", "/goals", json={"title": "Serve freelancers"})
        [note] = call.api.ok("GET", "/notes")["items"]
        call.api.ok(
            "POST",
            f"/notes/{note['id']}/triage",
            json={
                "category": "goal",
                "summary": "Focus on freelancers",
                "actions": [{"type": "goal", "ref_id": goal["id"]}],
                "reply": "Done: I set a goal to serve freelancers.",
            },
        )
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = founder
    ex = cmd(client, cid, "Focus on freelancers")
    run_founder(app, scheduler, worker, clock)
    assert scripted.errors == [] and snapshots == [
        ("processing", True)
    ]  # live progress: the run id
    done = feed(client, cid)["items"][0]
    assert done["id"] == ex["id"] and done["status"] == "answered"
    assert done["output"] == {
        "kind": "text",
        "title": "AI Co-founder",
        "text": "Done: I set a goal to serve freelancers.",
        "columns": None,
        "rows": None,
        "links": None,
    }
    [chip] = done["actions"]
    assert chip["type"] == "goal" and chip["label"] == "Serve freelancers" and chip["ref_id"]
    assert done["run_id"] and done["updated_at"] >= done["created_at"]


def test_a_reply_without_triage_is_an_answer_too(
    app, client, w, scheduler, scripted, worker, clock
):
    cid = w.cid

    def founder(call):
        answer_new_notes(call)
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = founder
    cmd(client, cid, "What is our status?")
    run_founder(app, scheduler, worker, clock)
    done = feed(client, cid)["items"][0]
    assert done["status"] == "answered" and done["output"]["text"] == "noted"


def test_a_human_reply_on_the_note_puts_the_exchange_back_in_the_queue(
    app, client, w, scheduler, scripted, worker, clock
):
    cid = w.cid

    def founder(call):
        [note] = call.api.ok("GET", "/notes")["items"]
        call.api.ok(
            "POST",
            f"/notes/{note['id']}/triage",
            json={"category": "idea", "summary": "ok", "reply": "Nice idea."},
        )
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = founder
    ex = cmd(client, cid, "an idea")
    run_founder(app, scheduler, worker, clock)
    assert feed(client, cid)["items"][0]["status"] == "answered"
    client.post(api(cid, f"/notes/{ex['note_id']}/replies"), json={"body": "tell me more"})
    again = feed(client, cid)["items"][0]
    assert again["status"] == "pending" and again["output"] is None


# ------------------------------------------------------------------ guaranteed output


def test_a_run_that_fails_or_stays_silent_gets_a_fallback_then_one_automatic_retry_then_failed(
    app, client, w, scheduler, scripted, worker, clock
):
    cid = w.cid
    attempts = []

    def silent(call):  # finishes without ever answering the note
        attempts.append(1)
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = silent
    ex = cmd(client, cid, "Are you there?")
    run_founder(app, scheduler, worker, clock)
    assert len(attempts) >= 1
    # after the first silent run: the system explained, and is trying again by itself
    state = next(e for e in feed(client, cid)["items"] if e["id"] == ex["id"])
    assert state["status"] in ("pending", "processing", "failed")
    run_world(app, scheduler, worker, clock)
    final = next(e for e in feed(client, cid)["items"] if e["id"] == ex["id"])
    assert final["status"] == "failed", final  # exactly one retry, then it stops
    assert (
        final["output"]["kind"] == "error"
        and "could not answer after 2 tries" in final["output"]["text"]
    )
    assert "without replying" in final["output"]["text"] and "saved" in final["output"]["text"]
    assert len(attempts) == 2
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(CommandExchange.attempts).where(CommandExchange.id == ex["id"])) == 2
        )
    # manual retry: pending again, the AI Co-founder is woken, and this time it answers
    scripted.handlers[w.founder["name"]] = lambda call: (
        answer_new_notes(call),
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        ),
    )
    r = client.post(api(cid, f"/command-center/{ex['id']}/retry"))
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["output"] is None
    run_founder(app, scheduler, worker, clock)
    assert (
        next(e for e in feed(client, cid)["items"] if e["id"] == ex["id"])["status"] == "answered"
    )


def test_a_failing_run_explains_what_happened(app, client, w, scheduler, scripted, worker, clock):
    cid = w.cid

    def boom(call):
        raise RuntimeError("model exploded")

    scripted.handlers[w.founder["name"]] = boom
    ex = cmd(client, cid, "Hello?")
    run_founder(app, scheduler, worker, clock)
    first = next(e for e in feed(client, cid)["items"] if e["id"] == ex["id"])
    assert first["output"] is not None and "run failed" in first["output"]["text"]
    assert "once more automatically" in first["output"]["text"] or first["status"] == "failed"


def test_nothing_answering_within_fifteen_minutes_writes_the_fallback(
    app, client, w, scheduler, clock
):
    cid = w.cid
    ex = cmd(client, cid, "Anyone?")
    # the scheduler has no founder runtime that answers; time passes
    clock.advance(16 * 60)
    n = command_center.sweep(app.state.session_factory, clock())
    assert n == 1
    state = feed(client, cid)["items"][0]
    assert state["id"] == ex["id"] and "within 15 minutes" in state["output"]["text"]
    assert state["status"] == "pending"  # the automatic retry is queued
    clock.advance(16 * 60)
    command_center.sweep(app.state.session_factory, clock())
    final = feed(client, cid)["items"][0]
    assert final["status"] == "failed" and final["output"]["kind"] == "error"
    assert command_center.sweep(app.state.session_factory, clock()) == 0  # nothing left to do


def test_the_clock_does_not_run_against_a_paused_company(app, client, w, clock):
    cid = w.cid
    cmd(client, cid, "Anyone?")
    with app.state.session_factory() as db:
        db.execute(update(Company).where(Company.id == cid).values(status="paused"))
        db.commit()
    clock.advance(60 * 60)
    assert command_center.sweep(app.state.session_factory, clock()) == 0
    assert feed(client, cid)["items"][0]["status"] == "pending"


def test_retry_only_applies_to_failed_notes(app, client, w):
    cid = w.cid
    ex = cmd(client, cid, "hello")
    assert client.post(api(cid, f"/command-center/{ex['id']}/retry")).status_code == 409  # pending
    done = cmd(client, cid, "/help")
    r = client.post(api(cid, f"/command-center/{done['id']}/retry"))
    assert r.status_code == 409 and "instantly" in r.json()["error"]["message"]
    assert client.post(api(cid, "/command-center/nope/retry")).status_code == 404


def test_a_cancelled_run_for_a_pause_does_not_burn_an_attempt(app, client, w):
    cid = w.cid
    ex = cmd(client, cid, "hello")
    from app.models.run import TaskRun

    with app.state.session_factory() as db:
        task = new_task(client, cid, title="x", assignee_agent_id=w.specialist["id"])
        run = TaskRun(
            company_id=cid,
            task_id=task["id"],
            agent_id=w.founder["id"],
            attempt_number=1,
            status="running",
            runtime="scripted",
            idempotency_key="k1",
            queued_at=utcnow(),
        )
        db.add(run)
        db.flush()
        row = db.get(CommandExchange, ex["id"])
        row.status, row.run_id = "processing", run.id
        command_center.on_run_finished(db, run, "cancelled", "company_paused", None, utcnow())
        db.commit()
        row = db.get(CommandExchange, ex["id"])
        assert row.status == "pending" and row.attempts == 1 and row.run_id is None


def test_note_exchanges_exist_for_notes_written_through_the_notes_api(client, w):
    cid = w.cid
    note = client.post(api(cid, "/notes"), json={"body": "from the CLI"}).json()
    top = feed(client, cid)["items"][0]
    assert (
        top["note_id"] == note["id"]
        and top["input"] == "from the CLI"
        and top["status"] == "pending"
    )
    assert agent_of_family(client, cid, "founder")["id"] == w.founder["id"]
    assert new_agent  # (helper imported for symmetry with other suites)
    with client.app.state.session_factory() as db:
        assert db.scalar(select(FounderNote.status).where(FounderNote.id == note["id"])) == "new"


def test_a_triage_alone_waits_for_the_reply_of_the_same_run_then_falls_back_to_the_summary(
    app, client, w, scheduler, scripted, worker, clock
):
    cid = w.cid
    seen = []

    def triage_then_reply(call):
        [note] = call.api.ok("GET", "/notes")["items"]
        call.api.ok(
            "POST",
            f"/notes/{note['id']}/triage",
            json={"category": "idea", "summary": "Noted the idea"},
        )
        seen.append(feed(client, cid)["items"][0]["status"])  # recorded, but not yet answered
        call.api.ok(
            "POST", f"/notes/{note['id']}/replies", json={"body": "Great idea, I will plan it."}
        )
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = triage_then_reply
    cmd(client, cid, "An idea")
    run_founder(app, scheduler, worker, clock)
    done = feed(client, cid)["items"][0]
    assert seen == ["processing"] and done["status"] == "answered"
    assert done["output"]["text"] == "Great idea, I will plan it."

    def triage_only(call):
        [note] = call.api.ok("GET", "/notes")["items"]
        call.api.ok(
            "POST",
            f"/notes/{note['id']}/triage",
            json={"category": "idea", "summary": "Noted the second idea"},
        )
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
        )

    scripted.handlers[w.founder["name"]] = triage_only
    cmd(client, cid, "A second idea")
    run_founder(app, scheduler, worker, clock)
    done = feed(client, cid)["items"][0]
    assert done["status"] == "answered" and "Noted the second idea" in done["output"]["text"]
