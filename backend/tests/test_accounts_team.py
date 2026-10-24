"""M7 team: the role matrix on every mutating route, invitations, members as org nodes, tenancy."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.models.accounts import Invitation
from tests.accounts_helpers import (
    PASSWORD,
    browser,
    create_company,
    do_setup,
    invite,
    join,
    login,
    member_ref,
)
from tests.helpers import api, get_task, new_agent, new_task


def staff(app, owner, cid) -> dict:
    """One browser per role, all in company ``cid``."""
    out = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        out[role] = join(app, invite(owner, cid, f"{role}@example.com", role), role)
    return out


@pytest.fixture
def world(app):
    owner = do_setup(app)
    company = create_company(owner)
    return owner, company["id"]


# ------------------------------------------------------------------ role matrix

OWNER_ADMIN = {"owner", "admin"}
OWNER_ONLY = {"owner"}
ALL = {"owner", "admin", "member", "viewer"}


def matrix(ids: dict) -> list[tuple]:
    t, t2, a, d, g, p, s = (
        ids[k] for k in ("task", "task2", "agent", "dept", "goal", "proj", "sched")
    )
    return [
        # label, method, path (under /companies/{cid}), json body, allowed roles
        ("create task", "POST", "/tasks", {"title": "x"}, OWNER_ADMIN),
        ("edit task", "PATCH", f"/tasks/{t}", {"title": "y"}, OWNER_ADMIN),
        ("cancel task", "POST", f"/tasks/{t2}/cancel", {}, OWNER_ADMIN),
        ("block task", "POST", f"/tasks/{t}/block", {"reason": "r"}, OWNER_ADMIN),
        ("unblock task", "POST", f"/tasks/{t}/unblock", None, OWNER_ADMIN),
        ("retry task", "POST", f"/tasks/{t}/retry", None, OWNER_ADMIN),
        ("review task", "POST", f"/tasks/{t}/review", {"decision": "accept"}, OWNER_ADMIN),
        ("complete task", "POST", f"/tasks/{t}/complete", {}, OWNER_ADMIN),
        (
            "add dependency",
            "POST",
            f"/tasks/{t}/dependencies",
            {"depends_on_task_id": t2},
            OWNER_ADMIN,
        ),
        ("create agent", "POST", "/agents", {"name": "Zed", "role_title": "Dev"}, OWNER_ADMIN),
        ("edit agent", "PATCH", f"/agents/{a}", {"name": "Nova2"}, OWNER_ADMIN),
        ("agent lifecycle", "POST", f"/agents/{a}/pause", None, OWNER_ADMIN),
        ("create department", "POST", "/departments", {"name": "Ops"}, OWNER_ADMIN),
        ("edit department", "PATCH", f"/departments/{d}", {"name": "Ops2"}, OWNER_ADMIN),
        ("create goal", "POST", "/goals", {"title": "g"}, OWNER_ADMIN),
        ("edit goal", "PATCH", f"/goals/{g}", {"title": "g2"}, OWNER_ADMIN),
        ("create project", "POST", "/projects", {"name": "pr"}, OWNER_ADMIN),
        ("edit project", "PATCH", f"/projects/{p}", {"name": "pr2"}, OWNER_ADMIN),
        (
            "create schedule",
            "POST",
            "/schedules",
            {"name": "s", "agent_id": a, "interval_seconds": 3600},
            OWNER_ADMIN,
        ),
        ("edit schedule", "PATCH", f"/schedules/{s}", {"enabled": False}, OWNER_ADMIN),
        ("write a note", "POST", "/notes", {"body": "hello"}, OWNER_ADMIN),
        ("add memory", "POST", "/memory", {"kind": "fact", "body": "b"}, OWNER_ADMIN),
        ("edit policy", "PATCH", "/policy", {"max_headcount": 12}, OWNER_ADMIN),
        ("edit company", "PATCH", "", {"mission": "m"}, OWNER_ADMIN),
        ("pause company", "POST", "/pause", None, OWNER_ADMIN),
        ("resume company", "POST", "/resume", None, OWNER_ADMIN),
        (
            "create guardrail rule",
            "POST",
            "/guardrails/rules",
            {"tier": "spend", "scope_type": "all", "decision": "human_approval"},
            OWNER_ADMIN,
        ),
        ("store credential", "POST", "/credentials", {"name": "k", "value": "v" * 12}, OWNER_ADMIN),
        (
            "add model provider",
            "POST",
            "/model-providers",
            {"name": "p", "kind": "ollama", "default_model": "m"},
            OWNER_ADMIN,
        ),
        (
            "record expense",
            "POST",
            "/finance/expenses",
            {"vendor": "v", "amount_micros": 5, "incurred_on": "2026-10-01"},
            OWNER_ADMIN,
        ),
        ("wake the co-founder", "POST", "/cofounder/wake", None, OWNER_ADMIN),
        ("seed skills", "POST", "/skills/seed", None, OWNER_ADMIN),
        (
            "invite",
            "POST",
            "/invitations",
            {"email": "n@example.com", "role": "viewer"},
            OWNER_ADMIN,
        ),
        ("revoke invitation", "DELETE", f"/invitations/{ids['invitation']}", None, OWNER_ADMIN),
        ("edit member", "PATCH", f"/members/{ids['victim']}", {"title": "T"}, OWNER_ADMIN),
        ("remove member", "DELETE", f"/members/{ids['victim']}", None, OWNER_ADMIN),
    ]


def test_role_matrix_on_every_mutating_route(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    agent = new_agent(owner, cid, name="Nova")
    ids = {
        "task": new_task(owner, cid, title="t")["id"],
        "task2": new_task(owner, cid, title="t2")["id"],
        "agent": agent["id"],
        "dept": owner.post(api(cid, "/departments"), json={"name": "D"}).json()["id"],
        "goal": owner.post(api(cid, "/goals"), json={"title": "G"}).json()["id"],
        "proj": owner.post(api(cid, "/projects"), json={"name": "P"}).json()["id"],
        "sched": owner.post(
            api(cid, "/schedules"),
            json={"name": "S", "agent_id": agent["id"], "interval_seconds": 3600},
        ).json()["id"],
        "invitation": invite(owner, cid, "pending@example.com", "viewer")["id"],
        "victim": join(app, invite(owner, cid, "victim@example.com", "viewer"), "victim")
        .get("/api/me")
        .json()["memberships"][0]["member_id"],
    }
    # least privileged first: refusals must not depend on (or change) state the allowed roles touch
    for label, method, path, body, allowed in matrix(ids):
        for role in ("viewer", "member", "admin", "owner"):
            r = people[role].request(method, api(cid, path), json=body)
            if role in allowed:
                assert r.status_code not in (401, 403), (label, role, r.status_code, r.text)
            else:
                assert r.status_code == 403, (label, role, r.status_code, r.text)
                assert r.json()["error"]["code"] == "forbidden", (label, role)


def test_artifact_upload_is_admin_only_unless_it_is_your_own_task(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    files = {"file": ("a.txt", b"hello", "text/plain")}
    for role in ("viewer", "member"):
        r = people[role].post(
            api(cid, "/artifacts"), data={"title": "a", "kind": "other"}, files=files
        )
        assert r.status_code == 403, role
    r = people["admin"].post(
        api(cid, "/artifacts"), data={"title": "a", "kind": "other"}, files=files
    )
    assert r.status_code == 201


def test_everybody_in_the_company_can_read(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    for role, b in people.items():
        for path in ("/tasks", "/agents", "/members", "/overview", "/goals", "/approvals"):
            assert b.get(api(cid, path)).status_code == 200, (role, path)


def test_only_the_installation_owner_can_use_the_emergency_stop(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    other = login(app, "admin")
    other_company = create_company(other, "Other Co")  # the admin owns a second company
    assert other_company["id"]
    for role in ("viewer", "member", "admin"):
        assert people[role].post("/api/system/emergency-stop", json={}).status_code == 403, role
    assert people["owner"].post("/api/system/emergency-stop", json={}).status_code == 200
    assert people["owner"].post("/api/system/resume", json={}).status_code == 200


# ------------------------------------------------------------------ invitations


def test_invite_returns_the_link_once_and_stores_only_a_hash(app, world):
    owner, cid = world
    created = invite(owner, cid, "Bob@Example.com", "member", title="Designer")
    assert created["delivery"] == "link" and created["status"] == "pending"
    assert created["email"] == "bob@example.com" and created["title"] == "Designer"
    assert created["invite_url"] == f"/invite/{created['token']}"
    assert len(created["token"]) >= 40
    listed = owner.get(api(cid, "/invitations")).json()["items"]
    assert [i["id"] for i in listed] == [created["id"]]
    assert "token" not in listed[0] and "invite_url" not in listed[0]
    with app.state.session_factory() as db:
        row = db.scalar(select(Invitation))
        assert created["token"] not in str(row.__dict__) and len(row.token_hash) == 64
        assert row.expires_at - row.created_at == timedelta(days=7)


def test_accept_creates_the_account_membership_and_session(app, world):
    owner, cid = world
    created = invite(owner, cid, "bob@example.com", "member", title="Designer")
    bob = browser(app)
    r = bob.post(
        f"/api/invitations/{created['token']}/accept",
        json={"username": "bob", "display_name": "Bob B", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] == cid and body["role"] == "member" and body["member_id"]
    me = bob.get("/api/me").json()  # signed in by the accept
    assert me["user"]["username"] == "bob" and me["user"]["email"] == "bob@example.com"
    assert me["memberships"][0]["role"] == "member"
    members = owner.get(api(cid, "/members")).json()["items"]
    row = next(m for m in members if m["username"] == "bob")
    assert (row["role"], row["title"], row["status"]) == ("member", "Designer", "active")
    assert row["email"] == "bob@example.com"  # owners see emails
    assert owner.get(api(cid, "/invitations")).json()["items"][0]["status"] == "accepted"


def test_an_invitation_is_single_use(app, world):
    owner, cid = world
    created = invite(owner, cid, "bob@example.com")
    join(app, created, "bob")
    again = browser(app).post(
        f"/api/invitations/{created['token']}/accept",
        json={"username": "bob2", "display_name": "B", "password": PASSWORD},
    )
    assert again.status_code == 404


def test_expired_revoked_and_unknown_tokens_are_the_same_404(app, world):
    owner, cid = world
    expired = invite(owner, cid, "a@example.com")
    revoked = invite(owner, cid, "b@example.com")
    with app.state.session_factory() as db:
        row = db.get(Invitation, expired["id"])
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert owner.delete(api(cid, f"/invitations/{revoked['id']}")).status_code == 204
    body = {"username": "zed", "display_name": "Z", "password": PASSWORD}
    seen = set()
    for token in (expired["token"], revoked["token"], "x" * 43):
        r = browser(app).post(f"/api/invitations/{token}/accept", json=body)
        assert r.status_code == 404
        seen.add(r.text)
    assert len(seen) == 1  # indistinguishable: the page cannot be used to probe tokens
    statuses = {
        i["email"]: i["status"] for i in owner.get(api(cid, "/invitations")).json()["items"]
    }
    assert statuses == {"a@example.com": "expired", "b@example.com": "revoked"}


def test_invitations_are_bound_to_the_email(app, world):
    owner, cid = world
    invitee = invite(owner, cid, "carol@example.com", "member")
    # a signed-in account with a different email cannot use the link
    mallory = join(app, invite(owner, cid, "mallory@example.com", "viewer"), "mallory")
    r = mallory.post(f"/api/invitations/{invitee['token']}/accept", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_mismatch"
    # an account that already exists for that email must sign in rather than register again
    other_company = create_company(mallory, "Mallory Co")
    carol = join(app, invite(mallory, other_company["id"], "carol@example.com", "viewer"), "carol")
    anon = browser(app)
    r = anon.post(
        f"/api/invitations/{invitee['token']}/accept",
        json={"username": "carol2", "display_name": "C", "password": PASSWORD},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "account_exists"
    # signed in as carol, `{}` joins this company too, with one account in two companies
    r = carol.post(f"/api/invitations/{invitee['token']}/accept", json={})
    assert r.status_code == 200 and r.json()["company_id"] == cid
    roles = {m["company_name"]: m["role"] for m in carol.get("/api/me").json()["memberships"]}
    assert roles == {"Mallory Co": "viewer", "Acme Labs": "member"}


def test_accept_validates_the_new_account(app, world):
    owner, cid = world
    created = invite(owner, cid, "bob@example.com")
    url = f"/api/invitations/{created['token']}/accept"
    anon = browser(app)
    assert anon.post(url, json={}).status_code == 422  # needs credentials
    weak = {"username": "bob", "display_name": "B", "password": "password1234"}
    assert anon.post(url, json=weak).status_code == 422
    taken = {"username": "alice", "display_name": "B", "password": PASSWORD}
    assert anon.post(url, json=taken).status_code == 409
    ok = {"username": "bob", "display_name": "B", "password": PASSWORD}
    assert anon.post(url, json=ok).status_code == 200  # nothing above consumed the invitation


def test_reinviting_replaces_the_old_link_and_members_cannot_be_invited(app, world):
    owner, cid = world
    first = invite(owner, cid, "dan@example.com")
    second = invite(owner, cid, "dan@example.com")
    body = {"username": "dan", "display_name": "D", "password": PASSWORD}
    assert (
        browser(app).post(f"/api/invitations/{first['token']}/accept", json=body).status_code == 404
    )
    join(app, second, "dan")
    r = owner.post(api(cid, "/invitations"), json={"email": "dan@example.com", "role": "member"})
    assert r.status_code == 409  # already a member


def test_invitation_role_limits_and_validation(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    admin = people["admin"]
    for role in ("owner", "admin"):
        r = admin.post(
            api(cid, "/invitations"), json={"email": f"{role}2@example.com", "role": role}
        )
        assert r.status_code == 403, role
    assert invite(owner, cid, "newadmin@example.com", "admin")["role"] == "admin"
    assert invite(admin, cid, "newviewer@example.com", "viewer")["role"] == "viewer"
    bad = owner.post(api(cid, "/invitations"), json={"email": "nope", "role": "member"})
    assert bad.status_code == 422
    assert (
        owner.post(api(cid, "/invitations"), json={"email": "a@b.co", "role": "boss"}).status_code
        == 422
    )
    # a manager must exist in this company and be able to manage
    r = owner.post(
        api(cid, "/invitations"),
        json={"email": "m@example.com", "role": "member", "manager_ref": "member:" + "0" * 36},
    )
    assert r.status_code == 404


def test_invitations_are_company_scoped(app, world):
    owner, cid = world
    other = create_company(owner, "Second Co")
    inv = invite(owner, cid, "x@example.com")
    assert owner.get(api(other["id"], "/invitations")).json()["items"] == []
    assert owner.delete(api(other["id"], f"/invitations/{inv['id']}")).status_code == 404


def test_only_admins_see_invitations(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    for role in ("member", "viewer"):
        assert people[role].get(api(cid, "/invitations")).status_code == 403
    assert people["admin"].get(api(cid, "/invitations")).status_code == 200


# ------------------------------------------------------------------ members


def test_member_fields_and_email_visibility(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    as_member = people["member"].get(api(cid, "/members")).json()["items"]
    assert {m["username"] for m in as_member} == {"alice", "admin", "member", "viewer"}
    me = next(m for m in as_member if m["username"] == "member")
    assert me["email"] == "member@example.com"  # your own
    assert all(m["email"] is None for m in as_member if m["username"] != "member")
    as_admin = people["admin"].get(api(cid, "/members")).json()["items"]
    assert all(m["email"] for m in as_admin if m["username"] != "alice")
    one = people["admin"].get(api(cid, f"/members/{me['id']}")).json()
    assert one["role"] == "member" and one["manager_ref"] is None and one["status"] == "active"


def test_role_changes_follow_the_matrix(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    admin, member = people["admin"], people["member"]
    # an admin manages members/viewers but not admins/owners, and cannot grant admin
    assert (
        admin.patch(api(cid, f"/members/{ids['member']}"), json={"role": "viewer"}).status_code
        == 200
    )
    assert (
        admin.patch(api(cid, f"/members/{ids['member']}"), json={"role": "admin"}).status_code
        == 403
    )
    assert admin.patch(api(cid, f"/members/{ids['alice']}"), json={"title": "x"}).status_code == 403
    assert admin.delete(api(cid, f"/members/{ids['alice']}")).status_code == 403
    assert admin.delete(api(cid, f"/members/{ids['admin']}")).status_code == 403  # not yourself
    # nobody changes their own role; the last owner cannot be demoted or removed
    assert (
        owner.patch(api(cid, f"/members/{ids['alice']}"), json={"role": "admin"}).status_code == 403
    )
    assert owner.delete(api(cid, f"/members/{ids['alice']}")).status_code == 403
    assert (
        owner.patch(api(cid, f"/members/{ids['admin']}"), json={"role": "owner"}).status_code == 200
    )
    co_owner = login(app, "admin")
    assert (
        co_owner.patch(api(cid, f"/members/{ids['alice']}"), json={"role": "admin"}).status_code
        == 200
    )
    # alice is now an admin; the co-owner is the only owner left
    assert (
        owner.patch(api(cid, f"/members/{ids['admin']}"), json={"role": "admin"}).status_code == 403
    )
    assert member.get(api(cid, "/members")).status_code == 200  # still reads (now as viewer)


def test_the_last_owner_is_protected(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    owner.patch(api(cid, f"/members/{ids['admin']}"), json={"role": "owner"})
    co = login(app, "admin")
    assert (
        co.patch(api(cid, f"/members/{ids['alice']}"), json={"role": "member"}).status_code == 200
    )
    # alice (now a member) is gone from the owner set: the co-owner cannot demote the last owner
    r = co.patch(api(cid, f"/members/{ids['admin']}"), json={"role": "admin"})
    assert r.status_code == 403  # self
    assert (
        people["owner"]
        .patch(api(cid, f"/members/{ids['admin']}"), json={"role": "viewer"})
        .status_code
        == 403
    )


def test_member_title_and_manager_are_org_data(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    agent = new_agent(owner, cid, name="Nova")
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    r = owner.patch(
        api(cid, f"/members/{ids['member']}"),
        json={"title": "Head of Design", "manager_ref": f"agent:{agent['id']}"},
    )
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["manager_ref"]) == (
        "Head of Design",
        f"agent:{agent['id']}",
    )
    # humans manage humans too
    r = owner.patch(
        api(cid, f"/members/{ids['viewer']}"), json={"manager_ref": f"member:{ids['member']}"}
    )
    assert r.status_code == 200
    # a viewer cannot be a manager; unknown / foreign refs are 404, junk is 422
    assert (
        owner.patch(
            api(cid, f"/members/{ids['admin']}"), json={"manager_ref": f"member:{ids['viewer']}"}
        ).status_code
        == 422
    )
    assert (
        owner.patch(
            api(cid, f"/members/{ids['member']}"), json={"manager_ref": "member:" + "1" * 36}
        ).status_code
        == 404
    )
    assert (
        owner.patch(
            api(cid, f"/members/{ids['member']}"), json={"manager_ref": "nonsense"}
        ).status_code
        == 422
    )
    # null clears (reports to the Founder)
    assert (
        owner.patch(api(cid, f"/members/{ids['member']}"), json={"manager_ref": None}).json()[
            "manager_ref"
        ]
        is None
    )
    del people


def test_agents_can_report_to_humans_and_cycles_are_refused(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    mref = member_ref(people["member"], cid)
    a = new_agent(owner, cid, name="Nova")
    b = new_agent(owner, cid, name="Kai")
    r = owner.patch(api(cid, f"/agents/{a['id']}"), json={"manager_ref": mref})
    assert r.status_code == 200
    assert r.json()["manager_ref"] == mref and r.json()["manager_agent_id"] is None
    # the member reports to an agent that reports to them: a cycle across kinds
    mid = mref.split(":")[1]
    r = owner.patch(api(cid, f"/members/{mid}"), json={"manager_ref": f"agent:{a['id']}"})
    assert r.status_code == 409
    # creating an agent under a person, and switching back to an agent manager
    r = owner.post(
        api(cid, "/agents"), json={"name": "Zed", "role_title": "Dev", "manager_ref": mref}
    )
    assert r.status_code == 201 and r.json()["manager_ref"] == mref
    r = owner.patch(api(cid, f"/agents/{a['id']}"), json={"manager_agent_id": b["id"]})
    assert r.json()["manager_ref"] == f"agent:{b['id']}"
    listed = owner.get(api(cid, "/agents")).json()["items"]
    assert {x["name"]: x["manager_ref"] for x in listed}["Zed"] == mref


def test_removing_a_member_reassigns_work_keeps_history_and_revokes_access(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    bob = people["member"]
    agent = new_agent(owner, cid, name="Nova")
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    owner.patch(api(cid, f"/members/{ids['member']}"), json={"manager_ref": f"agent:{agent['id']}"})
    t1 = new_task(owner, cid, title="Ship the logo", assignee_ref=f"member:{ids['member']}")
    t2 = new_task(owner, cid, title="Done already", assignee_ref=f"member:{ids['member']}")
    assert (
        bob.post(api(cid, f"/tasks/{t2['id']}/complete"), json={"comment": "yes"}).status_code
        == 200
    )

    assert owner.delete(api(cid, f"/members/{ids['member']}")).status_code == 204
    moved = get_task(owner, cid, t1["id"])
    assert (
        moved["assignee_ref"] == f"agent:{agent['id']}"
        and moved["assignee_agent_id"] == agent["id"]
    )
    assert get_task(owner, cid, t2["id"])["assignee_ref"] == f"member:{ids['member']}"  # history
    assert get_task(owner, cid, t2["id"])["status"] == "done"
    # gone from the list but kept as history
    assert ids["member"] not in {m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    gone = owner.get(api(cid, "/members"), params={"include_removed": True}).json()["items"]
    assert next(m for m in gone if m["id"] == ids["member"])["status"] == "removed"
    # their access ended: no company, no sessions, no credentials
    assert bob.get(api(cid, "/tasks")).status_code in (401, 404)
    assert bob.get("/api/me").status_code == 401  # last membership: sessions revoked
    assert (
        browser(app)
        .post("/api/auth/login", json={"username": "member", "password": PASSWORD})
        .status_code
        == 200
    )  # the account exists, but it belongs to no company:
    again = login(app, "member")
    assert again.get("/api/me").json()["memberships"] == []
    assert again.get(api(cid, "/tasks")).status_code == 404


def test_removed_members_tasks_go_to_the_ai_cofounder_when_there_is_no_manager(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    t = new_task(owner, cid, title="Orphan", assignee_ref=f"member:{ids['member']}")
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    owner.delete(api(cid, f"/members/{ids['member']}"))
    assert get_task(owner, cid, t["id"])["assignee_agent_id"] == founder["id"]
    del people


def test_removing_a_member_keeps_their_other_company(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    dana = people["member"]
    other = create_company(dana, "Dana Co")  # dana owns a second company
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    owner.delete(api(cid, f"/members/{ids['member']}"))
    assert dana.get("/api/me").status_code == 200  # still has a company: the session survives
    assert dana.get(api(other["id"], "/tasks")).status_code == 200
    assert dana.get(api(cid, "/tasks")).status_code == 404


def test_a_removed_member_can_be_invited_back(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    ids = {m["username"]: m["id"] for m in owner.get(api(cid, "/members")).json()["items"]}
    owner.delete(api(cid, f"/members/{ids['viewer']}"))
    back = invite(owner, cid, "viewer@example.com", "member")
    login(app, "viewer").post(f"/api/invitations/{back['token']}/accept", json={})
    members = owner.get(api(cid, "/members")).json()["items"]
    row = next(m for m in members if m["username"] == "viewer")
    assert row["id"] == ids["viewer"] and row["role"] == "member" and row["status"] == "active"
    del people


# ------------------------------------------------------------------ tenancy


def test_a_person_in_two_companies_sees_each_through_its_own_membership(app, world):
    owner, cid = world
    people = staff(app, owner, cid)
    erin = people["member"]
    second = create_company(erin, "Erin Co")  # erin owns this one, is only a member of the first
    sid = second["id"]
    assert erin.get(api(cid, "/tasks")).status_code == 200
    assert erin.post(api(cid, "/tasks"), json={"title": "no"}).status_code == 403  # member there
    assert erin.post(api(sid, "/tasks"), json={"title": "yes"}).status_code == 201  # owner here
    roles = {m["company_name"]: m["role"] for m in erin.get("/api/me").json()["memberships"]}
    assert roles == {"Acme Labs": "member", "Erin Co": "owner"}
    # each company's data stays in its company
    assert [t["title"] for t in erin.get(api(sid, "/tasks")).json()["items"]] == ["yes"]
    assert owner.get(api(sid, "/tasks")).status_code == 404  # alice is not a member of Erin Co
    # member refs are per company: a ref from Acme cannot be used in Erin Co
    acme_ref = member_ref(erin, cid)
    r = erin.post(api(sid, "/tasks"), json={"title": "x", "assignee_ref": acme_ref})
    assert r.status_code == 404
    listing = {m["company_id"] for m in erin.get(api(sid, "/members")).json()["items"]}
    assert listing == {sid}


def test_non_members_get_404_not_403(app, world):
    owner, cid = world
    stranger = join(app, invite(owner, cid, "s@example.com"), "stranger")
    other = create_company(stranger, "Stranger Co")
    for path in ("/tasks", "/members", "/invitations"):
        assert owner.get(api(other["id"], path)).status_code == 404
    assert owner.delete(api(other["id"], "/members/" + "2" * 36)).status_code == 404
    assert (
        owner.post(
            api(other["id"], "/invitations"), json={"email": "e@e.co", "role": "viewer"}
        ).status_code
        == 404
    )
