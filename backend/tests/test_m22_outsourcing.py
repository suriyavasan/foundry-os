"""M22 outsourcing: HR drafts, a person approves, posts and records; Foundry moves nothing."""

from __future__ import annotations

import socket
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.outsourcing import OutsourceJob
from tests.accounts_helpers import create_company
from tests.helpers import api, get_task, new_task
from tests.m22_helpers import hr_agent, refs, session, specialist, world  # noqa: F401

JOB = {
    "title": "Logo for Acme",
    "brief": "Context: a B2B tool. Deliverables: SVG + PNG logo. Acceptance: 3 drafts. Timeline: 5 days.",
    "skills": ["logo design", "branding"],
    "marketplace": "fiverr",
    "budget_usd": 50,
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Outsourcing never talks to a marketplace: any outbound connection fails the test."""

    def boom(*a, **k):
        raise AssertionError("outsourcing made a network call")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)


def draft(s, **over):
    return s.post("/outsource-jobs", json={**JOB, **over})


def hr_session(app, world, anon):
    people, cid = world
    return session(app, people["owner"], cid, hr_agent(people["owner"], cid)["id"], anon)


def job_of(app, job_id) -> OutsourceJob:
    with app.state.session_factory() as db:
        return db.get(OutsourceJob, job_id)


def audit_actions(app, cid) -> list[str]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.company_id == cid)
                .order_by(AuditEvent.created_at)
            )
        )


def approve(owner, cid, approval_id, what="approve"):
    r = owner.post(api(cid, f"/approvals/{approval_id}/{what}"), json={"note": "ok"})
    assert r.status_code == 200, r.text
    return r.json()


def approved_job(app, world, anon, **over) -> tuple[str, str]:
    people, cid = world
    out = draft(hr_session(app, world, anon), **over).json()
    approve(people["owner"], cid, out["approval_id"])
    return out["job_id"], out["approval_id"]


def post_to(owner, cid, job_id, step, **body):
    return owner.post(api(cid, f"/outsource-jobs/{job_id}/{step}"), json=body)


# ------------------------------------------------------------------ drafting


def test_hr_drafts_a_job_and_a_human_approval(app, world, anon_client):
    people, cid = world
    s = hr_session(app, world, anon_client)
    r = draft(s)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["job_status"] == "pending_approval" and out["budget_micros"] == 50_000_000
    job = people["owner"].get(api(cid, f"/outsource-jobs/{out['job_id']}")).json()
    assert job["status"] == "pending_approval" and job["marketplace"] == "fiverr"
    assert job["skills"] == ["logo design", "branding"] and job["approval_status"] == "pending"
    assert job["created_by_agent_name"] and job["open_url"].startswith("https://www.fiverr.com/")
    assert job["paid_micros"] is None and job["cost_kind"] is None
    approval = people["owner"].get(api(cid, f"/approvals/{out['approval_id']}")).json()
    assert approval["kind"] == "outsource_job" and approval["approver"] == "human"
    assert approval["amount_micros"] == 50_000_000 and approval["risk"] == "medium"
    assert approval["subject_type"] == "outsource_job" and approval["subject_id"] == out["job_id"]
    assert "outsource.drafted" in audit_actions(app, cid)


def test_only_the_hr_agent_may_draft(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    for agent in (
        specialist(owner, cid),
        next(a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"),
    ):
        s = session(app, owner, cid, agent["id"], anon_client)
        r = draft(s)
        assert r.status_code == 403, (agent["name"], r.text)
    assert owner.get(api(cid, "/outsource-jobs")).json()["items"] == []
    # people cannot use the agent endpoint, and agents cannot read the people's list
    assert owner.post("/api/agent/v1/outsource-jobs", json=JOB).status_code in (401, 403)


def test_the_approval_is_human_whatever_the_policy_says(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    from tests.helpers import set_policy

    set_policy(owner, cid, human_approval_required_for=[], autonomy_mode="autonomous")
    s = hr_session(app, world, anon_client)
    out = draft(s).json()
    with app.state.session_factory() as db:
        approval = db.get(Approval, out["approval_id"])
        assert approval.approver == "human" and approval.status == "pending"
    # the Co-founder (an agent) can never decide it
    founder = next(
        a for a in owner.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    fs = session(app, owner, cid, founder["id"], anon_client)
    r = fs.post(f"/approvals/{out['approval_id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    assert job_of(app, out["job_id"]).status == "pending_approval"


def test_nobody_can_request_an_outsourcing_approval_without_a_job(app, world, anon_client):
    s = hr_session(app, world, anon_client)
    r = s.post("/approvals", json={"kind": "outsource_job", "title": "Hire someone"})
    assert r.status_code == 422


def test_the_budget_ceiling_and_the_company_budget(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    s = hr_session(app, world, anon_client)
    over = draft(s, budget_usd=250)
    assert (
        over.status_code == 409
        and over.json()["error"]["details"]["limit"] == "outsource_max_job_micros"
    )
    assert owner.get(api(cid, "/outsource-jobs")).json()["items"] == []
    from tests.helpers import set_policy

    assert (
        set_policy(owner, cid, outsource_max_job_micros=300_000_000)["outsource_max_job_micros"]
        == 300_000_000
    )
    assert draft(s, budget_usd=250).status_code == 201
    # the company's monthly budget: what is left after the job already waiting
    set_policy(owner, cid, monthly_budget_micros=400_000_000)
    tight = draft(s, budget_usd=200, title="Another")
    assert (
        tight.status_code == 409 and tight.json()["error"]["details"]["limit"] == "company_monthly"
    )
    assert tight.json()["error"]["details"]["remaining_micros"] == 150_000_000
    assert draft(s, budget_usd=150, title="Another").status_code == 201
    # agents can never change the ceiling
    assert s.patch("/policy", json={"outsource_max_job_micros": 1}).status_code in (404, 405, 403)
    assert (
        people["member"]
        .patch(api(cid, "/policy"), json={"outsource_max_job_micros": 1})
        .status_code
        == 403
    )


def test_at_most_five_open_jobs(app, world, anon_client):
    people, cid = world
    s = hr_session(app, world, anon_client)
    for i in range(5):
        assert draft(s, title=f"Job {i}", budget_usd=10).status_code == 201
    r = draft(s, title="Job 6", budget_usd=10)
    assert r.status_code == 409 and r.json()["error"]["details"]["limit"] == "open_outsource_jobs"
    # cancelling one frees a slot
    first = people["owner"].get(api(cid, "/outsource-jobs")).json()["items"][-1]
    assert (
        post_to(people["owner"], cid, first["id"], "cancel", reason="not needed").status_code == 200
    )
    assert draft(s, title="Job 7", budget_usd=10).status_code == 201


@pytest.mark.parametrize(
    "brief",
    [
        "Contact me at jane.doe@gmail.com for details of the logo.",
        "Call +1 415 555 0134 and ask for the brand guidelines of the product.",
        "Use the key sk-ant-api03-" + "a" * 40 + " to log in to the design tool.",
        "Pay with card 4111 1111 1111 1111 expiry 12/29 when asked by the seller.",
    ],
)
def test_the_brief_is_scanned_for_secrets_and_personal_data(app, world, anon_client, brief):
    people, cid = world
    s = hr_session(app, world, anon_client)
    r = draft(s, brief=brief)
    assert r.status_code == 422 and "secrets or personal data" in r.json()["error"]["message"]
    assert people["owner"].get(api(cid, "/outsource-jobs")).json()["items"] == []


def test_the_linked_task_must_exist_in_this_company(app, world, anon_client):
    people, cid = world
    s = hr_session(app, world, anon_client)
    assert draft(s, task_id="7" * 36).status_code == 404
    t = new_task(
        people["owner"], cid, title="Brand", assignee_agent_id=hr_agent(people["owner"], cid)["id"]
    )
    out = draft(s, task_id=t["id"])
    assert out.status_code == 201
    assert (
        people["owner"]
        .get(api(cid, f"/outsource-jobs/{out.json()['job_id']}"))
        .json()["task_title"]
        == "Brand"
    )


# ------------------------------------------------------------------ the approval


def test_declining_the_approval_declines_the_job_and_wakes_hr(app, world, anon_client):
    people, cid = world
    hr = hr_agent(people["owner"], cid)
    out = draft(hr_session(app, world, anon_client)).json()
    approve(people["owner"], cid, out["approval_id"], "reject")
    job = job_of(app, out["job_id"])
    assert job.status == "declined"
    with app.state.session_factory() as db:
        texts = [
            r["text"]
            for req in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == hr["id"]))
            for r in req.reasons
        ]
    assert any("Do not draft the same job again" in t for t in texts)
    assert "outsource.declined" in audit_actions(app, cid)
    # nothing more can happen to a declined job
    assert (
        post_to(
            people["owner"], cid, out["job_id"], "posted", listing_url="https://www.fiverr.com/x"
        ).status_code
        == 409
    )


def test_an_expired_approval_declines_the_job_never_approves_it(app, world, anon_client):
    people, cid = world
    out = draft(hr_session(app, world, anon_client)).json()
    from app.services import approvals

    with app.state.session_factory() as db:
        approvals.sweep_deadlines(db, utcnow() + timedelta(days=3))
    job = job_of(app, out["job_id"])
    assert job.status == "declined"
    with app.state.session_factory() as db:
        assert db.get(Approval, out["approval_id"]).status == "expired"


def test_approving_makes_the_job_approved_and_a_chat_cannot_decide_it(app, world, anon_client):
    people, cid = world
    out = draft(hr_session(app, world, anon_client)).json()
    from app.services.channels import render

    with app.state.session_factory() as db:
        assert render.approval_is_chat_decidable(db.get(Approval, out["approval_id"])) is False
    approve(people["owner"], cid, out["approval_id"])
    assert job_of(app, out["job_id"]).status == "approved"
    # an approval can only be decided once
    assert (
        people["owner"]
        .post(api(cid, f"/approvals/{out['approval_id']}/approve"), json={})
        .status_code
        == 409
    )


# ------------------------------------------------------------------ the person's steps


def test_the_whole_flow_records_each_step_and_a_verified_expense(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    t = new_task(owner, cid, title="Brand work", assignee_agent_id=hr_agent(owner, cid)["id"])
    job_id, _ = approved_job(app, world, anon_client, task_id=t["id"])
    posted = post_to(owner, cid, job_id, "posted", listing_url="https://www.fiverr.com/gigs/logo-1")
    assert posted.status_code == 200 and posted.json()["status"] == "posted"
    hired = post_to(
        owner,
        cid,
        job_id,
        "hired",
        freelancer_name="Pat Designer",
        freelancer_profile_url="https://www.fiverr.com/pat",
        agreed_price_usd=45.5,
    )
    assert hired.status_code == 200
    body = hired.json()
    assert body["status"] == "hired" and body["agreed_price_micros"] == 45_500_000
    up = owner.post(
        api(cid, "/artifacts"),
        data={"title": "Logo files", "kind": "other", "task_id": t["id"]},
        files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")},
    )
    assert up.status_code == 201, up.text
    delivered = post_to(owner, cid, job_id, "delivered", artifact_ids=[up.json()["id"]], notes="v2")
    assert delivered.status_code == 200 and delivered.json()["status"] == "delivered"
    assert get_task(owner, cid, t["id"])["output_refs"] == [up.json()["id"]]
    paid = post_to(owner, cid, job_id, "paid", amount_usd=45.5)
    assert paid.status_code == 200
    out = paid.json()
    assert out["paid_micros"] == 45_500_000 and out["cost_kind"] == "verified"
    with app.state.session_factory() as db:
        expense = db.get(ExternalExpense, out["ledger_entry_id"])
        assert isinstance(expense.amount_micros, int) and expense.amount_micros == 45_500_000
        assert expense.cost_kind == "verified" and expense.source == "outsourcing"
        assert expense.company_id == cid and "Pat Designer" in expense.vendor
    shown = owner.get(api(cid, "/finance/expenses")).json()["items"]
    assert any(e["source"] == "outsourcing" and e["cost_kind"] == "verified" for e in shown)
    assert post_to(owner, cid, job_id, "close").json()["status"] == "closed"
    actions = audit_actions(app, cid)
    for needed in (
        "outsource.drafted",
        "outsource.approved",
        "outsource.posted",
        "outsource.hired",
        "outsource.delivered",
        "outsource.paid",
        "outsource.closed",
        "expense.created",
    ):
        assert needed in actions, needed
    assert post_to(owner, cid, job_id, "paid", amount_usd=1).status_code == 409  # closed


def test_the_steps_are_validated_in_order(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    out = draft(hr_session(app, world, anon_client)).json()
    jid = out["job_id"]
    url = "https://www.fiverr.com/gigs/x"
    # before the approval nothing can be recorded
    assert post_to(owner, cid, jid, "posted", listing_url=url).status_code == 409
    assert (
        post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=1).status_code
        == 409
    )
    assert post_to(owner, cid, jid, "delivered", artifact_ids=[]).status_code == 409
    assert post_to(owner, cid, jid, "paid", amount_usd=1).status_code == 409
    approve(owner, cid, out["approval_id"])
    # approved: delivered / paid still impossible; the person may hire straight away or post first
    assert post_to(owner, cid, jid, "delivered", artifact_ids=[]).status_code == 409
    assert post_to(owner, cid, jid, "paid", amount_usd=1).status_code == 409
    assert post_to(owner, cid, jid, "posted", listing_url=url).status_code == 200
    assert post_to(owner, cid, jid, "posted", listing_url=url).status_code == 409  # not twice
    # hired: the agreed price may not exceed the approved budget
    over = post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=50.01)
    assert over.status_code == 422 and "Raise budget" in over.json()["error"]["message"]
    assert job_of(app, jid).status == "posted"
    assert (
        post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=40).status_code
        == 200
    )
    # paid: not above the agreed price, once only
    assert post_to(owner, cid, jid, "paid", amount_usd=40.01).status_code == 422
    assert post_to(owner, cid, jid, "paid", amount_usd=0).status_code == 422
    assert post_to(owner, cid, jid, "paid", amount_usd=25).status_code == 200
    assert post_to(owner, cid, jid, "paid", amount_usd=15).status_code == 409
    # the hire cannot be rewritten once money was recorded; a paid job cannot be cancelled
    assert (
        post_to(owner, cid, jid, "hired", freelancer_name="Q", agreed_price_usd=40).status_code
        == 409
    )
    assert post_to(owner, cid, jid, "cancel", reason="x").status_code == 409


def test_the_listing_url_must_be_on_the_marketplaces_own_domain(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    jid, _ = approved_job(app, world, anon_client)
    bad = [
        "http://www.fiverr.com/gigs/x",  # not https
        "https://fiverr.com.evil.example/gigs/x",
        "https://www.upwork.com/jobs/x",  # the wrong marketplace
        "https://user:pw@www.fiverr.com/x",
        "https://notfiverr.com/x",
        "javascript:alert(1)",
        "www.fiverr.com/x",
    ]
    for url in bad:
        r = post_to(owner, cid, jid, "posted", listing_url=url)
        assert r.status_code == 422, url
    assert job_of(app, jid).status == "approved"
    assert post_to(owner, cid, jid, "posted", listing_url="https://fiverr.com/x").status_code == 200
    # "other" takes any https link, an upwork job its own domain
    jid2, _ = approved_job(app, world, anon_client, marketplace="other", title="Other")
    assert (
        post_to(owner, cid, jid2, "posted", listing_url="https://example.org/job/1").status_code
        == 200
    )
    jid3, _ = approved_job(app, world, anon_client, marketplace="upwork", title="Up")
    assert (
        post_to(
            owner, cid, jid3, "posted", listing_url="https://www.upwork.com/jobs/~01"
        ).status_code
        == 200
    )
    assert (
        post_to(
            owner,
            cid,
            jid3,
            "hired",
            freelancer_name="Z",
            agreed_price_usd=5,
            freelancer_profile_url="https://evil.example/z",
        ).status_code
        == 422
    )


def test_raising_the_budget_needs_a_new_human_approval(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    jid, first = approved_job(app, world, anon_client)
    r = owner.post(
        api(cid, f"/outsource-jobs/{jid}/raise-budget"),
        json={"new_budget_usd": 80, "reason": "scope grew"},
    )
    assert (
        r.status_code == 200 and r.json()["budget_micros"] == 50_000_000
    )  # unchanged until approved
    with app.state.session_factory() as db:
        pending = list(
            db.scalars(
                select(Approval).where(
                    Approval.status == "pending", Approval.kind == "outsource_job"
                )
            )
        )
    assert (
        len(pending) == 1
        and pending[0].amount_micros == 30_000_000
        and pending[0].approver == "human"
    )
    assert pending[0].id != first
    # only one raise at a time; the ceiling still holds; lowering is not a raise
    assert (
        owner.post(
            api(cid, f"/outsource-jobs/{jid}/raise-budget"), json={"new_budget_usd": 90}
        ).status_code
        == 409
    )
    assert owner.post(
        api(cid, f"/outsource-jobs/{jid}/raise-budget"), json={"new_budget_usd": 40}
    ).status_code in (409, 422)
    assert (
        post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=70).status_code
        == 422
    )
    approve(owner, cid, pending[0].id)
    assert job_of(app, jid).budget_micros == 80_000_000 and job_of(app, jid).status == "approved"
    assert (
        post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=70).status_code
        == 200
    )
    # a declined raise changes nothing
    r = owner.post(api(cid, f"/outsource-jobs/{jid}/raise-budget"), json={"new_budget_usd": 150})
    assert r.status_code == 200
    with app.state.session_factory() as db:
        again = db.scalar(
            select(Approval).where(Approval.status == "pending", Approval.kind == "outsource_job")
        )
    approve(owner, cid, again.id, "reject")
    assert job_of(app, jid).budget_micros == 80_000_000 and job_of(app, jid).status == "hired"
    # above the per-job ceiling is refused up front
    assert (
        owner.post(
            api(cid, f"/outsource-jobs/{jid}/raise-budget"), json={"new_budget_usd": 500}
        ).status_code
        == 409
    )


def test_cancelling_a_pending_job_withdraws_its_approval(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    out = draft(hr_session(app, world, anon_client)).json()
    assert post_to(owner, cid, out["job_id"], "cancel", reason="plans changed").status_code == 200
    with app.state.session_factory() as db:
        assert db.get(Approval, out["approval_id"]).status == "rejected"
    assert job_of(app, out["job_id"]).status == "cancelled"
    assert "outsource.cancelled" in audit_actions(app, cid)


# ------------------------------------------------------------------ who may do what


def test_only_owners_and_admins_record_steps_and_everyone_in_the_company_reads(
    app, world, anon_client
):
    people, cid = world
    jid, _ = approved_job(app, world, anon_client)
    url = "https://www.fiverr.com/gigs/x"
    for who in ("member", "viewer"):
        b = people[who]
        assert post_to(b, cid, jid, "posted", listing_url=url).status_code == 403
        assert post_to(b, cid, jid, "cancel", reason="x").status_code == 403
        assert (
            b.post(
                api(cid, f"/outsource-jobs/{jid}/raise-budget"), json={"new_budget_usd": 60}
            ).status_code
            == 403
        )
        assert b.get(api(cid, f"/outsource-jobs/{jid}")).status_code == 200
    assert post_to(people["admin"], cid, jid, "posted", listing_url=url).status_code == 200
    # an agent cannot use the people's endpoints
    s = hr_session(app, world, anon_client)
    r = s.client.post(
        api(cid, f"/outsource-jobs/{jid}/hired"),
        json={"freelancer_name": "P", "agreed_price_usd": 1},
        headers=s.headers,
    )
    assert r.status_code in (401, 403)
    assert s.client.get(api(cid, "/outsource-jobs"), headers=s.headers).status_code in (401, 403)


def test_tenancy_another_company_sees_and_touches_nothing(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    jid, _ = approved_job(app, world, anon_client)
    other = create_company(owner, "Other Co")["id"]
    assert owner.get(api(other, "/outsource-jobs")).json()["items"] == []
    assert owner.get(api(other, f"/outsource-jobs/{jid}")).status_code == 404
    for step, body in (
        ("posted", {"listing_url": "https://www.fiverr.com/x"}),
        ("hired", {"freelancer_name": "P", "agreed_price_usd": 1}),
        ("delivered", {"artifact_ids": []}),
        ("paid", {"amount_usd": 1}),
        ("close", None),
        ("cancel", {}),
        ("raise-budget", {"new_budget_usd": 60}),
    ):
        r = owner.post(api(other, f"/outsource-jobs/{jid}/{step}"), json=body or {})
        assert r.status_code == 404, step
    assert job_of(app, jid).status == "approved"


def test_an_artifact_from_another_company_cannot_be_delivered(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    jid, _ = approved_job(app, world, anon_client)
    post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=10)
    assert post_to(owner, cid, jid, "delivered", artifact_ids=["3" * 36]).status_code == 404
    assert job_of(app, jid).status == "hired"


def test_money_is_integer_micros_and_never_a_float(app, world, anon_client):
    people, cid = world
    owner = people["owner"]
    jid, _ = approved_job(app, world, anon_client, budget_usd=19.99)
    post_to(owner, cid, jid, "hired", freelancer_name="P", agreed_price_usd=19.99)
    out = post_to(owner, cid, jid, "paid", amount_usd=19.99).json()
    assert out["budget_micros"] == 19_990_000 and out["agreed_price_micros"] == 19_990_000
    assert out["paid_micros"] == 19_990_000
