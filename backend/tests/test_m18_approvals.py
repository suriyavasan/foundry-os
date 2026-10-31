"""M18 A1: approvals have deadlines, the Co-founder is woken at once, no approval is a dead end.

Timeouts never approve anything: a human approval that times out is declined (``expired``), the
requester is woken to continue another way."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.governance import Approval
from app.models.work import Task
from app.services import approvals
from tests.guardrail_helpers import add_rule, connect_mail, make_team, run_as, send
from tests.helpers import agent_of_family, api, new_agent, new_task, seed_library, set_policy
from tests.test_agent_api import start_run
from tests.test_hiring import hire_body, skills_env_override  # noqa: F401


@pytest.fixture
def hiring_world(app, client, company):
    cid = company["id"]
    seed_library(client, cid)
    founder = agent_of_family(client, cid, "founder")
    hr_agent = agent_of_family(client, cid, "hr")
    hr = start_run(app, client, cid, hr_agent["id"], task_title="Hire an engineer", kind="hiring")
    return cid, founder, hr_agent, hr


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    founder = client.get(api(cid, "/overview")).json()["founder"]
    worker = new_agent(client, cid, name="Worker")
    return cid, founder, worker


def ask(sess, kind="other", **extra):
    r = sess.post("/approvals", json={"kind": kind, "title": f"Need {kind}", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def wake_triggers(app, agent_id: str) -> list[str]:
    with app.state.session_factory() as db:
        return [
            r["trigger"]
            for req in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id))
            for r in req.reasons
        ]


def row(app, approval_id: str) -> Approval:
    with app.state.session_factory() as db:
        a = db.get(Approval, approval_id)
        db.expunge(a)
        return a


def sweep(app, now):
    with app.state.session_factory() as db:
        return approvals.sweep_deadlines(db, now)


def actions(app, target_id: str) -> list[str]:
    with app.state.session_factory() as db:
        return list(db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == target_id)))


# ------------------------------------------------------------------ deadlines at creation


def test_every_pending_approval_gets_a_deadline_from_policy(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    human_one = ask(sess, "external_purchase")
    founder_one = ask(sess, "hire")
    h = client.get(api(cid, f"/approvals/{human_one['id']}")).json()
    f = client.get(api(cid, f"/approvals/{founder_one['id']}")).json()
    assert h["approver"] == "human" and f["approver"] == "founder"
    now = utcnow()
    for body, seconds in ((h, 86_400), (f, 1_800)):
        expires = approvals_expires(body)
        assert abs((expires - now).total_seconds() - seconds) < 30
    # the deadlines are policy: bounded, human-only and audited
    pol = client.get(api(cid, "/policy")).json()
    assert (
        pol["approval_timeout_seconds"] == 86_400 and pol["agent_approval_timeout_seconds"] == 1_800
    )
    r = client.patch(api(cid, "/policy"), json={"approval_timeout_seconds": 7_200})
    assert r.status_code == 200 and r.json()["approval_timeout_seconds"] == 7_200
    for bad in (
        {"approval_timeout_seconds": 3_599},
        {"approval_timeout_seconds": 604_801},
        {"agent_approval_timeout_seconds": 299},
        {"agent_approval_timeout_seconds": 86_401},
    ):
        assert client.patch(api(cid, "/policy"), json=bad).status_code == 422, bad
    again = ask(sess, "external_purchase")
    expires = approvals_expires(client.get(api(cid, f"/approvals/{again['id']}")).json())
    assert abs((expires - utcnow()).total_seconds() - 7_200) < 30
    # an agent can never change them (403), whatever its token
    r = client.patch(
        api(cid, "/policy"),
        json={"approval_timeout_seconds": 3_600},
        headers=sess.headers,
    )
    assert r.status_code == 403
    with app.state.session_factory() as db:
        changed = [
            e.data.get("changed")
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "policy.updated"))
        ]
    assert ["approval_timeout_seconds"] in changed


def approvals_expires(body):
    from datetime import datetime

    assert body["expires_at"]
    return datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))


# ------------------------------------------------------------------ the Co-founder is woken


def test_creating_a_cofounder_approval_wakes_the_cofounder_at_once(app, client, world):
    cid, founder, worker = world
    sess = start_run(app, client, cid, worker["id"])
    assert wake_triggers(app, founder["id"]) == []
    ask(sess, "hire")  # path 1: company_request_approval
    assert "approval_pending" in wake_triggers(app, founder["id"])
    with app.state.session_factory() as db:
        [req] = list(db.scalars(select(WakeRequest).where(WakeRequest.agent_id == founder["id"])))
        # short debounce: due within seconds, not at the next heartbeat
        assert (req.not_before - utcnow()).total_seconds() <= 10


def test_a_guardrail_cofounder_approval_wakes_the_cofounder(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    mail = connect_mail(app, client, cid)
    add_rule(client, cid, tier="external_communication", decision="cofounder_approval")
    r = send(run_as(app, client, cid, team.worker), mail)
    assert r.status_code == 202 and r.json()["approver"] == "founder"
    assert wake_triggers(app, team.founder["id"]).count("approval_pending") == 1  # once, not twice


def test_a_hire_approval_for_the_cofounder_wakes_the_cofounder(client, app, hiring_world):
    cid, founder, _, hr = hiring_world
    set_policy(client, cid, auto_activate_hires=False)
    r = hr.post("/hires", json=hire_body(founder, hr, name="Zed"))
    assert r.status_code == 201 and r.json()["approver"] == "founder"
    assert "approval_pending" in wake_triggers(app, founder["id"])


# ------------------------------------------------------------------ the self-request dead end


def test_a_request_by_the_cofounder_goes_to_a_human_who_is_told(app, client, world):
    cid, founder, _ = world
    sess = start_run(app, client, cid, founder["id"])
    mine = ask(sess, "hire")  # founder-approvable, but the Co-founder asked
    assert mine["approver"] == "human"
    assert (
        wake_triggers(app, founder["id"]) == []
    )  # nobody wakes the Co-founder for its own request
    notes = client.get("/api/notifications").json()["items"]
    assert any(n["kind"] == "approval_needed" and n["related_id"] == mine["id"] for n in notes)
    # it expires like any human approval
    out = sweep(app, utcnow() + timedelta(hours=25))
    assert out["expired"] == 1
    assert row(app, mine["id"]).status == "expired"


# ------------------------------------------------------------------ sweep: escalate once, expire


def test_cofounder_approval_is_handed_to_a_human_once_then_expires_declined(app, client, world):
    cid, founder, worker = world
    sess = start_run(app, client, cid, worker["id"])
    a = ask(sess, "hire")
    assert a["approver"] == "founder"
    assert sweep(app, utcnow() + timedelta(minutes=10)) == {"escalated": 0, "expired": 0}
    out = sweep(app, utcnow() + timedelta(minutes=31))
    assert out == {"escalated": 1, "expired": 0}
    escalated = row(app, a["id"])
    assert escalated.status == "pending" and escalated.approver == "human"
    assert escalated.approver_member_id is None
    assert (escalated.expires_at - utcnow()).total_seconds() > 80_000  # fresh human deadline
    assert "approval.escalated" in actions(app, a["id"])
    notes = client.get("/api/notifications").json()["items"]
    assert any(n["kind"] == "approval_needed" and n["related_id"] == a["id"] for n in notes)
    # escalated once: another sweep soon after does nothing
    assert sweep(app, utcnow() + timedelta(hours=1)) == {"escalated": 0, "expired": 0}
    assert actions(app, a["id"]).count("approval.escalated") == 1
    # the human window passes with no answer: declined by timeout, never approved
    out = sweep(app, utcnow() + timedelta(hours=25))
    assert out == {"escalated": 0, "expired": 1}
    done = row(app, a["id"])
    assert done.status == "expired" and done.consumed_at is None
    assert done.decision_note == "No answer within 24h; declined by timeout"
    assert done.decided_by_type == "system" and done.decided_at is not None
    assert "approval.expired" in actions(app, a["id"])
    # an expired approval can neither be decided nor consumed
    assert client.post(api(cid, f"/approvals/{a['id']}/approve")).status_code == 409
    from app.core.errors import Conflict
    from app.core.principal import CompanyContext, Role

    with app.state.session_factory() as db, pytest.raises(Conflict, match="expired"):
        approvals.consume(
            db, CompanyContext(cid, _owner(client, cid), Role.OWNER), a["id"], dict(done.scope)
        )


def _owner(client, cid):
    from app.core.principal import Principal, PrincipalType

    me = client.get("/api/me").json()
    return Principal(PrincipalType.HUMAN, me["user"]["id"], "Owner")


def test_expired_human_approval_wakes_the_requester_and_returns_its_task(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    a = ask(sess, "external_purchase")
    # a task that was waiting on this approval (blocked, pointing at it)
    task = new_task(client, cid, title="Needs the card", assignee_agent_id=worker["id"])
    with app.state.session_factory() as db:
        ap = db.get(Approval, a["id"])
        ap.task_id = task["id"]
        t = db.get(Task, task["id"])
        t.status = "blocked"
        t.error = {"code": "blocked", "message": "waiting", "approval_id": a["id"], "run_id": None}
        db.commit()
    out = sweep(app, utcnow() + timedelta(hours=25))
    assert out == {"escalated": 0, "expired": 1}
    assert "approval_expired" in wake_triggers(app, worker["id"])
    with app.state.session_factory() as db:
        t = db.get(Task, task["id"])
        assert t.status == "ready"  # back in the queue
        assert "declined by timeout" in (t.review_note or "")
    assert row(app, a["id"]).status == "expired"


def test_deciding_an_approval_wakes_the_requester(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    a = ask(sess, "external_purchase")
    assert wake_triggers(app, worker["id"]) == []
    assert client.post(api(cid, f"/approvals/{a['id']}/reject")).status_code == 200
    assert "approval" in wake_triggers(app, worker["id"])


def test_a_human_decision_before_the_deadline_wins_and_the_sweep_leaves_it_alone(
    app, client, world
):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    a = ask(sess, "external_purchase")
    assert client.post(api(cid, f"/approvals/{a['id']}/approve")).status_code == 200
    assert sweep(app, utcnow() + timedelta(days=3)) == {"escalated": 0, "expired": 0}
    assert row(app, a["id"]).status == "approved"


def test_expired_guardrail_approval_is_not_resubmitted_in_a_loop(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    mail = connect_mail(app, client, cid)
    add_rule(client, cid, tier="external_communication", decision="human_approval")
    worker = run_as(app, client, cid, team.worker)
    r = send(worker, mail)
    assert r.status_code == 202
    aid = r.json()["approval_id"]
    sweep(app, utcnow() + timedelta(hours=25))
    assert row(app, aid).status == "expired"
    assert "approval_expired" in wake_triggers(app, team.worker["id"])
    again = send(worker, mail)  # the identical content
    assert again.status_code == 403  # remembered like a rejection: no second approval
    with app.state.session_factory() as db:
        assert len(list(db.scalars(select(Approval).where(Approval.kind != "hire")))) == 1


def test_the_sweep_skips_paused_companies_and_purchase_approvals_keep_their_own_rule(
    app, client, world
):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    a = ask(sess, "external_purchase")
    client.post(api(cid, "/pause"))
    assert sweep(app, utcnow() + timedelta(days=2)) == {"escalated": 0, "expired": 0}
    client.post(api(cid, "/resume"))
    assert sweep(app, utcnow() + timedelta(days=2))["expired"] == 1
    assert row(app, a["id"]).status == "expired"
    # purchasing creates its approvals with a subject and no deadline of their own
    with app.state.session_factory() as db:
        from app.core.principal import Principal, PrincipalType

        made = approvals.create_approval_record(
            db,
            company_id=cid,
            principal=Principal(PrincipalType.AGENT, worker["id"], "W", cid),
            kind="external_purchase",
            title="Buy a thing",
            subject=("purchase_request", "p-1"),
        )
        assert made.expires_at is None


def test_a_peer_review_nobody_did_is_handed_to_a_person_and_its_review_task_is_cancelled(
    app, client, company
):
    cid = company["id"]
    team = make_team(client, cid)
    mail = connect_mail(app, client, cid)
    r = send(run_as(app, client, cid, team.worker), mail)  # tier default: peer review
    assert r.status_code == 202 and r.json()["approver"] == "peer"
    aid = r.json()["approval_id"]
    with app.state.session_factory() as db:
        review = db.scalar(select(Task).where(Task.idempotency_key == f"peerreview:{aid}"))
        assert review is not None and review.status not in ("cancelled", "done")
        review_id = review.id
    assert sweep(app, utcnow() + timedelta(minutes=31)) == {"escalated": 1, "expired": 0}
    escalated = row(app, aid)
    assert escalated.status == "pending" and escalated.approver == "human"
    assert escalated.reviewer_agent_id is None
    with app.state.session_factory() as db:
        assert db.get(Task, review_id).status == "cancelled"
    # a person can still decide it, as with any human approval
    assert client.post(api(cid, f"/approvals/{aid}/approve")).status_code == 200
