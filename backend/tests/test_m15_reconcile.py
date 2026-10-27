"""Reconciliation of provider transactions into the ledger, against the stateful fake provider:
matching, verified expenses (once), refunds, anomalies that freeze a card, declines, balance alerts,
tenancy, the scheduler passes and the card webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.integrations.cards import registry
from app.integrations.cards.base import CardProviderError
from app.integrations.cards.fake import FakeCardProvider
from app.models.audit import AuditEvent
from app.models.autonomy import Alert
from app.models.guardrails import ExternalExpense
from app.models.purchasing import (
    AgentCard,
    CardAccount,
    CardTransaction,
    PurchaseRequest,
    Subscription,
)
from app.models.run import CompanyEvent
from app.models.work import Task
from app.services import card_reconcile
from tests.helpers import FakeClock, api, new_agent, new_task
from tests.m8_helpers import connect
from tests.m15_helpers import USD, issue_for, make_account, make_purchase


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry.clear_test_providers()


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    clock = FakeClock()
    provider = FakeCardProvider(funded_micros=1000 * USD, now=clock)
    with app.state.session_factory() as db:
        account = make_account(db, cid, created_at=clock() - timedelta(days=1))
        purchase = make_purchase(db, cid, agent["id"], task["id"], checkout_task_id=task["id"])
        issued = issue_for(db, provider, account, purchase)
        ids = SimpleNamespace(
            account=account.id,
            purchase=purchase.id,
            card=issued.card.id,
            pcard=issued.issued.provider_card_id,
        )
    w = SimpleNamespace(
        app=app,
        client=client,
        cid=cid,
        agent=agent,
        task=task,
        clock=clock,
        provider=provider,
        ids=ids,
    )

    def sync(company_id=None, factory=None):
        with app.state.session_factory() as db:
            return card_reconcile.sync_company(
                db,
                company_id or cid,
                now=clock(),
                provider_factory=factory or (lambda db, acc: provider),
            )

    def db():
        return app.state.session_factory()

    w.sync, w.db = sync, db
    return w


def alerts_of(db, cid, kind=None):
    stmt = select(Alert).where(Alert.company_id == cid)
    if kind:
        stmt = stmt.where(Alert.kind == kind)
    return list(db.scalars(stmt))


def expenses(db, cid):
    return list(db.scalars(select(ExternalExpense).where(ExternalExpense.company_id == cid)))


# ------------------------------------------------------------------ the happy path


def test_authorization_marks_the_purchase_authorized_and_finishes_the_checkout_task(world):
    w = world
    assert w.client.get(api(w.cid, f"/tasks/{w.task['id']}")).json()["status"] == "ready"
    auth = w.provider.authorize(w.ids.pcard, 21 * USD, merchant_name="PLAUSIBLE.IO")
    result = w.sync()
    assert (result.accounts_synced, result.transactions_new, result.errors) == (1, 1, [])
    with w.db() as db:
        purchase = db.get(PurchaseRequest, w.ids.purchase)
        assert purchase.status == "authorized"
        assert (
            purchase.cost_kind is None and purchase.settled_micros is None
        )  # a hold, not a charge
        assert purchase.held_micros == 20 * USD
        row = db.scalar(
            select(CardTransaction).where(CardTransaction.provider_txn_id == auth.provider_txn_id)
        )
        assert (row.match_status, row.purchase_request_id, row.source) == (
            "matched",
            w.ids.purchase,
            "provider",
        )
        assert expenses(db, w.cid) == []  # an authorization is never an expense
        assert db.get(Task, w.task["id"]).status == "done"
        actions = {
            a.action for a in db.scalars(select(AuditEvent).where(AuditEvent.company_id == w.cid))
        }
        assert "purchase.authorized" in actions


def test_capture_settles_the_purchase_once_with_a_verified_card_expense(world):
    w = world
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="PLAUSIBLE.IO")
    cap = w.provider.capture(auth.provider_txn_id, 21 * USD)
    first = w.sync()
    second = w.sync()  # replay: nothing changes
    third = w.sync()
    assert (
        first.transactions_new == 2 and second.transactions_new == 0 and third.transactions_new == 0
    )
    with w.db() as db:
        purchase = db.get(PurchaseRequest, w.ids.purchase)
        assert purchase.status == "settled" and purchase.settled_micros == 21 * USD
        assert purchase.cost_kind == "verified" and purchase.held_micros == 0
        [expense] = expenses(db, w.cid)
        assert (expense.amount_micros, expense.cost_kind, expense.source) == (
            21 * USD,
            "verified",
            "card",
        )
        assert expense.recurrence == "one_time"  # finance would project a recurring row again
        assert expense.purchase_request_id == w.ids.purchase
        txn = db.scalar(
            select(CardTransaction).where(CardTransaction.provider_txn_id == cap.provider_txn_id)
        )
        assert expense.card_transaction_id == txn.id and txn.match_status == "matched"
        assert db.scalar(select(func.count(CardTransaction.id))) == 2
        # no card number anywhere in what we stored
        blob = json.dumps([t.raw for t in db.scalars(select(CardTransaction))])
        assert not re.search(r"\d{13,}", blob)
        audit = " ".join(json.dumps(a.data) + a.summary for a in db.scalars(select(AuditEvent)))
        assert not re.search(r"\d{13,}", audit)


def test_single_use_card_is_closed_after_the_first_capture_even_if_the_provider_does_not(world):
    w = world
    w.provider.emulate_single_use = False  # Mercury-like: nothing closes the card but us
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id)
    w.sync()
    assert w.provider.cards[w.ids.pcard].status == "closed"
    with w.db() as db:
        card = db.get(AgentCard, w.ids.card)
        assert card.status == "closed" and card.closed_at is not None


def test_a_failed_close_is_retried_on_the_next_sync(world):
    w = world
    w.provider.emulate_single_use = False
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id)
    w.provider.fail_next("close", transient=True)
    w.sync()
    with w.db() as db:
        assert db.get(AgentCard, w.ids.card).status == "active"
        assert db.get(PurchaseRequest, w.ids.purchase).status == "settled"
    w.sync()
    with w.db() as db:
        assert db.get(AgentCard, w.ids.card).status == "closed"


def test_a_refund_is_a_negative_verified_expense_linked_to_the_purchase(world):
    w = world
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    cap = w.provider.capture(auth.provider_txn_id)
    w.provider.refund(cap.provider_txn_id, 5 * USD)
    w.sync()
    w.sync()
    with w.db() as db:
        amounts = sorted(e.amount_micros for e in expenses(db, w.cid))
        assert amounts == [-5 * USD, 20 * USD]
        assert all(
            e.cost_kind == "verified" and e.purchase_request_id == w.ids.purchase
            for e in expenses(db, w.cid)
        )
        assert db.get(PurchaseRequest, w.ids.purchase).settled_micros == 15 * USD


def test_pagination_ingests_every_page(world):
    w = world
    w.provider.page_size = 2
    for _ in range(5):
        w.provider.authorize(w.ids.pcard, 1 * USD, merchant_name="Plausible")
    result = w.sync()
    assert result.transactions_seen == 5 and result.transactions_new == 5


# ------------------------------------------------------------------ anomalies


def test_a_transaction_on_a_card_with_no_request_freezes_it_and_alerts_critically(world):
    w = world
    with w.db() as db:
        orphan_issue = w.provider.issue_card(
            limit_micros=50 * USD,
            limit_interval="monthly",
            merchant_lock=None,
            single_use=False,
            expires_at=None,
            idempotency_key="orphan",
            metadata={},
        )
        db.add(
            AgentCard(
                company_id=w.cid,
                card_account_id=w.ids.account,
                provider_card_id=orphan_issue.provider_card_id,
                kind="single_use",
                status="active",
                last4=orphan_issue.last4,
                limit_micros=50 * USD,
                limit_interval="monthly",
                merchant_lock={},
            )
        )
        db.commit()
    auth = w.provider.authorize(orphan_issue.provider_card_id, 12 * USD, merchant_name="Somebody")
    w.provider.capture(auth.provider_txn_id)
    w.sync()
    w.sync()
    assert w.provider.cards[orphan_issue.provider_card_id].status == "frozen"
    with w.db() as db:
        [alert] = alerts_of(db, w.cid, "card_unmatched_transaction")
        assert alert.severity == "critical"
        txn = db.scalars(select(CardTransaction).where(CardTransaction.kind == "capture")).one()
        assert txn.match_status == "unmatched"
        [expense] = expenses(db, w.cid)  # the money left: the ledger says so
        assert expense.purchase_request_id is None and expense.cost_kind == "verified"
    assert w.provider.call_count("freeze") == 1  # not re-frozen on every sync


def test_a_merchant_that_does_not_look_like_the_vendor_freezes_the_card(world):
    w = world
    w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="TOTALLY OTHER SHOP LLC")
    w.sync()
    assert w.provider.cards[w.ids.pcard].status == "frozen"
    with w.db() as db:
        [alert] = alerts_of(db, w.cid, "card_merchant_mismatch")
        assert alert.severity == "critical"
        assert db.scalars(select(CardTransaction)).one().match_status == "mismatch"
        card = db.get(AgentCard, w.ids.card)
        assert card.status == "frozen" and card.frozen_reason == "merchant_mismatch"


@pytest.mark.parametrize(
    "name", ["PLAUSIBLE.IO", "PAYPAL *PLAUSIBLE", "Plausible Insights OU", None]
)
def test_vendor_like_merchants_do_not_trip_the_mismatch_check(world, name):
    w = world
    w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name=name)
    w.sync()
    assert w.provider.cards[w.ids.pcard].status == "active"
    with w.db() as db:
        assert alerts_of(db, w.cid, "card_merchant_mismatch") == []


def test_a_charge_above_the_approved_amount_plus_tolerance_freezes_the_card(world):
    w = world
    w.provider.emulate_single_use = False  # the provider lets the over-charge through
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id, 30 * USD)  # approved 20 + tolerance 2 = 22
    w.sync()
    assert w.provider.cards[w.ids.pcard].status == "frozen"
    with w.db() as db:
        [alert] = alerts_of(db, w.cid, "card_over_approved")
        assert alert.severity == "critical"
        assert db.get(AgentCard, w.ids.card).status == "frozen"  # not closed: a person looks at it
        assert db.get(PurchaseRequest, w.ids.purchase).settled_micros == 30 * USD
        assert expenses(db, w.cid)[0].amount_micros == 30 * USD  # the money did leave


def test_a_charge_inside_the_tolerance_is_fine(world):
    w = world
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id, 21_500_000)  # tax: within max(10%, $2)
    w.sync()
    with w.db() as db:
        assert alerts_of(db, w.cid) == []
        assert db.get(PurchaseRequest, w.ids.purchase).status == "settled"


def test_a_second_charge_on_a_single_use_card_freezes_it(world):
    w = world
    w.provider.emulate_single_use = False
    first = w.provider.authorize(w.ids.pcard, 10 * USD, merchant_name="Plausible")
    w.provider.capture(first.provider_txn_id)
    second = w.provider.authorize(w.ids.pcard, 5 * USD, merchant_name="Plausible")
    w.provider.capture(second.provider_txn_id)
    w.sync()
    assert w.provider.cards[w.ids.pcard].status == "frozen"
    with w.db() as db:
        assert [a.kind for a in alerts_of(db, w.cid, "card_second_charge")] == [
            "card_second_charge"
        ]
        assert len(expenses(db, w.cid)) == 2  # both are real charges


def test_a_card_foundry_did_not_issue_is_shown_but_never_an_expense_or_frozen(world):
    w = world
    other = w.provider.issue_card(
        limit_micros=50 * USD,
        limit_interval="monthly",
        merchant_lock=None,
        single_use=False,
        expires_at=None,
        idempotency_key="owner-made",
        metadata={},
    )
    auth = w.provider.authorize(other.provider_card_id, 9 * USD, merchant_name="Owner Shop")
    w.provider.capture(auth.provider_txn_id)
    w.sync()
    assert w.provider.cards[other.provider_card_id].status == "active"
    with w.db() as db:
        assert expenses(db, w.cid) == []
        assert {t.match_status for t in db.scalars(select(CardTransaction))} == {"unmatched"}
        [alert] = alerts_of(db, w.cid, "card_unknown_card")
        assert alert.severity == "info"


# ------------------------------------------------------------------ declines


def test_an_insufficient_funds_decline_raises_the_low_balance_alert_and_wakes_the_agent(world):
    w = world
    w.provider.funded_micros = 0
    w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.sync()
    w.sync()
    with w.db() as db:
        [alert] = alerts_of(db, w.cid, "wallet_low_balance")
        assert alert.severity == "critical" and "Wallet" in alert.body
        txn = db.scalars(select(CardTransaction)).one()
        assert (txn.kind, txn.status) == ("decline", "declined")
        assert db.get(PurchaseRequest, w.ids.purchase).status == "awaiting_checkout"
        from app.models.autonomy import WakeRequest

        wakes = list(db.scalars(select(WakeRequest).where(WakeRequest.agent_id == w.agent["id"])))
        assert wakes and "declined" in json.dumps(wakes[0].reasons)


def test_other_declines_alert_as_warnings_and_a_frozen_card_decline_is_quiet(world):
    w = world
    w.provider.authorize(w.ids.pcard, 500 * USD, merchant_name="Plausible")  # over the card limit
    w.sync()
    with w.db() as db:
        [alert] = alerts_of(db, w.cid, "card_declined")
        assert alert.severity == "warning"
    w.provider.freeze(w.ids.pcard)
    w.provider.authorize(w.ids.pcard, 5 * USD, merchant_name="Plausible")
    w.sync()
    with w.db() as db:
        assert len(alerts_of(db, w.cid, "card_declined")) == 1  # card_inactive adds nothing


# ------------------------------------------------------------------ failures and tenancy


def test_a_provider_failure_marks_the_account_and_recovers_on_the_next_sync(world):
    w = world
    w.provider.fail_next(
        "list_transactions", CardProviderError("upstream 503 sk_live_" + "Ab12" * 6, transient=True)
    )
    result = w.sync()
    assert result.accounts_synced == 0 and len(result.errors) == 1
    assert "sk_live_" not in result.errors[0]  # a key-shaped string never reaches the record
    with w.db() as db:
        account = db.get(CardAccount, w.ids.account)
        assert account.status == "attention" and account.last_error
        assert "sk_live_" not in account.last_error
        assert len(alerts_of(db, w.cid, "card_sync_failed")) == 1
    w.provider.authorize(w.ids.pcard, 1 * USD, merchant_name="Plausible")
    assert w.sync().accounts_synced == 1  # an attention account is still polled
    with w.db() as db:
        account = db.get(CardAccount, w.ids.account)
        assert account.status == "active" and account.last_error is None
        assert alerts_of(db, w.cid, "card_sync_failed")[0].status == "resolved"


def test_one_accounts_failure_does_not_stop_the_others(world):
    w = world
    with w.db() as db:
        second = make_account(
            db, w.cid, label="Second", is_default=False, created_at=w.clock() - timedelta(days=1)
        )
        second_id = second.id
    other = FakeCardProvider(funded_micros=100 * USD, now=w.clock)
    other.fail_next("list_transactions", transient=False)
    by_account = {w.ids.account: w.provider, second_id: other}
    w.provider.authorize(w.ids.pcard, 1 * USD, merchant_name="Plausible")
    result = w.sync(factory=lambda db, acc: by_account[acc.id])
    assert result.accounts_synced == 1 and len(result.errors) == 1
    assert result.transactions_new == 1


def test_manual_and_disabled_accounts_are_never_polled(world):
    w = world
    with w.db() as db:
        make_account(db, w.cid, provider="manual", label="Own", is_default=False)
        make_account(db, w.cid, status="disabled", label="Off", is_default=False)
    calls = []
    result = w.sync(factory=lambda db, acc: calls.append(acc.id) or w.provider)
    assert calls == [w.ids.account] and result.accounts_synced == 1


def test_another_companys_accounts_are_never_touched(app, client, world):
    w = world
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    other_agent = new_agent(client, other["id"])
    other_task = new_task(client, other["id"], assignee_agent_id=other_agent["id"])
    other_provider = FakeCardProvider(funded_micros=500 * USD, now=w.clock)
    with w.db() as db:
        account = make_account(db, other["id"], created_at=w.clock() - timedelta(days=1))
        purchase = make_purchase(db, other["id"], other_agent["id"], other_task["id"])
        issued = issue_for(db, other_provider, account, purchase)
        other_ids = (account.id, purchase.id, issued.issued.provider_card_id)
    auth = other_provider.authorize(other_ids[2], 20 * USD, merchant_name="Plausible")
    other_provider.capture(auth.provider_txn_id)
    w.provider.authorize(w.ids.pcard, 1 * USD, merchant_name="Plausible")
    w.sync()  # syncs OUR company only
    assert other_provider.call_count("list_transactions") == 0
    with w.db() as db:
        assert db.get(PurchaseRequest, other_ids[1]).status == "awaiting_checkout"
        assert (
            db.scalar(
                select(func.count(CardTransaction.id)).where(
                    CardTransaction.company_id == other["id"]
                )
            )
            == 0
        )
    # a provider card id that equals one in the other company never matches across tenants
    with w.db() as db:
        clash = AgentCard(
            company_id=w.cid,
            card_account_id=w.ids.account,
            provider_card_id="shared",
            last4="0001",
            kind="single_use",
            status="active",
            limit_micros=USD,
            limit_interval="monthly",
            merchant_lock={},
            purchase_request_id=other_ids[1],
        )
        db.add(clash)
        db.commit()
    shared = FakeCardProvider(now=w.clock)
    shared.cards["shared"] = w.provider.cards[w.ids.pcard].__class__(
        **{**w.provider.cards[w.ids.pcard].__dict__, "provider_card_id": "shared"}
    )
    shared.authorize("shared", 3 * USD, merchant_name="Plausible")
    w.sync(factory=lambda db, acc: shared)
    with w.db() as db:
        assert (
            db.get(PurchaseRequest, other_ids[1]).status == "awaiting_checkout"
        )  # foreign id ignored


# ------------------------------------------------------------------ recurring purchases


def test_a_recurring_purchase_creates_a_subscription_and_keeps_its_card_open(world):
    w = world
    with w.db() as db:
        p2 = make_purchase(
            db,
            w.cid,
            w.agent["id"],
            w.task["id"],
            recurrence="monthly",
            vendor="Linear",
            domain="linear.app",
            amount=10 * USD,
        )
        issued = issue_for(
            db, w.provider, db.get(CardAccount, w.ids.account), p2, kind="subscription"
        )
        pcard, p2_id, card_id = issued.issued.provider_card_id, p2.id, issued.card.id
    auth = w.provider.authorize(pcard, 10 * USD, merchant_name="LINEAR.APP")
    w.provider.capture(auth.provider_txn_id)
    w.sync()
    w.sync()
    with w.db() as db:
        sub = db.scalars(select(Subscription)).one()
        purchase = db.get(PurchaseRequest, p2_id)
        card = db.get(AgentCard, card_id)
        assert (sub.status, sub.recurrence, sub.vendor, sub.amount_micros) == (
            "active",
            "monthly",
            "Linear",
            10 * USD,
        )
        assert purchase.subscription_id == sub.id and card.subscription_id == sub.id
        assert sub.card_id == card_id and card.status == "active"  # not closed
        assert sub.next_renewal_on > sub.started_on
        assert len(expenses(db, w.cid)) == 1


def test_a_renewal_charge_advances_the_date_without_a_second_subscription(world):
    w = world
    with w.db() as db:
        p2 = make_purchase(
            db,
            w.cid,
            w.agent["id"],
            w.task["id"],
            recurrence="monthly",
            vendor="Linear",
            domain="linear.app",
            amount=10 * USD,
        )
        issued = issue_for(
            db, w.provider, db.get(CardAccount, w.ids.account), p2, kind="subscription"
        )
        pcard = issued.issued.provider_card_id
    a1 = w.provider.authorize(pcard, 10 * USD, merchant_name="LINEAR.APP")
    w.provider.capture(a1.provider_txn_id)
    w.sync()
    with w.db() as db:
        first_due = db.scalars(select(Subscription)).one().next_renewal_on
    w.clock.advance(days=30)
    a2 = w.provider.authorize(pcard, 11 * USD, merchant_name="LINEAR.APP")  # tax: inside tolerance
    w.provider.capture(a2.provider_txn_id)
    w.sync()
    with w.db() as db:
        sub = db.scalars(select(Subscription)).one()
        assert sub.next_renewal_on > first_due and sub.last_charge_micros == 11 * USD
        assert len(expenses(db, w.cid)) == 2
        assert all(e.purchase_request_id == sub.purchase_request_id for e in expenses(db, w.cid))


# ------------------------------------------------------------------ balance


def test_balance_check_alerts_when_funds_do_not_cover_holds_and_resolves_when_they_do(world):
    w = world
    factory = lambda db, acc: w.provider  # noqa: E731
    w.provider.funded_micros = 25 * USD  # holds $20 + buffer $10 > $25
    with w.db() as db:
        assert card_reconcile.check_balances(db, now=w.clock(), provider_factory=factory) == 1
        account = db.get(CardAccount, w.ids.account)
        assert account.last_balance_micros == 25 * USD and account.balance_checked_at is not None
        [alert] = alerts_of(db, w.cid, "wallet_low_balance")
        assert alert.status == "open" and alert.severity == "warning"
        assert (
            card_reconcile.check_balances(db, now=w.clock(), provider_factory=factory) == 0
        )  # dedupe
    w.provider.fund(500 * USD)
    with w.db() as db:
        card_reconcile.check_balances(db, now=w.clock(), provider_factory=factory)
        assert alerts_of(db, w.cid, "wallet_low_balance")[0].status == "resolved"


def test_a_failing_balance_call_is_survived(world):
    w = world
    w.provider.fail_next("balance", transient=True)
    with w.db() as db:
        assert (
            card_reconcile.check_balances(
                db, now=w.clock(), provider_factory=lambda d, a: w.provider
            )
            == 0
        )


# ------------------------------------------------------------------ the scheduler


def test_the_scheduler_runs_the_card_passes_on_their_intervals(world, scheduler, clock):
    w = world
    registry.register_test_provider(w.ids.account, w.provider)
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id)
    report = scheduler.tick(clock())
    assert set(report.cards) == {"sync", "purchases", "balance", "subscriptions"}
    with w.db() as db:
        assert db.get(PurchaseRequest, w.ids.purchase).status == "settled"
    assert scheduler.tick(clock()).cards == {}  # nothing due again yet
    clock.advance(minutes=16)
    assert set(scheduler.tick(clock()).cards) == {"sync", "purchases"}
    clock.advance(hours=2)
    assert set(scheduler.tick(clock()).cards) == {"sync", "purchases", "balance"}
    clock.advance(days=2)
    assert set(scheduler.tick(clock()).cards) == {"sync", "purchases", "balance", "subscriptions"}


def test_a_crashing_card_pass_never_breaks_the_tick(world, scheduler, clock, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(card_reconcile, "run_all", boom)
    monkeypatch.setattr("app.services.subscriptions.check", boom)
    monkeypatch.setattr("app.services.purchasing.expire_stale", boom)
    report = scheduler.tick(clock())
    assert "sync" not in report.cards and "subscriptions" not in report.cards
    assert report.cards.get("purchases") == 0  # per-company failures are contained
    assert "balance" in report.cards  # the other passes still ran
    assert report.dispatch is not None


def test_the_card_passes_are_skipped_during_the_emergency_stop(world, scheduler, clock):
    w = world
    registry.register_test_provider(w.ids.account, w.provider)
    r = w.client.post("/api/system/emergency-stop", json={"reason": "test"})
    assert r.status_code == 200, r.text
    w.provider.authorize(w.ids.pcard, 1 * USD, merchant_name="Plausible")
    assert scheduler.tick(clock()).cards == {}
    assert w.provider.call_count("list_transactions") == 0
    assert w.client.post("/api/system/resume").status_code == 200
    assert "sync" in scheduler.tick(clock()).cards


# ------------------------------------------------------------------ the card webhook


WHSEC = "whsec_" + "Qw3rTy9u" * 3
SK = "rk_test_" + "AbCd1234" * 4


def stripe_header(body: bytes, secret: str, stamp: int | None = None) -> dict:
    stamp = int(time.time()) if stamp is None else stamp
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={stamp},v1={digest}", "content-type": "application/json"}


@pytest.fixture
def stripe_world(world):
    """The world's account becomes a stripe_issuing account with a connected account and a webhook
    secret. The Stripe connection check is answered by a mock transport."""
    w = world

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/balance":
            return httpx.Response(200, json={"livemode": False, "issuing": {"available": []}})
        return httpx.Response(200, json={"data": []})

    w.app.state.integrations.http.transport = httpx.MockTransport(answer)
    acc = connect(w.client, w.cid, "stripe_issuing", secret=SK, extras={"webhook_secret": WHSEC})
    assert acc["status"] == "connected", acc
    with w.db() as db:
        account = db.get(CardAccount, w.ids.account)
        account.provider, account.connected_account_id = "stripe_issuing", acc["id"]
        db.commit()
    registry.register_test_provider(w.ids.account, w.provider)
    return w


def post_webhook(w, body: bytes, headers: dict, cid: str | None = None, provider="stripe_issuing"):
    return w.client.post(
        f"/api/webhooks/cards/{cid or w.cid}/{provider}", content=body, headers=headers
    )


def test_a_good_signature_only_triggers_a_sync(stripe_world):
    w = stripe_world
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id)
    # the body claims something else entirely: it must not be trusted or stored
    body = json.dumps(
        {
            "type": "issuing_transaction.created",
            "data": {
                "object": {
                    "id": "ipi_forged",
                    "type": "capture",
                    "amount": -999999,
                    "card": w.ids.pcard,
                }
            },
        }
    ).encode()
    r = post_webhook(w, body, stripe_header(body, WHSEC))
    assert r.status_code == 202, r.text
    assert r.json()["synced"] == 1
    with w.db() as db:
        assert db.get(PurchaseRequest, w.ids.purchase).status == "settled"
        ids = {t.provider_txn_id for t in db.scalars(select(CardTransaction))}
        assert "ipi_forged" not in ids and len(ids) == 2
        assert expenses(db, w.cid)[0].amount_micros == 20 * USD  # from polling, not the body


def test_every_webhook_failure_is_the_same_401(stripe_world):
    w = stripe_world
    body = json.dumps({"type": "issuing_transaction.created", "data": {"object": {}}}).encode()
    good = stripe_header(body, WHSEC)
    cases = {
        "bad signature": post_webhook(w, body, stripe_header(body, "whsec_" + "x" * 24)),
        "tampered body": post_webhook(w, body + b" ", good),
        "stale": post_webhook(w, body, stripe_header(body, WHSEC, stamp=int(time.time()) - 3600)),
        "no header": post_webhook(w, body, {"content-type": "application/json"}),
        "unknown company": post_webhook(w, body, good, cid="no-such-company"),
        "no webhooks for provider": post_webhook(w, body, good, provider="mercury"),
        "unknown provider": post_webhook(w, body, good, provider="brex"),
    }
    expected = cases["unknown company"].json()
    for name, resp in cases.items():
        assert resp.status_code == 401, name
        assert resp.json() == expected, name
    assert w.provider.call_count("list_transactions") == 0  # no failure synced anything


def test_a_company_without_a_webhook_secret_rejects_even_a_valid_looking_signature(world):
    w = world
    w.app.state.integrations.http.transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, json={"livemode": False, "issuing": {"available": []}, "data": []}
        )
    )
    connect(w.client, w.cid, "stripe_issuing", secret=SK)  # no webhook_secret
    body = b"{}"
    assert post_webhook(w, body, stripe_header(body, WHSEC)).status_code == 401


def test_the_webhook_body_is_size_capped(stripe_world):
    w = stripe_world
    big = b"x" * (w.app.state.settings.webhook_max_bytes + 1)
    assert post_webhook(w, big, stripe_header(big, WHSEC)).status_code == 413


def test_card_events_reach_the_company_event_stream_without_card_data(world):
    w = world
    auth = w.provider.authorize(w.ids.pcard, 20 * USD, merchant_name="Plausible")
    w.provider.capture(auth.provider_txn_id)
    w.sync()
    with w.db() as db:
        payloads = [
            json.dumps(e.payload)
            for e in db.scalars(select(CompanyEvent).where(CompanyEvent.company_id == w.cid))
        ]
    assert any("purchase_request_id" in p for p in payloads)
    assert not any(re.search(r"\d{13,}", p) for p in payloads)


def test_the_purchase_expiry_pass_expires_unpaid_cards_past_their_ttl(world, scheduler, clock):
    w = world
    registry.register_test_provider(w.ids.account, w.provider)
    with w.db() as db:
        purchase = db.get(PurchaseRequest, w.ids.purchase)
        purchase.expires_at = clock() - timedelta(hours=1)
        db.commit()
    report = scheduler.tick(clock())
    assert report.cards["purchases"] == 1
    with w.db() as db:
        assert db.get(PurchaseRequest, w.ids.purchase).status == "expired"
    assert w.provider.cards[w.ids.pcard].status == "closed"
