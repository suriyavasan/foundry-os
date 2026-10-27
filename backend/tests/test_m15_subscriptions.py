"""Subscriptions: renewal reviews, keep / cancel paths, the 48 hour fallback to a human, renewal
and price-increase handling, missed renewals, cards closed after cancellation, and tenancy."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.integrations.cards import registry
from app.integrations.cards.fake import FakeCardProvider
from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.autonomy import Alert
from app.models.purchasing import AgentCard, PurchaseRequest, Subscription
from app.models.work import Task
from app.schemas.purchasing import SubscriptionHumanAction, SubscriptionReviewIn
from app.services import card_reconcile, subscriptions
from tests.helpers import FakeClock, new_agent, new_task
from tests.m15_helpers import USD, issue_for, make_account, make_purchase


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry.clear_test_providers()


def agent_ctx(cid, agent_id):
    return CompanyContext(cid, Principal(PrincipalType.AGENT, agent_id, "A", cid), Role.AGENT)


def human_ctx(cid, role=Role.OWNER):
    return CompanyContext(cid, Principal(PrincipalType.HUMAN, "u-test", "Owner"), role)


@pytest.fixture
def sw(app, client, company):
    """A company with a worker, a monthly Linear subscription due in 5 days and its open card."""
    cid = company["id"]
    worker = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=worker["id"])
    clock = FakeClock()
    provider = FakeCardProvider(funded_micros=1000 * USD, now=clock)
    with app.state.session_factory() as db:
        founder = db.scalar(select(Agent).where(Agent.company_id == cid, Agent.kind == "founder"))
        account = make_account(db, cid, created_at=clock() - timedelta(days=40))
        purchase = make_purchase(
            db,
            cid,
            worker["id"],
            task["id"],
            recurrence="monthly",
            vendor="Linear",
            domain="linear.app",
            amount=10 * USD,
        )
        issued = issue_for(db, provider, account, purchase, kind="subscription")
        sub = subscriptions.create_from_settled(
            db,
            purchase,
            charged_on=clock().date() - timedelta(days=25),
            charged_micros=10 * USD,
            card=issued.card,
            now=clock(),
        )
        sub.next_renewal_on = clock().date() + timedelta(days=5)
        purchase.status = "settled"
        db.commit()
        ids = SimpleNamespace(
            sub=sub.id,
            card=issued.card.id,
            pcard=issued.issued.provider_card_id,
            account=account.id,
            purchase=purchase.id,
            founder=founder.id if founder else None,
        )
    registry.register_test_provider(ids.account, provider)
    w = SimpleNamespace(
        app=app, client=client, cid=cid, worker=worker, clock=clock, provider=provider, ids=ids
    )
    w.db = app.state.session_factory

    def check(**kw):
        with app.state.session_factory() as db:
            n = subscriptions.check(db, w.cid, now=clock(), **kw)
            db.commit()
            return n

    w.check = check
    return w


def sub_of(w):
    with w.db() as db:
        return db.get(Subscription, w.ids.sub)


def alerts(w, kind):
    with w.db() as db:
        return list(db.scalars(select(Alert).where(Alert.company_id == w.cid, Alert.kind == kind)))


# ------------------------------------------------------------------ renewal review


def test_nothing_happens_before_the_alert_window(sw):
    with sw.db() as db:
        db.get(Subscription, sw.ids.sub).next_renewal_on = sw.clock().date() + timedelta(days=10)
        db.commit()
    assert sw.check() == 0
    assert sub_of(sw).status == "active" and sub_of(sw).review_task_id is None


def test_a_review_task_goes_to_the_owning_agent_n_days_before_and_only_once(sw):
    assert sw.check() == 1
    sub = sub_of(sw)
    assert sub.status == "review_due" and sub.review_task_id
    with sw.db() as db:
        task = db.get(Task, sub.review_task_id)
        assert task.assignee_agent_id == sw.worker["id"] and task.kind == "routine"
        assert "company_review_subscription" in task.description and sub.id in task.description
        assert task.title.startswith("Review subscription: Linear renews")
        assert task.due_at is not None
        actions = [
            a.action for a in db.scalars(select(AuditEvent).where(AuditEvent.company_id == sw.cid))
        ]
        assert "subscription.review_requested" in actions
    assert sw.check() == 0  # the next daily run does not ask again
    with sw.db() as db:
        assert (
            len(list(db.scalars(select(Task).where(Task.title.like("Review subscription%"))))) == 1
        )


def test_the_alert_window_follows_the_company_policy(sw):
    with sw.db() as db:
        from app.services import policy

        policy.load_policy(db, sw.cid).subscription_renewal_alert_days = 2
        db.commit()
    assert sw.check() == 0  # due in 5 days, window 2
    sw.clock.advance(days=3)
    assert sw.check() == 1


def test_keep_goes_back_to_active_and_is_not_asked_again_this_cycle(sw):
    sw.check()
    with sw.db() as db:
        sub = subscriptions.review(
            db,
            agent_ctx(sw.cid, sw.worker["id"]),
            sw.ids.sub,
            SubscriptionReviewIn(decision="keep", reason="we use it daily"),
        )
        assert sub.status == "active" and sub.reviewed_at is not None
        assert sub.cancel_task_id is None
    sw.clock.advance(days=1)
    assert sw.check() == 0
    assert sub_of(sw).status == "active"
    with sw.db() as db:
        assert "subscription.kept" in [a.action for a in db.scalars(select(AuditEvent))]


def test_cancel_creates_a_human_task_and_an_alert_and_never_cancels_by_itself(sw):
    sw.check()
    with sw.db() as db:
        sub = subscriptions.review(
            db,
            agent_ctx(sw.cid, sw.worker["id"]),
            sw.ids.sub,
            SubscriptionReviewIn(decision="cancel", reason="no longer needed"),
        )
        assert sub.status == "cancel_requested" and sub.cancel_task_id
        task = db.get(Task, sub.cancel_task_id)
        assert task.assignee_member_id is not None and task.assignee_agent_id is None
        assert task.title == f"Cancel Linear before {sub.next_renewal_on}"
        assert "linear.app" in task.description and "not a cancellation" in task.description
    [alert] = alerts(sw, "subscription_cancel_needed")
    assert alert.severity == "warning"
    assert sw.provider.call_count("close") == 0 and sw.provider.call_count("freeze") == 0


def test_no_answer_within_48_hours_becomes_a_human_cancel_task(sw):
    sw.check()
    sw.clock.advance(hours=47)
    assert sw.check() == 0 and sub_of(sw).status == "review_due"
    sw.clock.advance(hours=2)
    assert sw.check() == 1
    sub = sub_of(sw)
    assert sub.status == "cancel_requested" and sub.cancel_task_id
    with sw.db() as db:
        assert db.get(Task, sub.cancel_task_id).assignee_member_id is not None
    assert len(alerts(sw, "subscription_cancel_needed")) == 1
    assert sw.check() == 0  # and it does not repeat


def test_a_retired_owner_hands_the_review_to_the_cofounder(sw):
    with sw.db() as db:
        db.get(Agent, sw.worker["id"]).status = "retired"
        db.commit()
    sw.check()
    with sw.db() as db:
        sub = db.get(Subscription, sw.ids.sub)
        task = db.get(Task, sub.review_task_id)
        assert task.assignee_agent_id == sw.ids.founder


def test_the_manager_the_review_was_handed_to_may_answer_it(sw):
    """The review task goes owner -> manager -> Co-founder; whoever holds it can answer."""
    lead = new_agent(sw.client, sw.cid, name="Lead")
    with sw.db() as db:
        worker = db.get(Agent, sw.worker["id"])
        worker.manager_agent_id = lead["id"]
        worker.status = "retired"
        db.commit()
    sw.check()
    body = SubscriptionReviewIn(decision="keep", reason="still used")
    with sw.db() as db:
        task = db.get(Task, db.get(Subscription, sw.ids.sub).review_task_id)
        assert task.assignee_agent_id == lead["id"]
        assert task.idempotency_key.startswith("subrev:")
        bystander = new_agent(sw.client, sw.cid, name="Bystander")
        with pytest.raises(Forbidden):  # not the owner, not the assignee, not the Co-founder
            subscriptions.review(db, agent_ctx(sw.cid, bystander["id"]), sw.ids.sub, body)
        assert (
            subscriptions.review(db, agent_ctx(sw.cid, lead["id"]), sw.ids.sub, body).status
            == "active"
        )


# ------------------------------------------------------------------ who may do what


def test_only_the_owner_or_the_cofounder_may_review_and_agents_cannot_do_human_actions(sw):
    sw.check()
    other = new_agent(sw.client, sw.cid, name="Other")
    body = SubscriptionReviewIn(decision="keep", reason="fine")
    with sw.db() as db:
        with pytest.raises(Forbidden):
            subscriptions.review(db, agent_ctx(sw.cid, other["id"]), sw.ids.sub, body)
        with pytest.raises(Forbidden):  # a human uses the Wallet, not the agent entry
            subscriptions.review(db, human_ctx(sw.cid), sw.ids.sub, body)
        for call in (subscriptions.mark_cancelled, subscriptions.keep):
            with pytest.raises(Forbidden):
                call(db, agent_ctx(sw.cid, sw.worker["id"]), sw.ids.sub, SubscriptionHumanAction())
            with pytest.raises(Forbidden):
                call(db, human_ctx(sw.cid, Role.VIEWER), sw.ids.sub, SubscriptionHumanAction())
        assert (
            subscriptions.review(db, agent_ctx(sw.cid, sw.ids.founder), sw.ids.sub, body).status
            == "active"
        )
        with pytest.raises(Conflict):  # nothing left to review once it is active and answered
            subscriptions.review(
                db,
                agent_ctx(sw.cid, sw.ids.founder),
                sw.ids.sub,
                SubscriptionReviewIn(decision="cancel", reason="again"),
            )
            subscriptions.mark_cancelled(
                db, human_ctx(sw.cid), sw.ids.sub, SubscriptionHumanAction()
            )
            subscriptions.review(db, agent_ctx(sw.cid, sw.ids.founder), sw.ids.sub, body)


def test_subscriptions_of_another_company_are_a_404_everywhere(sw):
    other = sw.client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    other_agent = new_agent(sw.client, other["id"])
    with sw.db() as db:
        foreign_ctx = human_ctx(other["id"])
        with pytest.raises(NotFound):
            subscriptions.get_subscription(db, foreign_ctx, sw.ids.sub)
        with pytest.raises(NotFound):
            subscriptions.mark_cancelled(db, foreign_ctx, sw.ids.sub, SubscriptionHumanAction())
        with pytest.raises(NotFound):
            subscriptions.keep(db, foreign_ctx, sw.ids.sub, SubscriptionHumanAction())
        with pytest.raises(NotFound):
            subscriptions.review(
                db,
                agent_ctx(other["id"], other_agent["id"]),
                sw.ids.sub,
                SubscriptionReviewIn(decision="keep", reason="x"),
            )
        assert subscriptions.list_subscriptions(db, foreign_ctx)[0] == []
        mine, cursor = subscriptions.list_subscriptions(db, human_ctx(sw.cid))
        assert [s.id for s in mine] == [sw.ids.sub] and cursor is None
        assert subscriptions.list_subscriptions(db, human_ctx(sw.cid), status="cancelled")[0] == []
    # the daily pass for the other company does not act on ours
    with sw.db() as db:
        assert subscriptions.check(db, other["id"], now=sw.clock()) == 0
    assert sub_of(sw).status == "active"


# ------------------------------------------------------------------ cancellation closes the card


def test_a_cancelled_subscription_keeps_its_card_until_the_paid_period_ends(sw):
    with sw.db() as db:
        sub = subscriptions.mark_cancelled(
            db, human_ctx(sw.cid), sw.ids.sub, SubscriptionHumanAction(note="done on the site")
        )
        assert sub.status == "cancelled"
        assert (
            subscriptions.mark_cancelled(
                db, human_ctx(sw.cid), sw.ids.sub, SubscriptionHumanAction()
            ).status
            == "cancelled"
        )
    sw.check()
    with sw.db() as db:
        assert db.get(AgentCard, sw.ids.card).status == "active"  # still paid for
    sw.clock.advance(days=6)
    assert sw.check() == 1
    with sw.db() as db:
        assert db.get(AgentCard, sw.ids.card).status == "closed"
    assert sw.provider.cards[sw.ids.pcard].status == "closed"


def test_keep_by_a_person_cancels_the_pending_cancel_task(sw):
    sw.check()
    with sw.db() as db:
        subscriptions.review(
            db,
            agent_ctx(sw.cid, sw.worker["id"]),
            sw.ids.sub,
            SubscriptionReviewIn(decision="cancel", reason="r"),
        )
        cancel_task = db.get(Subscription, sw.ids.sub).cancel_task_id
        sub = subscriptions.keep(db, human_ctx(sw.cid), sw.ids.sub, SubscriptionHumanAction())
        assert sub.status == "active"
        assert db.get(Task, cancel_task).status == "cancelled"


# ------------------------------------------------------------------ renewals, price, missed


def test_a_renewal_charge_inside_the_tolerance_advances_the_date(sw):
    due = sub_of(sw).next_renewal_on
    sw.clock.advance(days=5)
    auth = sw.provider.authorize(sw.ids.pcard, 11 * USD, merchant_name="LINEAR.APP")
    sw.provider.capture(auth.provider_txn_id)
    with sw.db() as db:
        card_reconcile.sync_company(db, sw.cid, now=sw.clock())
    sub = sub_of(sw)
    assert sub.next_renewal_on == subscriptions.add_months(due, 1)
    assert sub.last_charge_micros == 11 * USD and sub.last_charged_on == sw.clock().date()
    assert sub.status == "active"


def test_a_review_due_subscription_that_renews_anyway_goes_back_to_active(sw):
    sw.check()
    assert sub_of(sw).status == "review_due"
    sw.clock.advance(days=5)
    auth = sw.provider.authorize(sw.ids.pcard, 10 * USD, merchant_name="LINEAR.APP")
    sw.provider.capture(auth.provider_txn_id)
    with sw.db() as db:
        card_reconcile.sync_company(db, sw.cid, now=sw.clock())
    assert sub_of(sw).status == "active"


def test_a_price_increase_is_declined_by_the_card_and_needs_a_new_request(sw):
    sw.clock.advance(days=5)
    sw.provider.authorize(sw.ids.pcard, 40 * USD, merchant_name="LINEAR.APP")  # card limit $12
    with sw.db() as db:
        card_reconcile.sync_company(db, sw.cid, now=sw.clock())
    [alert] = alerts(sw, "subscription_price_increase")
    assert (
        "$40.00" in alert.body and "$10.00" in alert.body and "new purchase request" in alert.body
    )
    assert alerts(sw, "card_declined") == []  # one clear alert, not two
    sub = sub_of(sw)
    assert sub.status == "active" and sub.last_charge_micros == 10 * USD  # nothing was charged
    with sw.db() as db:
        from app.models.autonomy import WakeRequest

        wake = db.scalar(select(WakeRequest).where(WakeRequest.agent_id == sw.worker["id"]))
        assert wake is not None and "file a new purchase request" in str(wake.reasons).lower()


def test_a_missed_renewal_is_an_info_alert_and_a_late_charge_reactivates(sw):
    due = sub_of(sw).next_renewal_on
    sw.clock.set(datetime.combine(due + timedelta(days=4), time(9, 0), tzinfo=UTC))
    sw.check()
    sub = sub_of(sw)
    assert sub.status == "lapsed"
    [alert] = alerts(sw, "subscription_missed_renewal")
    assert alert.severity == "info"
    assert sw.check() == 0
    auth = sw.provider.authorize(sw.ids.pcard, 10 * USD, merchant_name="LINEAR.APP")
    sw.provider.capture(auth.provider_txn_id)
    with sw.db() as db:
        card_reconcile.sync_company(db, sw.cid, now=sw.clock())
    sub = sub_of(sw)
    assert sub.status == "active" and sub.next_renewal_on > date.today()


def test_a_charge_after_the_subscription_was_cancelled_freezes_the_card(sw):
    with sw.db() as db:
        subscriptions.mark_cancelled(db, human_ctx(sw.cid), sw.ids.sub, SubscriptionHumanAction())
    sw.clock.advance(days=5)
    auth = sw.provider.authorize(sw.ids.pcard, 10 * USD, merchant_name="LINEAR.APP")
    sw.provider.capture(auth.provider_txn_id)
    with sw.db() as db:
        card_reconcile.sync_company(db, sw.cid, now=sw.clock())
    [alert] = alerts(sw, "subscription_charged_after_cancel")
    assert alert.severity == "critical"
    assert sw.provider.cards[sw.ids.pcard].status in ("frozen", "closed")


def test_create_from_settled_is_idempotent(sw):
    with sw.db() as db:
        purchase = db.get(PurchaseRequest, sw.ids.purchase)
        again = subscriptions.create_from_settled(db, purchase)
        assert again.id == sw.ids.sub
        assert len(list(db.scalars(select(Subscription)))) == 1


def test_add_months_clamps_the_day():
    assert subscriptions.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert subscriptions.add_period(date(2026, 3, 15), "yearly") == date(2027, 3, 15)
    assert subscriptions.add_period(date(2026, 12, 31), "monthly") == date(2027, 1, 31)


def test_a_manual_subscription_asks_the_person_instead_of_claiming_a_missed_renewal(sw):
    with sw.db() as db:
        sub = db.get(Subscription, sw.ids.sub)
        sub.card_id = None  # paid with the owner's own card: no charges are ever observed
        due = sub.next_renewal_on
        db.commit()
    sw.clock.set(datetime.combine(due + timedelta(days=4), time(9, 0), tzinfo=UTC))
    sw.check()
    sub = sub_of(sw)
    assert sub.status == "active" and sub.next_renewal_on == subscriptions.add_months(due, 1)
    assert alerts(sw, "subscription_missed_renewal") == []
    [alert] = alerts(sw, "subscription_confirm_renewal")
    assert alert.severity == "info" and "own card" in alert.body
