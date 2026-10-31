"""M22 human work: an AI employee asks a person for something only a person can do."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.run import TaskRun
from app.models.work import Task, TaskDependency
from tests.accounts_helpers import browser, create_company
from tests.helpers import api, get_task, new_agent, settle
from tests.m22_helpers import (  # noqa: F401  (``world`` is a fixture)
    member_id,
    refs,
    session,
    specialist,
    world,
)

BODY = {
    "title": "Sign the NDA",
    "instructions": "Open the e-sign mail from Acme and sign page 3.",
    "category": "signature",
    "why_human": "A legal signature must be given by a person",
}


def ask(s, **over):
    return s.post("/human-work", json={**BODY, **over})


def audit_actions(app, cid) -> list[str]:
    with app.state.session_factory() as db:
        return [
            a
            for a in db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.company_id == cid)
                .order_by(AuditEvent.created_at)
            )
        ]


def wake_texts(app, agent_id) -> list[str]:
    with app.state.session_factory() as db:
        return [
            r["text"]
            for req in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id))
            for r in req.reasons
        ]


# ------------------------------------------------------------------ the request


def test_a_request_becomes_a_pool_task_and_blocks_the_agents_task(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    sam = specialist(owner, cid)
    s = session(app, owner, cid, sam["id"], anon_client, task_title="Close the deal")
    r = ask(s)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["pool"] is True and out["assignee_ref"] is None and out["blocks_my_task"] is True
    assert out["status"] == "backlog"  # nobody has it yet: it is in the human pool
    human = get_task(owner, cid, out["task_id"])
    assert human["requires_human"] is True and human["human_category"] == "signature"
    assert human["human_instructions"].startswith("Open the e-sign mail")
    assert human["requested_by_agent_id"] == sam["id"] and human["kind"] == "work"
    # the agent's own task now depends on it (the plain dependency mechanism)
    mine = get_task(owner, cid, s.task_id)
    assert mine["depends_on"] == [out["task_id"]]
    assert "human_work.requested" in audit_actions(app, cid)


def test_the_agents_task_waits_and_resumes_with_the_persons_comment(app, world, anon_client):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    sam = specialist(owner, cid)
    s = session(app, owner, cid, sam["id"], anon_client, task_title="Close the deal")
    human_id = ask(s).json()["task_id"]
    # the run ends (the agent stops, or even calls complete): nothing completes, the task waits
    done = s.post(f"/tasks/{s.task_id}/complete", json={"result_summary": "all done"})
    assert done.status_code == 200, done.text
    waiting = get_task(owner, cid, s.task_id)
    assert waiting["status"] == "backlog" and waiting["blocked_reason"] == "waiting_for_human"
    assert waiting["result_summary"] is None and waiting["attempt_count"] == 0  # attempt refunded
    # a person takes it from the pool, does it and says what they did
    assert bob.post(api(cid, f"/human-work/{human_id}/claim")).status_code == 200
    assert get_task(owner, cid, human_id)["status"] == "ready"
    assert get_task(owner, cid, s.task_id)["status"] == "backlog"  # still waiting
    files = {"file": ("nda.txt", b"signed", "text/plain")}
    up = bob.post(
        api(cid, "/artifacts"),
        data={"title": "Signed NDA", "kind": "other", "task_id": human_id},
        files=files,
    )
    assert up.status_code == 201, up.text
    comp = bob.post(
        api(cid, f"/tasks/{human_id}/complete"),
        json={"comment": "Signed and e-mailed back", "artifact_ids": [up.json()["id"]]},
    )
    assert comp.status_code == 200 and comp.json()["status"] == "done"
    # the agent's task is ready again, and its next run sees the person's comment and file
    assert get_task(owner, cid, s.task_id)["status"] == "ready"
    assert get_task(owner, cid, s.task_id)["blocked_reason"] is None
    with app.state.session_factory() as db:
        task = db.get(Task, s.task_id)
        task.status = "in_progress"
        run = db.scalar(select(TaskRun).where(TaskRun.id == s.run_id))
        run.status = "running"
        db.commit()
    ctx = s.get("/context").json()
    dep = ctx["task"]["dependencies"][0]
    assert dep["id"] == human_id and dep["status"] == "done"
    assert dep["result_summary"] == "Signed and e-mailed back"
    assert dep["output_refs"] == [up.json()["id"]]


def test_not_blocking_leaves_the_agents_task_alone(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    out = ask(s, blocks_my_task=False).json()
    assert out["blocks_my_task"] is False
    assert get_task(owner, cid, s.task_id)["depends_on"] == []
    assert s.post(f"/tasks/{s.task_id}/complete", json={"result_summary": "ok"}).status_code == 200
    assert get_task(owner, cid, s.task_id)["status"] == "done"


def test_only_work_tasks_are_held_up_by_a_request(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client, kind="routine")
    out = ask(s).json()
    assert out["blocks_my_task"] is False and "not blocked" in out["next"]
    assert get_task(owner, cid, s.task_id)["depends_on"] == []


def test_a_specific_member_gets_it_and_is_notified(app, world, anon_client):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    s = session(app, owner, cid, specialist(owner, cid, "Nova")["id"], anon_client)
    out = ask(s, assignee_member_id=member_id(people, cid, "member")).json()
    assert out["pool"] is False and out["assignee_ref"] == refs(people, cid)["member"]
    assert out["status"] == "ready"
    mine = bob.get("/api/notifications").json()["items"]
    note = next(n for n in mine if n["kind"] == "human_work")
    assert "Nova needs a person" in note["title"] and note["related_id"] == out["task_id"]
    assert not any(
        n["kind"] == "human_work"
        for n in people["member2"].get("/api/notifications").json()["items"]
    )


def test_a_pool_request_notifies_the_owners_and_admins(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    ask(s)
    for who in ("owner", "admin"):
        items = people[who].get("/api/notifications").json()["items"]
        assert any(n["kind"] == "human_work" and "nobody has taken it" in n["title"] for n in items)
    for who in ("member", "viewer"):
        items = people[who].get("/api/notifications").json()["items"]
        assert not any(n["kind"] == "human_work" for n in items)


def test_the_assignee_must_be_an_active_non_viewer_member_of_this_company(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    assert ask(s, assignee_member_id=member_id(people, cid, "viewer")).status_code == 422
    assert ask(s, assignee_member_id="9" * 36).status_code == 404
    # a member of ANOTHER company is not one of ours
    other_cid = create_company(owner, "Other Co")["id"]
    other_member = owner.get("/api/me").json()["memberships"]
    foreign = next(m["member_id"] for m in other_member if m["company_id"] == other_cid)
    assert ask(s, assignee_member_id=foreign).status_code == 404
    assert get_task(owner, cid, s.task_id)["depends_on"] == []  # nothing was created


def test_the_limits_are_code_three_per_agent_and_twenty_per_company(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    sam, kim = specialist(owner, cid, "Sam"), specialist(owner, cid, "Kim")
    s = session(app, owner, cid, sam["id"], anon_client)
    for i in range(3):
        assert ask(s, title=f"Call {i}", blocks_my_task=False).status_code == 201
    over = ask(s, title="One more", blocks_my_task=False)
    assert over.status_code == 429 and over.json()["error"]["code"] == "human_work_limit"
    assert "company_ask_founder" in over.json()["error"]["message"]
    # company-wide: 20 open in total
    from app.models.enums import TaskKind

    with app.state.session_factory() as db:
        for i in range(17):
            db.add(
                Task(
                    company_id=cid,
                    title=f"filler {i}",
                    kind=TaskKind.WORK,
                    status="backlog",
                    requires_human=True,
                    human_category="other",
                    created_by_type="agent",
                    requested_by_agent_id=None,
                )
            )
        db.commit()
    s2 = session(app, owner, cid, kim["id"], anon_client)
    cap = ask(s2, blocks_my_task=False)
    assert cap.status_code == 429 and cap.json()["error"]["details"]["limit"].endswith("company")
    # finishing / cancelling frees a slot of the agent's own budget
    first = owner.get(api(cid, "/human-work")).json()["items"]
    mine = [i for i in first if i["requested_by_agent_id"] == sam["id"]]
    assert len(mine) == 3


def test_a_secret_in_the_request_is_refused(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    bad = ask(s, instructions="Log in with password sk-ant-api03-" + "a" * 40)
    assert bad.status_code == 422
    assert owner.get(api(cid, "/human-work")).json()["items"] == []


def test_only_agents_may_ask(app, world):
    people, cid = world
    r = people["owner"].post("/api/agent/v1/human-work", json=BODY)
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------ the pool


def test_a_second_claim_is_a_409_and_a_viewer_cannot_claim(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s).json()["task_id"]
    assert people["viewer"].post(api(cid, f"/human-work/{hid}/claim")).status_code == 403
    first = people["member"].post(api(cid, f"/human-work/{hid}/claim"))
    assert first.status_code == 200 and first.json()["assignee_ref"] == refs(people, cid)["member"]
    second = people["member2"].post(api(cid, f"/human-work/{hid}/claim"))
    assert second.status_code == 409
    assert people["member"].post(api(cid, f"/human-work/{hid}/claim")).status_code == 409
    assert audit_actions(app, cid).count("human_work.claimed") == 1


def test_listing_open_mine_and_done_with_overdue_first(app, world, anon_client):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    late = ask(s, title="Late", blocks_my_task=False, due_in_hours=1).json()["task_id"]
    soon = ask(s, title="Soon", blocks_my_task=False, due_in_hours=24).json()["task_id"]
    with app.state.session_factory() as db:  # the first one is now overdue
        db.get(Task, soon).due_at = db.get(Task, late).due_at - timedelta(days=2)
        db.commit()
    items = bob.get(api(cid, "/human-work")).json()["items"]
    assert [i["id"] for i in items] == [soon, late] and items[0]["overdue"] is True
    assert items[1]["overdue"] is False and all(i["pool"] for i in items)
    assert bob.post(api(cid, f"/human-work/{late}/claim")).status_code == 200
    mine = bob.get(api(cid, "/human-work"), params={"mine": "true"}).json()["items"]
    assert [i["id"] for i in mine] == [late] and mine[0]["assignee_name"]
    assert (
        people["member2"].get(api(cid, "/human-work"), params={"mine": "true"}).json()["items"]
        == []
    )
    assert (
        bob.post(api(cid, f"/tasks/{late}/complete"), json={"comment": "done"}).status_code == 200
    )
    done = bob.get(api(cid, "/human-work"), params={"status": "done"}).json()["items"]
    assert [i["id"] for i in done] == [late] and done[0]["result_summary"] == "done"
    assert [i["id"] for i in bob.get(api(cid, "/human-work")).json()["items"]] == [soon]


def test_agents_cannot_read_or_claim_human_work(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s).json()["task_id"]
    assert s.post(f"/human-work/{hid}/claim").status_code in (401, 403, 404, 405)


def test_tenancy_another_company_sees_and_touches_nothing(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s).json()["task_id"]
    other = create_company(owner, "Other Co")["id"]
    assert owner.get(api(other, "/human-work")).json()["items"] == []
    for call in (
        lambda: owner.get(api(other, f"/human-work/{hid}")),
        lambda: owner.post(api(other, f"/human-work/{hid}/claim")),
        lambda: owner.post(api(other, f"/human-work/{hid}/decline"), json={"reason": "x"}),
    ):
        assert call().status_code == 404
    stranger = browser(app)
    assert stranger.get(api(cid, "/human-work")).status_code == 401


# ------------------------------------------------------------------ decline


def test_declining_wakes_the_agent_and_returns_its_task_with_a_note(app, world, anon_client):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    sam = specialist(owner, cid)
    s = session(app, owner, cid, sam["id"], anon_client, task_title="Close the deal")
    hid = ask(s).json()["task_id"]
    s.post(f"/tasks/{s.task_id}/complete", json={"result_summary": "x"})
    assert get_task(owner, cid, s.task_id)["status"] == "backlog"
    # members cannot decline, admins can; a reason is required
    assert (
        bob.post(api(cid, f"/human-work/{hid}/decline"), json={"reason": "no"}).status_code == 403
    )
    assert people["admin"].post(api(cid, f"/human-work/{hid}/decline"), json={}).status_code == 422
    r = people["admin"].post(
        api(cid, f"/human-work/{hid}/decline"), json={"reason": "We never sign NDAs by e-mail"}
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    mine = get_task(owner, cid, s.task_id)
    assert mine["status"] == "ready" and mine["depends_on"] == []
    assert "We never sign NDAs by e-mail" in mine["review_note"]
    assert mine["blocked_reason"] is None
    assert any("We never sign NDAs by e-mail" in t for t in wake_texts(app, sam["id"]))
    assert "human_work.declined" in audit_actions(app, cid)
    assert (
        people["admin"]
        .post(api(cid, f"/human-work/{hid}/decline"), json={"reason": "again"})
        .status_code
        == 409
    )


def test_cancelling_the_task_the_normal_way_does_not_strand_the_agent(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s).json()["task_id"]
    s.post(f"/tasks/{s.task_id}/complete", json={"result_summary": "x"})
    assert (
        owner.post(api(cid, f"/tasks/{hid}/cancel"), json={"reason": "not needed"}).status_code
        == 200
    )
    assert get_task(owner, cid, s.task_id)["status"] == "ready"


def test_a_human_task_cannot_be_given_to_an_agent(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    sam = specialist(owner, cid)
    s = session(app, owner, cid, sam["id"], anon_client)
    hid = ask(s).json()["task_id"]
    r = owner.patch(api(cid, f"/tasks/{hid}"), json={"assignee_agent_id": sam["id"]})
    assert r.status_code == 422


# ------------------------------------------------------------------ never dispatched


def test_human_tasks_are_never_dispatched_to_a_runtime(app, world, anon_client, scheduler, worker):
    people, cid = world
    owner = people["owner"]
    nova = new_agent(owner, cid, name="Nova", script={})
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    pool = ask(s, title="Pool", blocks_my_task=False).json()["task_id"]
    assigned = ask(
        s,
        title="Assigned",
        blocks_my_task=False,
        assignee_member_id=member_id(people, cid, "member"),
    ).json()["task_id"]
    # even a human task that somehow became ready for an agent is skipped by the dispatcher
    with app.state.session_factory() as db:
        sneaky = db.get(Task, pool)
        sneaky.assignee_agent_id, sneaky.status = nova["id"], "ready"
        db.commit()
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id.in_([pool, assigned]))))
        assert runs == []
        assert db.get(Task, pool).status == "ready"


def test_the_unowned_work_watchdog_leaves_the_pool_alone(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s, blocks_my_task=False).json()["task_id"]
    from app.core.time import utcnow
    from app.services import autonomy_watchdog

    with app.state.session_factory() as db:
        db.get(Task, hid).updated_at = utcnow() - timedelta(days=3)
        db.commit()
        assert autonomy_watchdog.detect_unowned(db, cid, utcnow()) == 0
        db.commit()
        assert db.get(Task, hid).assignee_agent_id is None


# ------------------------------------------------------------------ deadlines


def test_an_overdue_task_reminds_the_owners_once_and_is_never_cancelled(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    hid = ask(s, due_in_hours=1, blocks_my_task=False).json()["task_id"]
    from app.core.time import utcnow
    from app.services import human_work

    now = utcnow()
    with app.state.session_factory() as db:
        assert human_work.sweep_reminders(db, now + timedelta(hours=24)) == 0  # due + 23 h
        assert human_work.sweep_reminders(db, now + timedelta(hours=26)) == 1  # due + 25 h
        assert human_work.sweep_reminders(db, now + timedelta(hours=30)) == 0  # once only
        assert human_work.sweep_reminders(db, now + timedelta(days=30)) == 0
        assert db.get(Task, hid).status == "backlog"  # a person's job, not a timeout's
    items = owner.get("/api/notifications").json()["items"]
    assert (
        sum(1 for n in items if n["kind"] == "human_work" and n["title"].startswith("Overdue")) == 1
    )


# ------------------------------------------------------------------ the work graph


def test_a_graph_run_that_proposes_a_result_waits_too(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = session(app, owner, cid, specialist(owner, cid)["id"], anon_client)
    ask(s)
    from app.services import tasks as task_service

    with app.state.session_factory() as db:
        run = db.get(TaskRun, s.run_id)
        task = task_service.apply_proposed_completion(
            db, run, {"summary": "I think I am done", "output_refs": []}, self_checked=True
        )
        assert task is not None
        assert task.status == "backlog" and task.blocked_reason == "waiting_for_human"
        assert task.result_summary is None
        deps = list(db.scalars(select(TaskDependency).where(TaskDependency.task_id == task.id)))
        assert len(deps) == 1
