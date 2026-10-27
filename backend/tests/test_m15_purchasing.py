"""M15 purchase requests end to end with the fake card provider: the approval state machine, card
issuance, the checkout task, mark-paid, cancel, expiry, pause / stop and retirement."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.integrations.cards.base import CardProviderError
from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.autonomy import Alert, WakeRequest
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.purchasing import AgentCard, CardTransaction, PurchaseRequest, Subscription
from app.models.work import Task
from app.services import purchasing, wallet
from tests.guardrail_helpers import run_as
from tests.helpers import api
from tests.m15_wp1_helpers import USD, make_world, purchase_body

COFOUNDER_BAND = {
    "purchase_cofounder_limit_micros": 50 * USD,
    "purchase_cofounder_monthly_micros": 200 * USD,
    "purchase_new_vendor_requires_human": False,
}


@pytest.fixture(autouse=True)
def _clean_providers():
    yield
    wallet.PROVIDER_OVERRIDES.clear()


def _row(app, purchase_id) -> PurchaseRequest:
    with app.state.session_factory() as db:
        row = db.get(PurchaseRequest, purchase_id)
        db.expunge(row)
        return row


def _audit(app, cid, action):
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action == action)
            )
        )


def _founder_decides(world, approval_id, decision="approve", note=None):
    founder = run_as(world.app, world.client, world.cid, world.team.founder, "approvals")
    return founder.post(
        f"/approvals/{approval_id}/decide", json={"decision": decision, "note": note}
    )


def _wake_texts(app, agent_id) -> list[str]:
    with app.state.session_factory() as db:
        out = []
        for w in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id)):
            out += [r["text"] for r in w.reasons]
        return out


# ---------------------------------------------------------------- the Co-founder band


def test_cofounder_approves_then_a_single_use_card_is_issued_and_a_person_gets_the_task(
    app, client, company
):
    world = make_world(app, client, company, **COFOUNDER_BAND)
    worker = world.agent()
    r = world.buy(worker)
    assert r.status_code == 201, r.text
    ack = r.json()
    assert ack["status"] == "pending_approval" and ack["decision"] == "cofounder"
    assert "Co-founder" in ack["next_step"]
    pid = ack["purchase_request_id"]
    assert world.fake.call_count("issue_card") == 0  # nothing is issued before the approval

    detail = world.purchase(pid)
    assert detail["approval"]["approver"] == "founder" and detail["held_micros"] == 0
    assert _founder_decides(world, detail["approval_id"]).status_code == 200

    after = world.purchase(pid)
    assert after["status"] == "awaiting_checkout" and after["checkout_path"] == "human_checkout"
    assert after["held_micros"] == 19 * USD and after["settled_micros"] is None
    card = after["card"]
    assert card["kind"] == "single_use" and card["status"] == "active"
    assert card["limit_micros"] == 21 * USD  # $19 + max(10 % = $1.90, $2)
    assert card["merchant_lock"]["domain"] == "plausible.io"
    # the provider got the purchase id as its idempotency key and only ids as metadata
    name, (key, limit, single) = (
        world.fake.calls[-1] if False else next(c for c in world.fake.calls if c[0] == "issue_card")
    )
    assert (key, limit, single) == (pid, 21 * USD, True)
    meta = world.fake.card_meta[card["provider_card_id"]]["metadata"]
    assert meta == {"cos_purchase": pid, "cos_company": world.cid}
    # no card number anywhere in what we return or store
    assert "pan" not in after["card"] and "number" not in after["card"]
    # the human task carries the packet and is assigned to the owner
    packet = after["checkout_packet"]
    assert packet["vendor_domain"] == "plausible.io" and packet["card_limit_micros"] == 21 * USD
    assert packet["card_last4"] == card["last4"]
    with app.state.session_factory() as db:
        task = db.get(Task, after["checkout_task_id"])
        assert task.assignee_member_id == world.owner_member_id()
        assert "plausible.io" in task.description
        # the approval was spent exactly once
        assert db.get(Approval, after["approval_id"]).consumed_at is not None
    assert "approved" in " ".join(_wake_texts(app, world.team.worker["id"]))


def test_a_changed_scope_cannot_use_the_approval(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND)
    worker = world.agent()
    pid = world.buy(worker).json()["purchase_request_id"]
    approval_id = world.purchase(pid)["approval_id"]
    with app.state.session_factory() as db:  # the amount is tampered with after the request
        db.get(PurchaseRequest, pid).amount_micros = 200 * USD
        db.commit()
    _founder_decides(world, approval_id)
    row = _row(app, pid)
    assert row.status == "issue_failed" and row.checkout_packet["_issue"]["transient"] is False
    assert world.fake.call_count("issue_card") == 0
    with app.state.session_factory() as db:
        assert db.get(Approval, approval_id).consumed_at is None
        assert db.scalar(select(Alert).where(Alert.kind == "card_issue_failed")) is not None


def test_nobody_can_approve_their_own_purchase(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND)
    worker = world.agent()
    pid = world.buy(worker).json()["purchase_request_id"]
    approval_id = world.purchase(pid)["approval_id"]
    # the requesting agent
    r = worker.post(f"/approvals/{approval_id}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    # another agent that is not the Co-founder
    other = world.agent("manager", "other")
    assert (
        other.post(f"/approvals/{approval_id}/decide", json={"decision": "approve"}).status_code
        == 403
    )
    assert world.purchase(pid)["status"] == "pending_approval"
    # the Co-founder's own request needs a person, and the Co-founder cannot decide it
    founder = run_as(app, client, world.cid, world.team.founder, "own")
    own = world.buy(founder).json()
    assert own["status"] == "pending_approval" and own["decision"] == "human"
    own_approval = world.purchase(own["purchase_request_id"])["approval_id"]
    assert _founder_decides(world, own_approval).status_code == 403
    assert (
        client.post(
            api(world.cid, f"/purchases/{own['purchase_request_id']}/approve"), json={}
        ).status_code
        == 200
    )


def test_a_rejection_reaches_the_agent_and_issues_nothing(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND)
    worker = world.agent()
    pid = world.buy(worker).json()["purchase_request_id"]
    approval_id = world.purchase(pid)["approval_id"]
    assert _founder_decides(world, approval_id, "reject", "use the free tier").status_code == 200
    row = _row(app, pid)
    assert row.status == "rejected" and row.held_micros == 0
    assert world.fake.call_count("issue_card") == 0
    assert any("free tier" in t for t in _wake_texts(app, world.team.worker["id"]))
    assert "Not approved" in worker.get(f"/purchases/{pid}").json()["next_step"]


def test_the_auto_band_issues_without_an_approval(app, client, company):
    world = make_world(
        app, client, company, **COFOUNDER_BAND, purchase_auto_approve_micros=25 * USD,
        purchase_agent_auto_monthly_micros=100 * USD,
    )  # fmt: skip
    worker = world.agent()
    ack = world.buy(worker).json()
    assert ack["decision"] == "auto" and ack["status"] == "awaiting_checkout"
    assert world.purchase(ack["purchase_request_id"])["approval_id"] is None
    assert world.fake.call_count("issue_card") == 1


def test_defaults_send_the_request_to_the_human_who_can_approve_in_the_wallet(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    pid = world.buy(worker).json()["purchase_request_id"]
    detail = world.purchase(pid)
    assert detail["decision"] == "human" and detail["status"] == "pending_approval"
    r = client.post(api(world.cid, f"/purchases/{pid}/approve"), json={"note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_checkout"
    assert client.post(api(world.cid, f"/purchases/{pid}/approve"), json={}).status_code == 409


def test_purchasing_disabled_hands_the_request_to_a_person_as_a_task(app, client, company):
    world = make_world(app, client, company, purchasing=False)
    worker = world.agent()
    ack = world.buy(worker).json()
    assert ack["status"] == "human_task" and "handed to the Founder" in ack["next_step"]
    detail = world.purchase(ack["purchase_request_id"])
    assert detail["decision"] == "report_only" and detail["held_micros"] == 0
    with app.state.session_factory() as db:
        task = db.get(Task, detail["checkout_task_id"])
        assert task.assignee_member_id == world.owner_member_id()
    assert world.fake.call_count("issue_card") == 0


# ---------------------------------------------------------------- validation, dedupe, limits


def test_request_validation_and_dedupe(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    for bad in (
        {"url": "https://localhost/pay"},
        {"url": "https://10.0.0.5/pay"},
        {"url": "https://169.254.169.254/latest"},
        {"url": "https://intranet/pay"},
        {"url": "http://plausible.io/"},
        {"item": "Plan 4242 4242 4242 4242"},
        {"amount_usd": "0"},
        {"original_amount": "10"},  # needs a currency too
    ):
        assert world.buy(worker, **bad).status_code in (422,), bad
    first = world.buy(worker).json()
    again = world.buy(worker).json()
    assert again["purchase_request_id"] == first["purchase_request_id"]
    with app.state.session_factory() as db:
        assert len(list(db.scalars(select(PurchaseRequest)))) == 1


def test_an_agent_buys_only_for_its_own_task(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    other_task = client.post(api(world.cid, "/tasks"), json={"title": "someone else's"}).json()
    r = worker.post("/purchases", json=purchase_body(other_task["id"]))
    assert r.status_code == 403
    r = worker.post("/purchases", json=purchase_body("no-such-task"))
    assert r.status_code == 404


def test_purchase_requests_are_rate_limited_per_agent(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    for i in range(5):
        r = world.buy(worker, item=f"Tool number {i} for the team", vendor=f"Vendor {i}")
        assert r.status_code == 201, r.text
    r = world.buy(worker, item="One tool too many", vendor="Vendor 6")
    assert r.status_code == 429


def test_the_money_is_integer_micros_from_decimal_dollars(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    pid = world.buy(worker, amount_usd="19.99").json()["purchase_request_id"]
    assert world.purchase(pid)["amount_micros"] == 19_990_000


# ---------------------------------------------------------------- issuing failures


def test_a_transient_provider_error_backs_off_and_then_succeeds_with_one_card(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND, purchase_auto_approve_micros=25 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD)  # fmt: skip
    world.fake.fail_next("issue_card", transient=True)
    worker = world.agent()
    pid = world.buy(worker).json()["purchase_request_id"]
    row = _row(app, pid)
    assert row.status == "issue_failed" and row.checkout_packet["_issue"]["transient"] is True
    with app.state.session_factory() as db:
        assert purchasing.expire_stale(db, world.cid, now=utcnow()) == 0  # not yet due
    assert _row(app, pid).status == "issue_failed"
    with app.state.session_factory() as db:
        purchasing.expire_stale(db, world.cid, now=utcnow() + timedelta(minutes=5))
    after = world.purchase(pid)
    assert after["status"] == "awaiting_checkout"
    assert len(world.fake.cards) == 1
    # calling the job again changes nothing (idempotent)
    with app.state.session_factory() as db:
        purchasing.issue_card(db, pid)
    assert len(world.fake.cards) == 1 and len(_cards(app)) == 1


def _cards(app):
    with app.state.session_factory() as db:
        return list(db.scalars(select(AgentCard)))


def test_a_permanent_provider_error_alerts_and_never_tries_another_account(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND, purchase_auto_approve_micros=25 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD)  # fmt: skip
    # a second, healthy account must not be used as a fallback
    second = make_world(app, client, company, provider="none", purchasing=False)
    assert second.fake is None
    world.fake.fail_next("issue_card", transient=False)
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    row = _row(app, pid)
    assert row.status == "issue_failed" and row.checkout_packet["_issue"]["transient"] is False
    with app.state.session_factory() as db:
        alert = db.scalar(select(Alert).where(Alert.kind == "card_issue_failed"))
        assert alert is not None and alert.severity == "critical"
        # the scheduler pass does not retry a permanent failure
        purchasing.expire_stale(db, world.cid, now=utcnow() + timedelta(days=1))
    assert world.fake.call_count("issue_card") == 1


def test_the_provider_failure_message_never_carries_the_provider_secret(app, client, company):
    world = make_world(app, client, company, **COFOUNDER_BAND, purchase_auto_approve_micros=25 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD)  # fmt: skip
    world.fake.fail_next("issue_card", CardProviderError("boom sk_live_abcdefghijklmnop1234"))
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    assert "sk_live_abcdefghijklmnop1234" not in str(_row(app, pid).checkout_packet)


# ---------------------------------------------------------------- manual mode and mark paid


def test_manual_mode_mark_paid_records_a_verified_human_expense(app, client, company):
    world = make_world(app, client, company, provider="manual")
    worker = world.agent()
    pid = world.buy(worker, amount_usd="19.00").json()["purchase_request_id"]
    client.post(api(world.cid, f"/purchases/{pid}/approve"), json={})
    detail = world.purchase(pid)
    assert detail["status"] == "awaiting_checkout" and detail["checkout_path"] == "human_buys"
    assert detail["card"] is None and detail["checkout_packet"]["card_last4"] == "1234"
    r = client.post(
        api(world.cid, f"/purchases/{pid}/mark-paid"),
        json={"merchant_amount_usd": "20.50", "billed_amount_inr": "1725.40"},
    )
    assert r.status_code == 200, r.text
    paid = r.json()
    assert paid["status"] == "settled" and paid["settled_micros"] == 20_500_000
    assert paid["cost_kind"] == "verified" and paid["held_micros"] == 0
    txn = paid["transactions"][0]
    assert txn["source"] == "human" and txn["cost_kind"] == "verified"
    with app.state.session_factory() as db:
        expense = db.scalar(
            select(ExternalExpense).where(ExternalExpense.purchase_request_id == pid)
        )
        assert (expense.cost_kind, expense.source, expense.amount_micros) == (
            "verified",
            "human",
            20_500_000,
        )
        raw = db.scalar(select(CardTransaction)).raw
        assert raw["billed_amount_inr"] == "1725.40"  # stored, never converted
        assert db.get(Task, detail["checkout_task_id"]).status == "done"
    assert any("paid" in t for t in _wake_texts(app, world.team.worker["id"]))
    # once only
    r = client.post(
        api(world.cid, f"/purchases/{pid}/mark-paid"), json={"merchant_amount_usd": "20"}
    )
    assert r.status_code == 409


def test_marking_a_recurring_purchase_paid_starts_a_subscription(app, client, company):
    world = make_world(app, client, company, provider="manual")
    worker = world.agent()
    pid = world.buy(worker, recurring="monthly").json()["purchase_request_id"]
    client.post(api(world.cid, f"/purchases/{pid}/approve"), json={})
    client.post(api(world.cid, f"/purchases/{pid}/mark-paid"), json={"merchant_amount_usd": "19"})
    with app.state.session_factory() as db:
        sub = db.scalar(select(Subscription))
        assert sub is not None and sub.recurrence == "monthly" and sub.purchase_request_id == pid


def test_a_card_issued_purchase_cannot_be_marked_paid_by_hand(app, client, company):
    world = make_world(app, client, company, purchase_auto_approve_micros=25 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD, purchase_new_vendor_requires_human=False)  # fmt: skip
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    r = client.post(
        api(world.cid, f"/purchases/{pid}/mark-paid"), json={"merchant_amount_usd": "19"}
    )
    assert r.status_code == 409  # the card provider sees the payment; no double count


def test_only_the_checkout_person_or_an_admin_marks_paid(
    app, client, company, make_user, add_member, client_as
):
    from tests.conftest import human

    world = make_world(app, client, company, provider="manual")
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    client.post(api(world.cid, f"/purchases/{pid}/approve"), json={})
    viewer = make_user("viewer@example.com")
    add_member(world.cid, viewer.id, "viewer")
    r = client_as(human(viewer)).post(
        api(world.cid, f"/purchases/{pid}/mark-paid"), json={"merchant_amount_usd": "19"}
    )
    assert r.status_code == 403


# ---------------------------------------------------------------- cancel, expiry, stop, retire


def _issued(app, client, company, **extra):
    world = make_world(
        app, client, company, **COFOUNDER_BAND, purchase_auto_approve_micros=25 * USD,
        purchase_agent_auto_monthly_micros=100 * USD, **extra,
    )  # fmt: skip
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    assert world.purchase(pid)["status"] == "awaiting_checkout"
    return world, pid


def test_cancel_closes_the_card_releases_the_hold_and_wakes_the_agent(app, client, company):
    world, pid = _issued(app, client, company)
    detail = world.purchase(pid)
    r = client.post(api(world.cid, f"/purchases/{pid}/cancel"), json={"reason": "not needed"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["held_micros"] == 0
    assert world.fake.cards[detail["card"]["provider_card_id"]].status == "closed"
    with app.state.session_factory() as db:
        assert db.get(Task, detail["checkout_task_id"]).status == "cancelled"
    assert any("cancelled" in t for t in _wake_texts(app, world.team.worker["id"]))
    assert client.post(api(world.cid, f"/purchases/{pid}/cancel"), json={}).status_code == 409


def test_cancel_stays_open_when_the_provider_will_not_close_the_card(app, client, company):
    world, pid = _issued(app, client, company)
    world.fake.fail_next("close", transient=True)
    assert client.post(api(world.cid, f"/purchases/{pid}/cancel"), json={}).status_code == 409
    assert world.purchase(pid)["status"] == "awaiting_checkout"


def test_an_unused_card_is_closed_when_its_time_is_up(app, client, company):
    world, pid = _issued(app, client, company)
    card = world.purchase(pid)["card"]
    with app.state.session_factory() as db:
        n = purchasing.expire_stale(db, world.cid, now=utcnow() + timedelta(hours=73))
    assert n == 1
    after = world.purchase(pid)
    assert after["status"] == "expired" and after["held_micros"] == 0
    assert world.fake.cards[card["provider_card_id"]].status == "closed"


def test_an_undecided_approval_expires_after_seven_days(app, client, company):
    world = make_world(app, client, company)
    pid = world.buy(world.agent()).json()["purchase_request_id"]
    with app.state.session_factory() as db:
        assert purchasing.expire_stale(db, world.cid, now=utcnow() + timedelta(days=6)) == 0
        assert purchasing.expire_stale(db, world.cid, now=utcnow() + timedelta(days=8)) == 1
    assert world.purchase(pid)["status"] == "expired"
    assert world.purchase(pid)["approval"]["status"] == "expired"


def test_the_company_pause_and_emergency_stop_block_and_freeze(app, client, company):
    world, pid = _issued(app, client, company)
    card = world.purchase(pid)["card"]
    assert client.post(api(world.cid, "/pause")).status_code in (200, 204)
    assert world.fake.cards[card["provider_card_id"]].status == "frozen"
    cards = client.get(api(world.cid, "/wallet/cards")).json()["items"]
    assert cards[0]["status"] == "frozen"
    # a new request while paused is refused, nothing issued
    worker = world.agent("manager", "paused")
    r = world.buy(worker, vendor="Another", item="Another thing to buy")
    assert r.status_code == 201 and r.json()["status"] == "rejected"
    # resuming does not unfreeze; unfreezing is a person's call
    assert client.post(api(world.cid, "/resume")).status_code in (200, 204)
    assert client.get(api(world.cid, "/wallet/cards")).json()["items"][0]["status"] == "frozen"
    r = client.post(api(world.cid, f"/wallet/cards/{cards[0]['id']}/unfreeze"), json={})
    assert r.status_code == 200 and r.json()["status"] == "active"


def test_emergency_stop_freezes_every_agent_card_and_issuance_waits(app, client, company):
    world, pid = _issued(app, client, company)
    card = world.purchase(pid)["card"]
    assert client.post("/api/system/emergency-stop", json={"reason": "test"}).status_code == 200
    assert world.fake.cards[card["provider_card_id"]].status == "frozen"
    assert client.get(api(world.cid, "/wallet/cards")).json()["items"][0]["status"] == "frozen"
    r = client.post(api(world.cid, f"/wallet/cards/{card['id']}/unfreeze"), json={})
    assert r.status_code == 409  # not while the stop is on
    client.post("/api/system/resume")
    assert client.get(api(world.cid, "/wallet/cards")).json()["items"][0]["status"] == "frozen"


def test_retiring_an_agent_cancels_its_unpaid_purchases(app, client, company):
    world, pid = _issued(app, client, company)
    card = world.purchase(pid)["card"]
    r = client.post(api(world.cid, f"/agents/{world.team.worker['id']}/retire"), json={})
    assert r.status_code == 200, r.text
    assert world.purchase(pid)["status"] == "cancelled"
    assert world.fake.cards[card["provider_card_id"]].status == "closed"
    with app.state.session_factory() as db:
        assert db.get(Agent, world.team.worker["id"]).status == "retired"


def test_audit_trail_has_the_state_changes(app, client, company):
    world, pid = _issued(app, client, company)
    for action in (
        "purchase.requested",
        "purchase.approved",
        "card.issued",
        "purchase.awaiting_checkout",
    ):
        assert _audit(app, world.cid, action), action
