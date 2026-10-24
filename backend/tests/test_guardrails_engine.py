"""Guardrail decisions end to end (docs/specs/m6 sections 1, 4, 5): rules and their precedence,
peer review / Co-founder / human approval, scope-bound single-use approvals, outbound DLP,
credential grants and the AI disclosure footer."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.governance import Approval
from app.models.guardrails import GuardrailEvent
from app.models.work import Task
from tests.guardrail_helpers import (
    SECRET,
    add_rule,
    connect_mail,
    events,
    make_team,
    run_as,
    send,
    set_family,
)
from tests.helpers import api, new_agent, set_policy


@pytest.fixture
def team(client, company):
    return make_team(client, company["id"])


@pytest.fixture
def mail(app, client, team):
    return connect_mail(app, client, team.cid)


def approval(client, cid, approval_id) -> dict:
    r = client.get(api(cid, f"/approvals/{approval_id}"))
    assert r.status_code == 200, r.text
    return r.json()


def review_task(app, approval_id: str) -> Task | None:
    with app.state.session_factory() as db:
        return db.scalar(select(Task).where(Task.idempotency_key == f"peerreview:{approval_id}"))


# ------------------------------------------------------------------ rule resolution


def test_rule_precedence_action_over_tier_agent_over_family_over_all_newest_wins(
    app, client, team, mail
):
    cid = team.cid
    set_family(app, team.worker["id"], "marketing")
    worker = run_as(app, client, cid, team.worker)

    assert send(worker, mail).status_code == 202  # tier default: peer review
    add_rule(client, cid, tier="external_communication", decision="allow")  # everyone: allow
    assert send(worker, mail, subject="two").status_code == 200
    # the family rule beats the all rule
    fam = add_rule(
        client,
        cid,
        tier="external_communication",
        scope_type="family",
        scope_value="marketing",
        decision="deny",
    )
    denied = send(worker, mail, subject="three")
    assert denied.status_code == 403 and denied.json()["error"]["details"]["rule_id"] == fam["id"]
    # a specific action rule beats every tier rule, even one scoped to the agent
    add_rule(
        client,
        cid,
        tier="external_communication",
        scope_type="agent",
        scope_value=team.worker["id"],
        decision="human_approval",
    )
    act = add_rule(
        client, cid, tier="external_communication", action="email.send", decision="allow"
    )
    assert send(worker, mail, subject="four").status_code == 200
    assert any(e["rule_id"] == act["id"] and e["decision"] == "allow" for e in events(client, cid))
    # same target and scope: the newest wins
    newest = add_rule(
        client, cid, tier="external_communication", action="email.send", decision="deny"
    )
    r = send(worker, mail, subject="five")
    assert r.status_code == 403 and r.json()["error"]["details"]["rule_id"] == newest["id"]
    # another action of the tier: the agent rule beats the family and the all rule
    r = send(worker, mail, action="social.post", subject="post")
    assert r.status_code == 202
    assert approval(client, cid, r.json()["approval_id"])["approver"] == "human"


def test_rule_conditions_amount_per_day_and_domains(app, client, team):
    cid = team.cid
    shop = connect_mail(app, client, cid, actions=("purchase.*",), provider="shop")
    worker = run_as(app, client, cid, team.worker)

    def buy(amount: int):
        return worker.post(
            "/provider-actions",
            json={
                "credential_id": shop.id,
                "action": "purchase.make",
                "params": {"amount_micros": amount, "item": f"thing {amount}"},
            },
        )

    # spend default: a human above auto_spend_limit_micros (0), else the Co-founder
    p = buy(2_000_000)
    assert (
        p.status_code == 202
        and approval(client, cid, p.json()["approval_id"])["approver"] == "human"
    )
    set_policy(client, cid, auto_spend_limit_micros=5_000_000)
    p = buy(3_000_000)
    assert approval(client, cid, p.json()["approval_id"])["approver"] == "founder"
    # "allow up to $1" is a rule with a condition; above it the default applies again
    add_rule(
        client, cid, tier="spend", decision="allow", conditions={"max_amount_micros": 1_000_000}
    )
    assert buy(900_000).status_code == 200
    assert buy(1_000_001).status_code == 202

    # max_per_day: the rule applies while fewer than N such actions happened today
    mail = connect_mail(app, client, cid)
    add_rule(
        client,
        cid,
        tier="external_communication",
        action="email.send",
        decision="allow",
        conditions={"max_per_day": 2},
    )
    assert send(worker, mail, subject="1").status_code == 200
    assert send(worker, mail, subject="2").status_code == 200
    assert send(worker, mail, subject="3").status_code == 202  # falls back to the default

    # allowed_domains: the rule applies to recipients on the list only
    add_rule(
        client,
        cid,
        tier="external_communication",
        action="social.post",
        decision="allow",
        conditions={"allowed_domains": ["partner.example"]},
    )
    assert (
        send(worker, mail, action="social.post", to="https://blog.partner.example/x").status_code
        == 200
    )
    other = send(worker, mail, action="social.post", to="https://elsewhere.example/x")
    assert other.status_code == 202


# ------------------------------------------------------------------ peer review (maker-checker)


def test_peer_review_end_to_end_is_scope_bound_and_single_use(app, client, team, mail):
    cid = team.cid
    worker = run_as(app, client, cid, team.worker)
    first = send(worker, mail)
    assert first.status_code == 202, first.text
    pending = first.json()
    assert pending["status"] == "pending_approval" and pending["approver"] == "peer"
    assert (
        pending["decision"] == "peer_review" and pending["reviewer_agent_id"] == team.manager["id"]
    )
    aid = pending["approval_id"]

    ap = approval(client, cid, aid)
    assert (
        ap["approver"] == "peer"
        and ap["status"] == "pending"
        and ap["kind"] == "external_communication"
    )
    assert (ap["tier"], ap["action"]) == ("external_communication", "email.send")
    assert ap["reviewer_agent_id"] == team.manager["id"]
    assert ap["requested_by_agent_id"] == team.worker["id"]
    assert ap["outbound"] == {
        "channel": "email",
        "to": "jane@customer.example",
        "subject": "Hello",
        "body": "Hi Jane, thanks for your interest.",
    }
    assert ap["dlp_findings"] == []
    assert any(
        e["approval_id"] == aid and e["decision"] == "peer_review" for e in events(client, cid)
    )

    # the review is a task in the reviewer's queue
    task = review_task(app, aid)
    assert task is not None and task.kind == "review"
    assert (
        task.assignee_agent_id == team.manager["id"] and task.assignee_agent_id != team.worker["id"]
    )

    # repeating the call while it waits returns the same approval (no duplicates)
    again = send(worker, mail)
    assert again.status_code == 202 and again.json()["approval_id"] == aid
    # only the assigned reviewer may decide: not the author, not a bystander (even the Co-founder)
    assert (
        worker.post(f"/peer-reviews/{aid}/decide", json={"decision": "approve"}).status_code == 403
    )
    founder = run_as(app, client, cid, team.founder)
    assert (
        founder.post(f"/peer-reviews/{aid}/decide", json={"decision": "approve"}).status_code == 403
    )
    assert founder.post(f"/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 403
    assert mail.calls == []

    reviewer = run_as(app, client, cid, team.manager)
    done = reviewer.post(
        f"/peer-reviews/{aid}/decide", json={"decision": "approve", "note": "Looks fine."}
    )
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert done.json()["decided_by_id"] == team.manager["id"]
    assert review_task(app, aid).status == "cancelled"  # never started: closed by the decision

    # the author repeats the identical action: it executes now, exactly once
    ok = send(worker, mail)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "executed" and body["action"] == "email.send"
    assert SECRET not in ok.text and body["result"]["debug"] == "auth=[redacted]"
    assert len(mail.calls) == 1 and mail.calls[0].secret == SECRET
    assert approval(client, cid, aid)["consumed_at"] is not None
    assert any(
        e["approval_id"] == aid and e["decision"] == "allow" and "consumed" in (e["reason"] or "")
        for e in events(client, cid)
    )
    # single use: the same content needs a new review
    third = send(worker, mail)
    assert third.status_code == 202 and third.json()["approval_id"] != aid
    assert len(mail.calls) == 1


def test_an_approval_authorizes_exactly_that_content_by_exactly_that_agent(app, client, team, mail):
    cid = team.cid
    zoe = new_agent(
        client, cid, name="Zoe", role_title="Writer", manager_agent_id=team.manager["id"]
    )
    worker, other = run_as(app, client, cid, team.worker), run_as(app, client, cid, zoe, "zoe")
    reviewer = run_as(app, client, cid, team.manager)
    a = send(worker, mail, body="Version A of the message").json()["approval_id"]
    assert (
        reviewer.post(f"/peer-reviews/{a}/decide", json={"decision": "approve"}).status_code == 200
    )
    # different content: not covered, a new review starts
    b = send(worker, mail, body="Version B of the message")
    assert b.status_code == 202 and b.json()["approval_id"] != a and mail.calls == []
    # another agent sending A's exact content is not covered either
    c = send(other, mail, body="Version A of the message")
    assert c.status_code == 202 and c.json()["approval_id"] not in (a, b.json()["approval_id"])
    # the approved content, by the approved agent, goes through
    assert send(worker, mail, body="Version A of the message").status_code == 200
    assert len(mail.calls) == 1 and mail.calls[0].params["body"].startswith("Version A")


def test_rejection_and_requested_changes_block_the_identical_content(app, client, team, mail):
    cid = team.cid
    worker, reviewer = run_as(app, client, cid, team.worker), run_as(app, client, cid, team.manager)
    a = send(worker, mail, body="Promise: 100% refund, always.").json()["approval_id"]
    r = reviewer.post(
        f"/peer-reviews/{a}/decide", json={"decision": "reject", "note": "We cannot promise that."}
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    again = send(worker, mail, body="Promise: 100% refund, always.")
    assert again.status_code == 403
    err = again.json()["error"]
    assert err["code"] == "guardrail_denied" and "We cannot promise that" in err["message"]
    assert mail.calls == []
    # changed content is a fresh review; "request_changes" blocks that one too
    b = send(worker, mail, body="Refunds follow our policy.").json()["approval_id"]
    assert (
        reviewer.post(
            f"/peer-reviews/{b}/decide",
            json={"decision": "request_changes", "note": "Add the link."},
        ).json()["status"]
        == "changes_requested"
    )
    assert send(worker, mail, body="Refunds follow our policy.").status_code == 403
    denied = [e for e in events(client, cid, decision="deny") if e["approval_id"] in (a, b)]
    assert len(denied) >= 2 and any("We cannot promise" in (e["reason"] or "") for e in denied)


def test_maker_checker_never_selects_the_author_as_its_own_reviewer(app, client, company):
    cid = company["id"]
    # a company of just the Co-founder and HR: nobody independent -> a human decides
    founder = make_team(client, cid).founder
    # (make_team added Mia and Wes; use a second, bare company for the lone case)
    lone = client.post("/api/companies", json={"name": "Solo Co"}).json()
    lone_founder = next(
        a for a in client.get(api(lone["id"], "/agents")).json()["items"] if a["kind"] == "founder"
    )
    mail = connect_mail(app, client, lone["id"])
    r = send(run_as(app, client, lone["id"], lone_founder), mail)
    assert r.status_code == 202 and r.json()["approver"] == "human"
    assert r.json()["reviewer_agent_id"] is None
    assert (
        "no independent reviewer" in approval(client, lone["id"], r.json()["approval_id"])["reason"]
    )

    # the Co-founder with a team gets someone else (never itself, never HR)
    mail2 = connect_mail(app, client, cid, actions=("email.*",), provider="email2")
    team = make_team(client, cid)  # a second team in the same company
    fr = send(run_as(app, client, cid, founder), mail2)
    assert fr.status_code == 202
    reviewer_id = fr.json()["reviewer_agent_id"]
    assert reviewer_id and reviewer_id != founder["id"]
    hr = next(
        a for a in client.get(api(cid, "/agents")).json()["items"] if a["role_family"] == "hr"
    )
    assert reviewer_id != hr["id"]

    # a worker whose manager is paused is checked by a QA agent, then (no QA) by someone else
    client.post(api(cid, f"/agents/{team.manager['id']}/pause"))
    qa = new_agent(client, cid, name="Quinn", role_title="QA", manager_agent_id=founder["id"])
    set_family(app, qa["id"], "qa")
    wr = send(run_as(app, client, cid, team.worker), mail2)
    assert wr.json()["reviewer_agent_id"] == qa["id"]
    # across every approval ever created the reviewer is never the requester
    with app.state.session_factory() as db:
        for ap in db.scalars(select(Approval).where(Approval.approver == "peer")):
            assert ap.reviewer_agent_id and ap.reviewer_agent_id != ap.requested_by_agent_id


def test_cofounder_approval_goes_to_the_cofounder_and_never_to_itself(app, client, team, mail):
    cid = team.cid
    add_rule(client, cid, tier="external_communication", decision="cofounder_approval")
    worker = run_as(app, client, cid, team.worker)
    r = send(worker, mail)
    assert r.status_code == 202 and r.json()["approver"] == "founder"
    aid = r.json()["approval_id"]
    assert worker.post(f"/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 403
    founder = run_as(app, client, cid, team.founder)
    assert founder.post(f"/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 200
    assert send(worker, mail).status_code == 200 and len(mail.calls) == 1
    # the Co-founder's own action cannot be approved by itself: a human decides
    own = send(founder, mail, subject="from the founder")
    assert own.status_code == 202 and own.json()["approver"] == "human"
    assert (
        founder.post(
            f"/approvals/{own.json()['approval_id']}/decide", json={"decision": "approve"}
        ).status_code
        == 403
    )


def test_human_approval_needs_a_human(app, client, team, mail):
    cid = team.cid
    add_rule(client, cid, tier="external_communication", decision="human_approval")
    worker = run_as(app, client, cid, team.worker)
    aid = send(worker, mail).json()["approval_id"]
    founder = run_as(app, client, cid, team.founder)
    r = founder.post(f"/approvals/{aid}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and "human" in r.json()["error"]["message"]
    assert client.post(api(cid, f"/approvals/{aid}/approve")).status_code == 200
    assert send(worker, mail).status_code == 200


def test_commitment_and_destructive_actions_default_to_a_human(app, client, team):
    cid = team.cid
    conn = connect_mail(
        app, client, cid, actions=("domain.register", "resource.delete", "dns.change")
    )
    worker = run_as(app, client, cid, team.worker)
    for action, tier in (
        ("domain.register", "external_commitment"),
        ("resource.delete", "destructive"),
        ("dns.change", "identity"),
    ):
        r = worker.post(
            "/provider-actions",
            json={"credential_id": conn.id, "action": action, "params": {"name": "x.example"}},
        )
        assert r.status_code == 202, (action, r.text)
        assert r.json()["approver"] == "human" and r.json()["tier"] == tier


# ------------------------------------------------------------------ outbound DLP


@pytest.mark.parametrize(
    ("leak", "kind"),
    [
        ("sk-abcdefghijklmnopqrstuvwxyz1234567890", "secret"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "secret"),
        ("-----BEGIN RSA PRIVATE KEY-----", "secret"),
        ("password = hunter2hunter2", "secret"),
        ("Call me on +1 415 555 0132 tonight", "phone"),
        ("Cc: bob.private@gmail.example please", "email"),
    ],
)
def test_dlp_blocks_credential_shapes_phones_and_stray_emails(app, client, team, mail, leak, kind):
    cid = team.cid
    worker = run_as(app, client, cid, team.worker)
    r = send(worker, mail, body=f"Hello Jane. {leak}")
    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "guardrail_denied" and err["details"]["reason"] == "dlp_blocked"
    findings = err["details"]["dlp_findings"]
    assert kind in {f["kind"] for f in findings}
    assert all(set(f) == {"kind", "severity", "description", "count"} for f in findings)
    assert leak.split()[-1] not in r.text  # the matched text is never echoed
    assert mail.calls == []
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(Approval).where(Approval.action == "email.send")) is None
        )  # not reviewed
        rows = list(db.scalars(select(GuardrailEvent).where(GuardrailEvent.decision == "deny")))
    assert rows and all(leak not in (e.reason or "") for e in rows)


def test_dlp_blocks_our_own_tokens_and_stored_credentials(app, client, team, mail):
    cid = team.cid
    worker = run_as(app, client, cid, team.worker)
    owner_token = app.state.owner_token.get()
    for what, text in (
        ("run token", f"debug: {worker.token}"),
        ("owner token", f"here: {owner_token}"),
        ("stored credential", f"the key is {SECRET} ok"),
    ):
        r = send(worker, mail, body=f"Hello. {text}")
        assert r.status_code == 403, what
        details = r.json()["error"]["details"]
        assert "own_token" in {f["kind"] for f in details["dlp_findings"]}, what
        for needle in (worker.token, owner_token, SECRET):
            assert needle not in r.text, what
    assert mail.calls == []
    # nothing sensitive reached the event log or the audit trail either
    with app.state.session_factory() as db:
        blobs = [
            json.dumps([e.reason, e.payload_digest]) for e in db.scalars(select(GuardrailEvent))
        ]
        blobs += [json.dumps([a.summary, a.data]) for a in db.scalars(select(AuditEvent))]
    for needle in (worker.token, owner_token, SECRET):
        assert not any(needle in b for b in blobs)


def test_dlp_blocks_internal_memory_but_allows_the_recipient_and_public_text(
    app, client, team, mail
):
    cid = team.cid
    internal = "We will acquire Globex Corporation next quarter and offer partners a forty percent discount"
    r = client.post(
        api(cid, "/memory"),
        json={"kind": "fact", "title": "Plan", "body": internal, "tags": ["internal"]},
    )
    assert r.status_code == 201 and r.json()["status"] == "verified"
    client.post(
        api(cid, "/memory"),
        json={
            "kind": "fact",
            "title": "Public",
            "body": "Our office opens at nine in the morning",
            "tags": ["hours"],
        },
    )
    worker = run_as(app, client, cid, team.worker)
    leak = send(worker, mail, body=f"FYI {internal}.")
    assert leak.status_code == 403
    assert "internal_memory" in {f["kind"] for f in leak.json()["error"]["details"]["dlp_findings"]}
    # a non-internal fact and the recipient's own address are fine (this one goes to peer review)
    fine = send(
        worker,
        mail,
        to="jane@customer.example",
        body="Our office opens at nine in the morning. Reply to jane@customer.example anytime.",
    )
    assert fine.status_code == 202


def test_ai_disclosure_footer_on_outbound_email_is_configurable(app, client, team, mail):
    cid = team.cid
    add_rule(client, cid, tier="external_communication", decision="allow")
    worker = run_as(app, client, cid, team.worker)
    assert send(worker, mail).status_code == 200
    body = mail.calls[-1].params["body"]
    assert body.startswith("Hi Jane") and "written by an AI agent working for Acme Labs" in body
    set_policy(client, cid, ai_disclosure_text="Sent by {company}'s AI assistant.")
    send(worker, mail, subject="custom")
    assert mail.calls[-1].params["body"].endswith("Sent by Acme Labs's AI assistant.")
    set_policy(client, cid, ai_disclosure_enabled=False)
    send(worker, mail, subject="off", body="No footer please.")
    assert mail.calls[-1].params["body"] == "No footer please."
    set_policy(client, cid, ai_disclosure_enabled=True)
    send(worker, mail, action="social.post", subject="post", body="A public post.")
    assert mail.calls[-1].params["body"] == "A public post."  # only e-mail carries the footer


# ------------------------------------------------------------------ credentials & providers


def test_credential_grants_are_scoped_by_action_and_family_and_revocable(app, client, team):
    cid = team.cid
    add_rule(client, cid, tier="external_communication", decision="allow")
    narrow = connect_mail(app, client, cid, actions=("email.read",), provider="inbox")
    worker = run_as(app, client, cid, team.worker)
    r = send(worker, narrow)  # email.send is not in the credential's actions
    assert r.status_code == 403 and "not scoped" in r.json()["error"]["message"]
    assert narrow.calls == []

    eng = connect_mail(app, client, cid, families=("engineering",), provider="eng-mail")
    assert send(worker, eng).status_code == 403  # the worker is not in the engineering family
    set_family(app, team.worker["id"], "engineering")
    assert send(run_as(app, client, cid, team.worker, "again"), eng).status_code == 200

    # revoking the credential stops the agents using it
    assert client.delete(api(cid, f"/credentials/{eng.id}")).status_code == 200
    assert send(run_as(app, client, cid, team.worker, "later"), eng).status_code == 409
    # another company's credential does not exist for this agent
    other = client.post("/api/companies", json={"name": "Other"}).json()
    foreign = connect_mail(app, client, other["id"], provider="foreign")
    assert send(run_as(app, client, cid, team.worker, "x"), foreign).status_code == 404


def test_a_provider_that_is_not_connected_says_so_and_nothing_is_reviewed(app, client, team):
    cid = team.cid
    mail = connect_mail(app, client, cid, register=False, provider="not-connected")
    worker = run_as(app, client, cid, team.worker)
    r = send(worker, mail)
    assert r.status_code == 501
    err = r.json()["error"]
    assert err["code"] == "not_available" and err["details"]["available"] is False
    assert err["details"]["milestone"] == "M8"
    with app.state.session_factory() as db:  # no review was opened for an action that cannot run
        assert db.scalar(select(Approval)) is None


def test_agents_cannot_read_or_change_credentials_and_humans_can_rotate_and_rescope(
    app, client, team, mail
):
    cid = team.cid
    add_rule(client, cid, tier="external_communication", decision="allow")
    worker = run_as(app, client, cid, team.worker)
    auth = {"Authorization": f"Bearer {worker.token}"}
    assert client.get(api(cid, "/credentials"), headers=auth).status_code == 403
    assert (
        client.patch(
            api(cid, f"/credentials/{mail.id}"),
            json={"allowed_actions": ["email.send"]},
            headers=auth,
        ).status_code
        == 403
    )
    assert (
        client.post(
            api(cid, f"/credentials/{mail.id}/rotate"), json={"value": "x" * 12}, headers=auth
        ).status_code
        == 403
    )
    # humans: validated scope, rotation keeps the id and scope
    assert (
        client.patch(
            api(cid, f"/credentials/{mail.id}"), json={"allowed_actions": ["bogus.action"]}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            api(cid, f"/credentials/{mail.id}"), json={"allowed_families": ["not-a-family"]}
        ).status_code
        == 422
    )
    scoped = client.patch(
        api(cid, f"/credentials/{mail.id}"), json={"allowed_actions": ["email.send"]}
    )
    assert scoped.status_code == 200 and scoped.json()["allowed_actions"] == ["email.send"]
    assert send(worker, mail, action="social.post").status_code == 403
    rotated = client.post(
        api(cid, f"/credentials/{mail.id}/rotate"), json={"value": "rotated-secret-9876543210"}
    )
    assert rotated.status_code == 200 and "rotated-secret" not in rotated.text
    assert send(worker, mail, subject="after rotation").status_code == 200
    assert mail.calls[-1].secret == "rotated-secret-9876543210"
    # the credential list shows metadata only
    listing = client.get(api(cid, "/credentials")).json()["items"]
    assert listing[0]["provider"] == "email" and "encrypted_value" not in json.dumps(listing)


def test_the_company_provider_tool_is_offered_only_when_a_credential_can_be_used(app, client, team):
    from app.services import agent_toolkit

    cid = team.cid
    with app.state.session_factory() as db:
        from app.models.agent import Agent

        agent = db.get(Agent, team.worker["id"])
        assert "company_provider_action" not in agent_toolkit.company_tools_for(db, agent)
    connect_mail(app, client, cid)
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        assert "company_provider_action" in agent_toolkit.company_tools_for(db, agent)


def test_the_agent_context_lists_only_credentials_scoped_for_it(app, client, team):
    cid = team.cid
    worker = run_as(app, client, cid, team.worker)
    assert worker.get("/context").json()["credentials"] == []
    mail = connect_mail(app, client, cid, actions=("email.*",))
    connect_mail(
        app, client, cid, actions=("social.*",), families=("marketing",), provider="social"
    )
    connect_mail(app, client, cid, actions=(), provider="unscoped")
    creds = worker.get("/context").json()["credentials"]
    assert [c["id"] for c in creds] == [
        mail.id
    ]  # the family-locked and the unscoped one are hidden
    assert creds[0]["allowed_actions"] == ["email.*"] and creds[0]["provider"] == "email"
    assert SECRET not in json.dumps(creds) and set(creds[0]) == {
        "id",
        "name",
        "provider",
        "allowed_actions",
    }
    set_family(app, team.worker["id"], "marketing")
    assert len(worker.get("/context").json()["credentials"]) == 2


def test_approvals_can_be_listed_by_tier_and_secrets_in_titles_and_previews_are_scrubbed(
    app, client, team
):
    cid = team.cid
    conn = connect_mail(app, client, cid, actions=("purchase.*", "email.*"), provider="shop")
    worker = run_as(app, client, cid, team.worker)
    secret_title = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    r = worker.post(
        "/provider-actions",
        json={
            "credential_id": conn.id,
            "action": "purchase.make",
            "params": {"amount_micros": 5, "title": secret_title, "token": "tok-secret-value-1234"},
        },
    )
    assert r.status_code == 202
    send(worker, conn)  # an external_communication approval too
    spend_only = client.get(api(cid, "/approvals"), params={"tier": "spend"}).json()["items"]
    assert [a["action"] for a in spend_only] == ["purchase.make"]
    assert secret_title not in json.dumps(spend_only)
    assert len(client.get(api(cid, "/approvals")).json()["items"]) == 2
    assert client.get(api(cid, "/approvals"), params={"tier": "bogus"}).status_code == 422
