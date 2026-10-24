"""M7 humans in the org: tasks, reviews and approvals assigned to people, never dispatched to a
runtime, notifications; agents can never touch accounts."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.errors import GuardrailDenied
from app.core.principal import Principal, PrincipalType
from app.guardrails import engine as guard_engine
from app.guardrails import registry, rules
from app.models.enums import GuardrailScope
from app.models.guardrails import GuardrailEvent, GuardrailRule
from app.models.policy import CompanyPolicy
from app.models.run import CompanyEvent, TaskRun
from app.models.user import User
from app.services import agent_auth, alerts, approvals
from tests.accounts_helpers import (
    PASSWORD,
    browser,
    create_company,
    do_setup,
    invite,
    join,
    member_ref,
)
from tests.helpers import api, get_task, new_agent, new_task, settle
from tests.test_agent_api import start_run


@pytest.fixture
def world(app):
    owner = do_setup(app)
    cid = create_company(owner)["id"]
    people = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        people[role] = join(app, invite(owner, cid, f"{role}@example.com", role), role)
    people["member2"] = join(app, invite(owner, cid, "member2@example.com", "member"), "member2")
    return people, cid


def refs(people, cid) -> dict[str, str]:
    return {k: member_ref(b, cid) for k, b in people.items()}


def notifications_of(b, **params) -> dict:
    r = b.get("/api/notifications", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def agent_session(app, owner, cid, agent_id, anon):
    s = start_run(app, owner, cid, agent_id)
    s.client = anon  # a process holding only its run token
    return s


# ------------------------------------------------------------------ tasks for people


def test_a_task_assigned_to_a_person_is_ready_and_never_dispatched(app, world, scheduler, worker):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    new_agent(owner, cid, name="Nova", script={})
    ref = refs(people, cid)["member"]
    task = new_task(owner, cid, title="Call the supplier", assignee_ref=ref)
    assert task["assignee_ref"] == ref and task["assignee_agent_id"] is None
    assert task["status"] == "ready"  # dependencies done, assignee active: ready for the person
    report = scheduler.tick()
    settle(scheduler, worker)
    assert report.dispatch.queued_run_ids == []
    assert get_task(owner, cid, task["id"])["status"] == "ready"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_id == task["id"])) == 0
    # the person hears about it instead
    n = notifications_of(bob)
    assert n["unread_count"] == 1
    assert (n["items"][0]["kind"], n["items"][0]["related_type"]) == ("task_assigned", "task")
    assert (
        n["items"][0]["related_id"] == task["id"] and "Call the supplier" in n["items"][0]["title"]
    )


def test_tasks_can_be_listed_by_assignee_ref(app, world):
    people, cid = world
    owner = people["owner"]
    r = refs(people, cid)
    agent = new_agent(owner, cid, name="Nova")
    mine = new_task(owner, cid, title="mine", assignee_ref=r["member"])
    other = new_task(owner, cid, title="theirs", assignee_ref=r["member2"])
    bots = new_task(owner, cid, title="bots", assignee_ref=f"agent:{agent['id']}")
    assert bots["assignee_agent_id"] == agent["id"]
    q = owner.get(api(cid, "/tasks"), params={"assignee_ref": r["member"]}).json()["items"]
    assert [t["id"] for t in q] == [mine["id"]]
    q = owner.get(api(cid, "/tasks"), params={"assignee_ref": f"agent:{agent['id']}"}).json()[
        "items"
    ]
    assert [t["id"] for t in q] == [bots["id"]]
    assert owner.get(api(cid, "/tasks"), params={"assignee_ref": "junk"}).status_code == 422
    assert other["assignee_ref"] == r["member2"]


def test_assignment_validation(app, world):
    people, cid = world
    owner = people["owner"]
    r = refs(people, cid)
    agent = new_agent(owner, cid, name="Nova")
    url = api(cid, "/tasks")
    # viewers hold no work; strangers do not exist
    assert owner.post(url, json={"title": "x", "assignee_ref": r["viewer"]}).status_code == 422
    assert (
        owner.post(url, json={"title": "x", "assignee_ref": "member:" + "3" * 36}).status_code
        == 404
    )
    # one assignee only
    both = {"title": "x", "assignee_agent_id": agent["id"], "assignee_ref": r["member"]}
    assert owner.post(url, json=both).status_code == 422
    clash = {"title": "x", "assignee_agent_id": agent["id"], "assignee_ref": "agent:" + "4" * 36}
    assert owner.post(url, json=clash).status_code == 422
    # nobody reviews their own work
    same = {"title": "x", "assignee_ref": r["member"], "reviewer_ref": r["member"]}
    assert owner.post(url, json=same).status_code == 422
    # reassigning between a person and an agent clears the other side
    t = new_task(owner, cid, title="moves", assignee_ref=r["member"])
    to_agent = owner.patch(f"{url}/{t['id']}", json={"assignee_ref": f"agent:{agent['id']}"}).json()
    assert (to_agent["assignee_agent_id"], to_agent["assignee_ref"]) == (
        agent["id"],
        f"agent:{agent['id']}",
    )
    back = owner.patch(f"{url}/{t['id']}", json={"assignee_ref": r["member2"]}).json()
    assert back["assignee_agent_id"] is None and back["assignee_ref"] == r["member2"]
    cleared = owner.patch(f"{url}/{t['id']}", json={"assignee_ref": None}).json()
    assert cleared["assignee_ref"] is None and cleared["status"] == "backlog"


def test_dependencies_gate_a_persons_task(app, world):
    people, cid = world
    owner, first, second = people["owner"], people["member"], people["member2"]
    r = refs(people, cid)
    a = new_task(owner, cid, title="step 1", assignee_ref=r["member"])
    b = new_task(owner, cid, title="step 2", assignee_ref=r["member2"], depends_on=[a["id"]])
    assert b["status"] == "backlog"
    assert second.post(api(cid, f"/tasks/{b['id']}/complete"), json={}).status_code == 409
    assert (
        first.post(api(cid, f"/tasks/{a['id']}/complete"), json={"comment": "ok"}).status_code
        == 200
    )
    assert get_task(owner, cid, b["id"])["status"] == "ready"


# ------------------------------------------------------------------ doing the work


def test_the_assignee_completes_with_a_comment_and_artifacts(app, world):
    people, cid = world
    owner, bob = people["owner"], people["member"]
    task = new_task(owner, cid, title="Write the memo", assignee_ref=refs(people, cid)["member"])
    files = {"file": ("memo.txt", b"the memo", "text/plain")}
    up = bob.post(
        api(cid, "/artifacts"),
        data={"title": "memo", "kind": "other", "task_id": task["id"]},
        files=files,
    )
    assert up.status_code == 201, up.text
    done = bob.post(
        api(cid, f"/tasks/{task['id']}/complete"),
        json={"comment": "Memo attached", "artifact_ids": [up.json()["id"]]},
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "done" and body["result_summary"] == "Memo attached"
    assert body["output_refs"] == [up.json()["id"]] and body["completed_at"]
    assert bob.post(api(cid, f"/tasks/{task['id']}/complete"), json={}).status_code == 409  # once
    # the activity trail shows who did it
    acts = owner.get(api(cid, "/activity")).json()["items"]
    assert any(a["action"] == "task.done" and a["actor_label"] == "Member" for a in acts)


def test_only_the_assignee_or_an_admin_completes_and_artifacts_are_task_bound(app, world):
    people, cid = world
    owner = people["owner"]
    r = refs(people, cid)
    t = new_task(owner, cid, title="mine", assignee_ref=r["member"])
    url = api(cid, f"/tasks/{t['id']}/complete")
    assert people["member2"].post(url, json={}).status_code == 403
    assert people["viewer"].post(url, json={}).status_code == 403
    files = {"file": ("x.txt", b"x", "text/plain")}
    mine = {"title": "x", "kind": "other", "task_id": t["id"]}
    assert people["member2"].post(api(cid, "/artifacts"), data=mine, files=files).status_code == 403
    assert people["member"].post(
        api(cid, "/artifacts"), data={**mine, "task_id": None}, files=files
    ).status_code in (403, 422)
    # an artifact must exist in this company
    bad = people["member"].post(url, json={"artifact_ids": ["5" * 36]})
    assert bad.status_code == 404
    # an owner/admin can finish it on the person's behalf
    assert people["admin"].post(url, json={"comment": "done for them"}).status_code == 200


def test_completing_is_for_people_tasks_only(app, world):
    people, cid = world
    owner = people["owner"]
    agent = new_agent(owner, cid, name="Nova")
    t = new_task(owner, cid, title="bot work", assignee_agent_id=agent["id"])
    r = owner.post(api(cid, f"/tasks/{t['id']}/complete"), json={})
    assert r.status_code == 409  # agents complete their own tasks through the agent API
    u = new_task(owner, cid, title="nobody")
    assert owner.post(api(cid, f"/tasks/{u['id']}/complete"), json={}).status_code == 409


def test_a_person_can_block_and_unblock_their_own_task_only(app, world):
    people, cid = world
    owner, bob, eve = people["owner"], people["member"], people["member2"]
    t = new_task(owner, cid, title="stuck", assignee_ref=refs(people, cid)["member"])
    base = api(cid, f"/tasks/{t['id']}")
    assert eve.post(f"{base}/block", json={"reason": "no"}).status_code == 403
    r = bob.post(f"{base}/block", json={"reason": "Waiting for the supplier"})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert eve.post(f"{base}/unblock").status_code == 403
    assert bob.post(f"{base}/unblock").json()["status"] == "ready"
    # members still cannot edit, cancel or retry
    for path in ("cancel", "retry"):
        assert bob.post(f"{base}/{path}", json={}).status_code == 403
    assert bob.patch(base, json={"title": "mine now"}).status_code == 403


def test_work_a_person_does_can_be_reviewed(app, world):
    people, cid = world
    owner, bob, rev, other = people["owner"], people["member"], people["member2"], people["admin"]
    r = refs(people, cid)
    t = new_task(
        owner,
        cid,
        title="Draft the offer",
        acceptance_criteria="Names the price",
        assignee_ref=r["member"],
        reviewer_ref=r["member2"],
    )
    assert t["reviewer_ref"] == r["member2"]
    assert (
        bob.post(api(cid, f"/tasks/{t['id']}/complete"), json={"comment": "v1"}).json()["status"]
        == "in_review"
    )
    n = notifications_of(rev)
    assert [i["kind"] for i in n["items"]] == ["review_requested"] and n["items"][0][
        "related_id"
    ] == t["id"]
    review = api(cid, f"/tasks/{t['id']}/review")
    assert bob.post(review, json={"decision": "accept"}).status_code == 403  # not your own work
    assert people["viewer"].post(review, json={"decision": "accept"}).status_code == 403
    changes = rev.post(review, json={"decision": "request_revision", "note": "Add the price"})
    assert changes.status_code == 200
    # the revision goes straight back to the same person's queue
    assert get_task(owner, cid, t["id"])["status"] == "ready"
    assert get_task(owner, cid, t["id"])["review_note"] == "Add the price"
    assert (
        bob.post(api(cid, f"/tasks/{t['id']}/complete"), json={"comment": "v2"}).json()["status"]
        == "in_review"
    )
    assert rev.post(review, json={"decision": "accept"}).json()["status"] == "done"
    del other


def test_an_unrelated_member_cannot_review(app, world):
    people, cid = world
    owner = people["owner"]
    r = refs(people, cid)
    t = new_task(
        owner,
        cid,
        title="x",
        acceptance_criteria="c",
        assignee_ref=r["member"],
        reviewer_ref=r["admin"],
    )
    people["member"].post(api(cid, f"/tasks/{t['id']}/complete"), json={})
    review = api(cid, f"/tasks/{t['id']}/review")
    assert people["member2"].post(review, json={"decision": "accept"}).status_code == 403
    assert people["admin"].post(review, json={"decision": "accept"}).status_code == 200


# ------------------------------------------------------------------ approvals for people


def human_approval(app, cid) -> str:
    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.username == "alice"))
        principal = Principal(PrincipalType.HUMAN, user.id, "Alice")
        a = approvals.create_approval_record(
            db, company_id=cid, principal=principal, kind="external_purchase", title="Buy a domain"
        )
        db.commit()
        assert a.approver == "human"
        return a.id


def test_approvals_can_be_routed_to_a_person(app, world):
    people, cid = world
    owner, admin, bob, eve = people["owner"], people["admin"], people["member"], people["member2"]
    r = refs(people, cid)
    aid = human_approval(app, cid)
    # the owners/admins are asked first
    assert [i["kind"] for i in notifications_of(admin)["items"]] == ["approval_needed"]
    assert notifications_of(bob)["items"] == []
    approve = api(cid, f"/approvals/{aid}/approve")
    assert bob.post(approve, json={}).status_code == 403  # not routed to them
    # only owners/admins route; the person must be assignable
    route = api(cid, f"/approvals/{aid}/route")
    assert bob.post(route, json={"approver_ref": r["member"]}).status_code == 403
    assert admin.post(route, json={"approver_ref": r["viewer"]}).status_code == 422
    assert admin.post(route, json={"approver_ref": f"agent:{'6' * 36}"}).status_code == 422
    shown = admin.post(route, json={"approver_ref": r["member"]})
    assert shown.status_code == 200 and shown.json()["approver_ref"] == r["member"]
    assert [i["kind"] for i in notifications_of(bob)["items"]] == ["approval_needed"]
    assert eve.post(approve, json={}).status_code == 403
    done = bob.post(approve, json={"note": "fine"})
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert done.json()["decided_by_id"]
    assert owner.get(api(cid, f"/approvals/{aid}")).json()["approver_ref"] == r["member"]
    assert admin.post(route, json={"approver_ref": None}).status_code == 409  # decided


def test_owners_can_still_decide_a_routed_approval(app, world):
    people, cid = world
    aid = human_approval(app, cid)
    people["admin"].post(
        api(cid, f"/approvals/{aid}/route"), json={"approver_ref": refs(people, cid)["member"]}
    )
    r = people["owner"].post(api(cid, f"/approvals/{aid}/reject"), json={})
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_routing_is_for_human_approvals_in_this_company(app, world):
    people, cid = world
    owner = people["owner"]
    other = create_company(owner, "Other Co")
    aid = human_approval(app, cid)
    assert (
        owner.post(
            api(other["id"], f"/approvals/{aid}/route"), json={"approver_ref": None}
        ).status_code
        == 404
    )
    foreign = member_ref(people["member"], cid)
    r = owner.post(api(other["id"], "/approvals/" + aid + "/route"), json={"approver_ref": foreign})
    assert r.status_code == 404
    with app.state.session_factory() as db:  # an agent-decided approval cannot be routed
        a = approvals.create_approval_record(
            db,
            company_id=cid,
            principal=Principal(PrincipalType.HUMAN, "u", "x"),
            kind="other",
            title="agent call",
            approver=approvals.Approver.FOUNDER,
        )
        db.commit()
        founder_aid = a.id
    r = owner.post(
        api(cid, f"/approvals/{founder_aid}/route"),
        json={"approver_ref": refs(people, cid)["member"]},
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ notifications


def test_notification_list_read_state_and_privacy(app, world):
    people, cid = world
    owner, bob, eve = people["owner"], people["member"], people["member2"]
    r = refs(people, cid)
    t1 = new_task(owner, cid, title="one", assignee_ref=r["member"])
    new_task(owner, cid, title="two", assignee_ref=r["member"])
    new_task(owner, cid, title="three", assignee_ref=r["member2"])
    got = notifications_of(bob)
    assert got["unread_count"] == 2 and len(got["items"]) == 2
    assert got["items"][0]["title"].endswith("two")  # newest first
    first = next(i for i in got["items"] if i["related_id"] == t1["id"])
    assert first["read_at"] is None and first["company_id"] == cid
    # reading one
    res = bob.post("/api/notifications/read", json={"ids": [first["id"]]}).json()
    assert res == {"marked": 1, "unread_count": 1}
    assert notifications_of(bob, unread=True)["items"][0]["title"].endswith("two")
    # other people's ids are ignored, never marked
    eve_ids = [i["id"] for i in notifications_of(eve)["items"]]
    assert bob.post("/api/notifications/read", json={"ids": eve_ids}).json()["marked"] == 0
    assert notifications_of(eve)["unread_count"] == 1
    # everything
    assert bob.post("/api/notifications/read", json={"all": True}).json() == {
        "marked": 1,
        "unread_count": 0,
    }
    assert notifications_of(bob)["unread_count"] == 0 and len(notifications_of(bob)["items"]) == 2
    # validation and paging
    assert bob.post("/api/notifications/read", json={}).status_code == 422
    assert bob.post("/api/notifications/read", json={"ids": ["x"], "all": True}).status_code == 422
    page = notifications_of(bob, limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert len(notifications_of(bob, limit=1, cursor=page["next_cursor"])["items"]) == 1


def test_notifications_need_a_signed_in_person(app, world, client):
    anon = browser(app)
    assert anon.get("/api/notifications").status_code == 401


def test_a_notification_event_reaches_the_company_stream_without_its_content(app, world):
    people, cid = world
    new_task(people["owner"], cid, title="Secret plan", assignee_ref=refs(people, cid)["member"])
    with app.state.session_factory() as db:
        rows = list(
            db.scalars(
                select(CompanyEvent).where(
                    CompanyEvent.company_id == cid, CompanyEvent.type == "notification.created"
                )
            )
        )
    assert len(rows) == 1
    assert set(rows[0].payload) == {"notification_id", "user_id", "kind"}
    assert "Secret plan" not in str(rows[0].payload)


def test_alerts_notify_owners_and_admins_only(app, world):
    people, cid = world
    with app.state.session_factory() as db:
        alerts.raise_alert(db, cid, kind="disk", title="Disk is nearly full", severity="warning")
        db.commit()
    for role in ("owner", "admin"):
        items = notifications_of(people[role])["items"]
        assert [i["kind"] for i in items] == ["alert"], role
    for role in ("member", "viewer"):
        assert notifications_of(people[role])["items"] == [], role


def test_the_cofounders_reply_notifies_the_author_of_the_note(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    note = owner.post(api(cid, "/notes"), json={"body": "Let's sell lemonade"}).json()
    s = agent_session(app, owner, cid, founder["id"], anon_client)
    r = s.post(f"/notes/{note['id']}/replies", json={"body": "On it: I created a goal."})
    assert r.status_code == 201, r.text
    items = notifications_of(owner)["items"]
    assert [i["kind"] for i in items] == ["note_replied"]
    assert items[0]["related_type"] == "note" and items[0]["related_id"] == note["id"]
    assert notifications_of(people["admin"])["items"] == []  # only the author


# ------------------------------------------------------------------ the AI Co-founder and people


def test_the_cofounder_sees_people_as_colleagues_and_can_assign_work(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    r = refs(people, cid)
    new_task(owner, cid, title="busy", assignee_ref=r["member"])
    owner.patch(api(cid, f"/members/{r['member'].split(':')[1]}"), json={"title": "Designer"})
    s = agent_session(app, owner, cid, founder["id"], anon_client)
    listed = s.get("/members").json()
    by_ref = {m["ref"]: m for m in listed}
    assert set(by_ref) == {r["owner"], r["admin"], r["member"], r["member2"]}  # not the viewer
    assert by_ref[r["member"]]["title"] == "Designer" and by_ref[r["member"]]["open_tasks"] == 1
    assert all(
        set(m) == {"ref", "name", "title", "role", "manager_ref", "open_tasks"} for m in listed
    )
    assert "example.com" not in str(listed)  # never an email
    created = s.post("/tasks", json={"title": "Review the logo", "assignee_ref": r["member2"]})
    assert created.status_code == 201, created.text
    assert (
        created.json()["assignee_ref"] == r["member2"]
        and created.json()["created_by_type"] == "agent"
    )
    assert [i["kind"] for i in notifications_of(people["member2"])["items"]] == ["task_assigned"]
    # and hands an existing task over
    t = new_task(owner, cid, title="loose")
    patched = s.patch(f"/tasks/{t['id']}", json={"assignee_ref": r["member"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["assignee_ref"] == r["member"]


def test_specialists_cannot_hand_work_to_people(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    agent = new_agent(owner, cid, name="Sam")
    r = refs(people, cid)
    s = agent_session(app, owner, cid, agent["id"], anon_client)
    assert s.post("/tasks", json={"title": "x", "assignee_ref": r["member"]}).status_code == 403
    assert s.post("/tasks", json={"title": "x", "reviewer_ref": r["member"]}).status_code == 403
    mine = new_task(owner, cid, title="loose")
    assert s.patch(f"/tasks/{mine['id']}", json={"assignee_ref": r["member"]}).status_code == 403


# ------------------------------------------------------------------ agents never manage accounts


ACCOUNT_ROUTES = [
    ("GET", "/api/auth/tokens", None),
    ("POST", "/api/auth/tokens", {"name": "evil", "scope": "admin"}),
    ("GET", "/api/auth/sessions", None),
    ("DELETE", "/api/auth/sessions", None),
    ("POST", "/api/auth/password", {"current_password": "x" * 12, "new_password": "y" * 12}),
    ("POST", "/api/auth/2fa/enroll", {}),
    ("POST", "/api/auth/logout", None),
]


def test_agents_can_never_create_users_invitations_tokens_or_sessions(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    member_id = refs(people, cid)["member"].split(":")[1]
    inv = invite(owner, cid, "pending@example.com", "viewer")
    for agent_id in (founder["id"], new_agent(owner, cid, name="Sam")["id"]):
        s = agent_session(app, owner, cid, agent_id, anon_client)
        hdr = s.headers
        calls = [
            *ACCOUNT_ROUTES[:6],
            ("POST", api(cid, "/invitations"), {"email": "e@example.com", "role": "admin"}),
            ("DELETE", api(cid, f"/invitations/{inv['id']}"), None),
            ("PATCH", api(cid, f"/members/{member_id}"), {"role": "admin"}),
            ("DELETE", api(cid, f"/members/{member_id}"), None),
            (
                "POST",
                f"/api/invitations/{inv['token']}/accept",
                {"username": "evil", "display_name": "E", "password": PASSWORD},
            ),
            ("POST", "/api/auth/login", {"username": "alice", "password": PASSWORD}),
            (
                "POST",
                "/api/auth/setup",
                {"username": "x1x", "display_name": "x", "password": PASSWORD},
            ),
        ]
        for method, path, body in calls:
            r = anon_client.request(method, path, json=body, headers=hdr)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            assert r.json()["error"]["code"] in ("guardrail_denied", "forbidden"), (method, path)
        assert anon_client.get(api(cid, "/members"), headers=hdr).status_code == 403
        # the agent's run token is not a session: it cannot be exchanged for one
        assert anon_client.get("/api/auth/session", headers=hdr).json()["state"] != "authenticated"
    # nothing was created
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(User.id))) == 5  # alice + the four people invited
        denied = {
            e.action
            for e in db.scalars(select(GuardrailEvent).where(GuardrailEvent.decision == "deny"))
        }
    assert {
        "token.create",
        "invitation.create",
        "member.update",
        "member.remove",
        "session.revoke",
    } <= denied
    assert owner.get("/api/auth/tokens").json()["items"] == []
    listed = owner.get(api(cid, "/invitations")).json()["items"]
    assert [i["id"] for i in listed if i["status"] == "pending"] == [inv["id"]]  # nothing new


def test_no_rule_can_allow_a_human_only_action(app, world):
    people, cid = world
    owner = people["owner"]
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    human_only = sorted(registry.HUMAN_ONLY)
    assert {"user.create", "invitation.create", "token.create", "session.create"} <= set(human_only)
    for name in human_only:
        spec = registry.get_action(name)
        assert spec.human_only and spec.tier == "identity"
        # a rule cannot even be written for it ...
        r = owner.post(
            api(cid, "/guardrails/rules"),
            json={"tier": "identity", "action": name, "scope_type": "all", "decision": "allow"},
        )
        assert r.status_code == 422, name
    # ... and a rule that got into the table anyway (or a tier-wide allow) changes nothing
    with app.state.session_factory() as db:
        for scope_type, scope_value in (
            (GuardrailScope.ALL, None),
            (GuardrailScope.AGENT, founder["id"]),
        ):
            db.add(
                GuardrailRule(
                    company_id=cid,
                    tier="identity",
                    action=None,
                    scope_type=scope_type,
                    scope_value=scope_value,
                    decision="allow",
                    conditions={},
                )
            )
        for name in human_only:
            db.add(
                GuardrailRule(
                    company_id=cid,
                    tier="identity",
                    action=name,
                    scope_type="all",
                    scope_value=None,
                    decision="allow",
                    conditions={},
                )
            )
        db.commit()
        agent = db.get(__import__("app.models.agent", fromlist=["Agent"]).Agent, founder["id"])
        limits = db.get(CompanyPolicy, cid) or CompanyPolicy(company_id=cid)
        for name in human_only:
            res = rules.resolve(db, agent, registry.get_action(name), limits, rules.Facts())
            assert res.decision == "deny", name
    # the engine itself denies (and logs) it for a live run
    anon = browser(app)
    s = agent_session(app, owner, cid, founder["id"], anon)
    with app.state.session_factory() as db:
        rc = agent_auth.authenticate(db, app.state.keys, s.token)
        with pytest.raises(GuardrailDenied):
            guard_engine.check(db, rc.ctx, "token.create", {}, run=rc.run)


def _gates(dependant) -> set[str]:
    found = set()
    for d in dependant.dependencies:
        found.add(getattr(d.call, "__qualname__", ""))
        found |= _gates(d)
    return found


def test_every_account_and_team_route_is_closed_to_agents(app):
    """Inventory: every route that creates or changes accounts, members, invitations, tokens or
    sessions sits behind a human-only gate (so an agent's run token gets 403, never a result)."""
    from app.api.routes import auth, team

    gates = ("human_only", "account_user", "reject_agent_credentials")
    unguarded_by_design = {
        ("GET", "/auth/local"),  # the owner-token bootstrap link
        ("GET", "/auth/session"),  # public: never authenticates an agent as a user
        ("POST", "/auth/logout"),  # ends the caller's own cookie session; a run token has none
        ("GET", "/companies/{company_id}/members"),  # the service answers 403 to agents
        ("GET", "/companies/{company_id}/members/{member_id}"),
        ("GET", "/companies/{company_id}/invitations"),  # the service answers 403 (admins only)
    }
    checked = 0
    for router in (auth.router, team.router):
        for route in router.routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                full = (method, route.path)
                if full in unguarded_by_design:
                    continue
                names = " ".join(_gates(route.dependant))
                assert any(g in names for g in gates), (method, route.path, names)
                checked += 1
    assert checked >= 14
