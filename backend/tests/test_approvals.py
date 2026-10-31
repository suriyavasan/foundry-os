from __future__ import annotations

import pytest

from app.core.errors import Conflict
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.services import approvals
from tests.conftest import human
from tests.helpers import api, new_agent, set_policy
from tests.test_agent_api import AgentSession, start_run  # reuse the agent-API harness


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    founder = client.get(api(cid, "/overview")).json()["founder"]
    worker_agent = new_agent(client, cid, name="Worker")
    return cid, founder, worker_agent


def _ask(sess: AgentSession, kind="external_purchase", **extra):
    body = {"kind": kind, "title": f"Buy {kind}", "scope": {"sku": "A1", "qty": 2}, **extra}
    r = sess.post("/approvals", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_approver_is_derived_from_policy(app, client, world):
    cid, founder, worker = world
    sess = start_run(app, client, cid, worker["id"])
    assert _ask(sess, "external_purchase")["approver"] == "human"
    assert _ask(sess, "budget_increase")["approver"] == "human"
    assert _ask(sess, "hire")["approver"] == "founder"
    assert _ask(sess, "production_deploy")["approver"] == "founder"
    # widening / narrowing the list changes the derivation
    set_policy(client, cid, human_approval_required_for=["hire"])
    assert _ask(sess, "hire")["approver"] == "human"
    assert _ask(sess, "external_purchase")["approver"] == "founder"
    set_policy(client, cid, human_approval_required_for=[])
    assert _ask(sess, "budget_increase")["approver"] == "founder"
    # a supervised company wants a human for everything
    set_policy(client, cid, autonomy_mode="supervised")
    assert _ask(sess, "other")["approver"] == "human"


def test_human_decides_human_approvals_and_decision_is_final(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    created = _ask(sess, "external_purchase", amount_micros=25_000_000, vendor="Acme", risk="high")
    assert created["status"] == "pending"
    listing = client.get(api(cid, "/approvals"), params={"status": "pending", "approver": "human"})
    assert [a["id"] for a in listing.json()["items"]] == [created["id"]]
    r = client.post(
        api(cid, f"/approvals/{created['id']}/approve"), json={"note": "ok, within plan"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved" and body["decided_by_type"] == "human"
    assert body["decision_note"] == "ok, within plan" and body["decided_at"]
    assert (
        body["amount_micros"] == 25_000_000 and body["vendor"] == "Acme" and body["risk"] == "high"
    )
    for action in ("approve", "reject", "request_changes"):
        r = client.post(api(cid, f"/approvals/{created['id']}/{action}"))
        assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    other = _ask(sess, "external_purchase")
    assert client.post(api(cid, f"/approvals/{other['id']}/reject")).json()["status"] == "rejected"
    third = _ask(sess, "external_purchase")
    r = client.post(
        api(cid, f"/approvals/{third['id']}/request_changes"), json={"note": "cheaper?"}
    )
    assert r.json()["status"] == "changes_requested"


def test_only_the_founder_agent_can_decide_and_only_founder_approvals(client, client_as, world):
    from tests.test_agent_api import make_approval

    cid, founder, worker = world
    founder_kind = make_approval(client, cid, worker["id"], "hire")  # approver: founder
    human_kind = make_approval(client, cid, worker["id"], "external_purchase")  # approver: human
    as_worker = client_as(Principal(PrincipalType.AGENT, worker["id"], "W", cid))
    as_founder = client_as(Principal(PrincipalType.AGENT, founder["id"], "F", cid))
    assert as_worker.post(api(cid, f"/approvals/{founder_kind}/approve")).status_code == 403
    assert as_worker.post(api(cid, f"/approvals/{human_kind}/approve")).status_code == 403
    assert as_founder.post(api(cid, f"/approvals/{human_kind}/approve")).status_code == 403
    assert as_founder.post(api(cid, f"/approvals/{founder_kind}/approve")).status_code == 200
    # humans may decide anything (they are the ultimate authority)
    assert client.post(api(cid, f"/approvals/{human_kind}/approve")).status_code == 200


def test_founder_decides_founder_approvals_via_agent_api(app, client, world):
    cid, founder, worker = world
    worker_sess = start_run(app, client, cid, worker["id"])
    approval = _ask(worker_sess, "hire")
    assert approval["approver"] == "founder"

    founder_sess = start_run(app, client, cid, founder["id"], task_title="founder work")
    r = founder_sess.post(
        f"/approvals/{approval['id']}/decide", json={"decision": "approve", "note": "fits plan"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["decided_by_type"] == "agent"
    assert r.json()["decided_by_id"] == founder["id"]
    # the requester can poll its own approval; a stranger cannot
    assert worker_sess.get(f"/approvals/{approval['id']}").json()["status"] == "approved"
    stranger = new_agent(client, cid, name="Stranger")
    stranger_sess = start_run(app, client, cid, stranger["id"])
    assert stranger_sess.get(f"/approvals/{approval['id']}").status_code == 403
    # already decided
    r = founder_sess.post(f"/approvals/{approval['id']}/decide", json={"decision": "reject"})
    assert r.status_code == 409


def test_human_only_kinds_cannot_be_decided_by_the_founder(app, client, world):
    cid, founder, worker = world
    worker_sess = start_run(app, client, cid, worker["id"])
    approval = _ask(worker_sess, "external_purchase")
    assert approval["approver"] == "human"
    founder_sess = start_run(app, client, cid, founder["id"], task_title="founder work")
    r = founder_sess.post(f"/approvals/{approval['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    assert client.get(api(cid, f"/approvals/{approval['id']}")).json()["status"] == "pending"


def test_an_agent_can_never_decide_its_own_request(app, client, world):
    cid, founder, worker = world
    # the founder asks for something founder-approvable: M18 routes it to a person (it could never
    # decide its own request, which used to be a dead end) and it still cannot approve it itself
    founder_sess = start_run(app, client, cid, founder["id"])
    mine = _ask(founder_sess, "hire")
    assert mine["approver"] == "human"
    r = founder_sess.post(f"/approvals/{mine['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and "own request" in r.json()["error"]["message"]
    # a non-founder agent cannot decide anything
    worker_sess = start_run(app, client, cid, worker["id"])
    r = worker_sess.post(f"/approvals/{mine['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    assert client.get(api(cid, f"/approvals/{mine['id']}")).json()["status"] == "pending"


def test_consume_is_single_use_and_bound_to_scope(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    created = _ask(
        sess, "external_purchase", scope={"sku": "A1", "qty": 2, "meta": {"b": 1, "a": 2}}
    )
    ctx = CompanyContext(cid, human(_owner(app)), Role.OWNER)
    with app.state.session_factory() as db, pytest.raises(Conflict, match="not approved"):
        approvals.consume(  # still pending
            db, ctx, created["id"], {"sku": "A1", "qty": 2, "meta": {"a": 2, "b": 1}}
        )
    client.post(api(cid, f"/approvals/{created['id']}/approve"))
    with app.state.session_factory() as db, pytest.raises(Conflict, match="scope"):
        approvals.consume(  # a different action
            db, ctx, created["id"], {"sku": "A1", "qty": 3, "meta": {"a": 2, "b": 1}}
        )
    with app.state.session_factory() as db:
        used = approvals.consume(
            db, ctx, created["id"], {"meta": {"a": 2, "b": 1}, "qty": 2, "sku": "A1"}
        )
        assert used.consumed_at is not None  # key order does not matter (canonical JSON)
    with app.state.session_factory() as db, pytest.raises(Conflict, match="already"):
        approvals.consume(db, ctx, created["id"], {"sku": "A1", "qty": 2, "meta": {"a": 2, "b": 1}})
    actions = [
        e["action"]
        for e in client.get(api(cid, "/activity"), params={"target_id": created["id"]}).json()[
            "items"
        ]
    ]
    assert (
        "approval.consumed" in actions
        and "approval.requested" in actions
        and "approval.approved" in actions
    )


def test_rejected_approvals_cannot_be_consumed(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    created = _ask(sess, "external_purchase")
    client.post(api(cid, f"/approvals/{created['id']}/reject"))
    ctx = CompanyContext(cid, human(_owner(app)), Role.OWNER)
    with app.state.session_factory() as db, pytest.raises(Conflict):
        approvals.consume(db, ctx, created["id"], {"sku": "A1", "qty": 2})


def test_concurrent_consume_only_succeeds_once(app, client, world):
    import threading

    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    created = _ask(sess, "external_purchase")
    client.post(api(cid, f"/approvals/{created['id']}/approve"))
    ctx = CompanyContext(cid, human(_owner(app)), Role.OWNER)
    results: list[str] = []
    barrier = threading.Barrier(6)

    def attempt():
        with app.state.session_factory() as db:
            barrier.wait(timeout=10)
            try:
                approvals.consume(db, ctx, created["id"], {"sku": "A1", "qty": 2})
                results.append("ok")
            except Conflict:
                results.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert results.count("ok") == 1 and results.count("conflict") == 5


def test_approvals_validate_input_and_hide_secrets(app, client, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    for bad in (
        {"kind": "bogus", "title": "x"},
        {"kind": "hire", "title": ""},
        {"kind": "hire", "title": "x", "risk": "extreme"},
        {"kind": "hire", "title": "x", "recurrence": "daily"},
        {"kind": "hire", "title": "x", "amount_micros": -5},
        {"kind": "hire", "title": "x", "decided_by_id": "me"},
        {"kind": "hire", "title": "x", "scope": {"api_key": "sk-123"}},
        {"kind": "hire", "title": "x", "evidence": {"nested": {"password": "p"}}},
        {"kind": "hire", "title": "x", "task_id": "not-a-task"},
    ):
        assert sess.post("/approvals", json=bad).status_code in (404, 422), bad


def test_approvals_are_tenant_scoped(app, client, client_as, make_user, world):
    cid, _, worker = world
    sess = start_run(app, client, cid, worker["id"])
    created = _ask(sess, "external_purchase")
    outsider = client_as(human(make_user("nosy@x.test")))
    assert outsider.get(api(cid, "/approvals")).status_code == 404
    assert outsider.post(api(cid, f"/approvals/{created['id']}/approve")).status_code == 404
    other = client.post("/api/companies", json={"name": "Elsewhere"}).json()["id"]
    assert client.get(api(other, f"/approvals/{created['id']}")).status_code == 404
    assert client.post(api(other, f"/approvals/{created['id']}/approve")).status_code == 404


def _owner(app):
    from app.services.users import get_local_owner

    with app.state.session_factory() as db:
        return get_local_owner(db)
