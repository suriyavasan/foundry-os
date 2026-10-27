"""M15 purchasing policy: the band decision, the monthly totals, the floors, the code ceilings and
who may change any of it."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.purchasing import PurchaseRequest
from app.models.work import Task
from app.services import purchasing, wallet
from tests.guardrail_helpers import add_rule
from tests.helpers import api
from tests.m15_wp1_helpers import USD, make_world

OPT_IN = {
    "purchase_cofounder_limit_micros": 50 * USD,
    "purchase_cofounder_monthly_micros": 200 * USD,
    "purchase_recurring_requires_human": True,
    "purchase_new_vendor_requires_human": False,
}


@pytest.fixture(autouse=True)
def _clean_providers():
    yield
    wallet.PROVIDER_OVERRIDES.clear()


def decide(
    world, agent_key="worker", *, amount=10 * USD, recurrence="one_time", domain="x.example"
):
    with world.app.state.session_factory() as db:
        return purchasing.decide_band(
            db,
            world.cid,
            getattr(world.team, agent_key)["id"],
            amount_micros=amount,
            recurrence=recurrence,
            vendor_domain=domain,
        )


def test_defaults_send_everything_to_a_person(app, client, company):
    world = make_world(app, client, company)
    for amount in (USD, 10 * USD, 5000 * USD):
        assert decide(world, amount=amount).decision == "human"


def test_a_company_that_has_not_switched_purchasing_on_gets_a_person_task(app, client, company):
    world = make_world(app, client, company, purchasing=False)
    assert decide(world).decision == "report_only"


def test_purchasing_on_without_a_card_account_is_still_a_person_task(app, client, company):
    world = make_world(app, client, company, provider="none")
    assert decide(world).decision == "report_only"


def test_the_card_limit_is_the_amount_plus_tolerance(app, client, company):
    world = make_world(app, client, company)
    # 10 % of $100 = $10 beats the $2 minimum; $5 + max($0.50, $2) = $7
    assert decide(world, amount=100 * USD).card_limit_micros == 110 * USD
    assert decide(world, amount=5 * USD).card_limit_micros == 7 * USD


def test_each_band(app, client, company):
    world = make_world(
        app, client, company, **OPT_IN, purchase_auto_approve_micros=5 * USD,
        purchase_agent_auto_monthly_micros=20 * USD,
    )  # fmt: skip
    assert decide(world, amount=5 * USD).decision == "auto"
    assert decide(world, amount=5 * USD + 1).decision == "cofounder"
    assert decide(world, amount=50 * USD).decision == "cofounder"
    assert decide(world, amount=50 * USD + 1).decision == "human"


def test_recurring_and_new_vendor_floors_go_to_a_person(app, client, company):
    world = make_world(app, client, company, **OPT_IN)
    assert decide(world, amount=10 * USD).decision == "cofounder"
    assert decide(world, amount=10 * USD, recurrence="monthly").decision == "human"
    world.policy(purchase_new_vendor_requires_human=True)
    d = decide(world, amount=10 * USD)
    assert d.decision == "human" and "new vendor" in d.reason
    world.policy(purchase_recurring_requires_human=False, purchase_new_vendor_requires_human=False)
    assert decide(world, amount=10 * USD, recurrence="monthly").decision == "cofounder"


def test_the_cofounder_cannot_approve_its_own_purchase(app, client, company):
    world = make_world(app, client, company, **OPT_IN, purchase_auto_approve_micros=20 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD)  # fmt: skip
    assert decide(world, amount=10 * USD).decision == "auto"
    d = decide(world, "founder", amount=10 * USD)
    assert d.decision == "human" and "Co-founder" in d.reason


def _seed(world, agent_key, amount, *, decision, status="awaiting_checkout", held=None):
    from app.core.time import utcnow

    with world.app.state.session_factory() as db:
        task_id = db.scalar(select(Task.id).limit(1))
        agent = getattr(world.team, agent_key)
        row = PurchaseRequest(
            company_id=world.cid, agent_id=agent["id"], task_id=task_id, item="seed", vendor="V",
            vendor_domain="seed.example", url="https://seed.example/", quantity=1,
            amount_micros=amount, recurrence="one_time", justification="j" * 40, status=status,
            decision=decision, held_micros=amount if held is None else held,
            dedupe_key=f"seed-{amount}-{decision}-{agent_key}-{utcnow().timestamp()}",
            period_month=utcnow().strftime("%Y-%m"), checkout_packet={},
        )  # fmt: skip
        db.add(row)
        db.commit()


def test_the_monthly_totals_tighten_the_band(app, client, company):
    world = make_world(
        app, client, company, **OPT_IN, purchase_auto_approve_micros=10 * USD,
        purchase_agent_auto_monthly_micros=15 * USD,
    )  # fmt: skip
    world.client.post(api(world.cid, "/tasks"), json={"title": "seed task"})
    assert decide(world, amount=10 * USD).decision == "auto"
    _seed(world, "worker", 10 * USD, decision="auto")
    # a second $10 would take the agent past $15 of automatic spend: the Co-founder decides
    assert decide(world, amount=10 * USD).decision == "cofounder"
    # another agent has its own automatic total
    assert decide(world, "manager", amount=10 * USD).decision == "auto"
    # the Co-founder's own month: $200 in total
    _seed(world, "worker", 195 * USD, decision="cofounder")
    assert decide(world, amount=11 * USD).decision == "human"


def test_the_company_purchase_budget_is_a_hard_cap(app, client, company):
    world = make_world(app, client, company, **OPT_IN, purchase_monthly_budget_micros=30 * USD)
    world.client.post(api(world.cid, "/tasks"), json={"title": "seed task"})
    assert decide(world, amount=30 * USD).decision == "cofounder"
    _seed(world, "worker", 25 * USD, decision="human")
    d = decide(world, amount=6 * USD)
    assert d.decision == "deny" and "purchase budget" in d.reason
    assert decide(world, amount=5 * USD).decision != "deny"


def test_an_agents_purchase_limit_is_a_hard_cap(app, client, company):
    world = make_world(app, client, company, **OPT_IN)
    worker = world.team.worker["id"]
    r = client.patch(
        api(world.cid, f"/wallet/agents/{worker}/purchase-limit"),
        json={"purchase_monthly_limit_micros": 8 * USD},
    )
    assert r.status_code == 200 and r.json()["purchase_monthly_limit_micros"] == 8 * USD
    d = decide(world, amount=9 * USD)
    assert d.decision == "deny" and "purchase limit" in d.reason
    assert decide(world, "manager", amount=9 * USD).decision != "deny"
    assert (
        client.get(api(world.cid, f"/agents/{worker}")).json()["purchase_monthly_limit_micros"]
        == 8 * USD
    )


def test_budget_does_not_touch_model_budgets(app, client, company):
    """Purchases have their own budget: a purchase never changes the model spend numbers."""
    world = make_world(app, client, company, **OPT_IN, monthly_budget_micros=1 * USD)
    assert decide(world, amount=40 * USD).decision == "cofounder"


def test_guardrail_rules_can_tighten_but_never_loosen(app, client, company):
    world = make_world(app, client, company, **OPT_IN, purchase_auto_approve_micros=10 * USD,
                       purchase_agent_auto_monthly_micros=100 * USD)  # fmt: skip
    assert decide(world, amount=10 * USD).decision == "auto"
    add_rule(client, world.cid, tier="spend", action="purchase.make", decision="human_approval")
    assert decide(world, amount=10 * USD).decision == "human"
    add_rule(client, world.cid, tier="spend", action="purchase.make", decision="deny",
             conditions={"allowed_domains": ["blocked.example"]})  # fmt: skip
    assert decide(world, amount=10 * USD, domain="blocked.example").decision == "deny"
    # an explicit allow rule cannot lift a band that needs a person
    add_rule(client, world.cid, tier="spend", action="purchase.make", decision="allow")
    assert decide(world, amount=200 * USD).decision == "human"


# ---------------------------------------------------------------- the policy endpoint


def test_policy_ceilings_are_enforced_in_the_api(app, client, company):
    cid = company["id"]
    url = api(cid, "/policy")
    got = client.get(url).json()
    assert got["purchasing_enabled"] is False
    assert got["purchase_auto_approve_micros"] == 0 and got["purchase_cofounder_limit_micros"] == 0
    assert got["purchase_recurring_requires_human"] and got["purchase_new_vendor_requires_human"]
    for field, ceiling in (
        ("purchase_auto_approve_micros", 100 * USD),
        ("purchase_agent_auto_monthly_micros", 500 * USD),
        ("purchase_cofounder_limit_micros", 1000 * USD),
    ):
        assert client.patch(url, json={field: ceiling}).status_code == 200
        assert client.patch(url, json={field: ceiling + 1}).status_code == 422
    for bad in (
        {"purchasing_enabled": None},
        {"purchase_card_ttl_hours": 0},
        {"purchase_checkout_member_id": "no-such-member"},
    ):
        assert client.patch(url, json=bad).status_code in (404, 422), bad
    assert client.patch(url, json={"purchase_monthly_budget_micros": None}).status_code == 200


def test_purchase_policy_changes_are_audited(app, client, company):
    cid = company["id"]
    client.patch(api(cid, "/policy"), json={"purchase_cofounder_limit_micros": 50 * USD})
    events = client.get(api(cid, "/activity"), params={"limit": 50}).json()["items"]
    assert any(e["action"] == "purchase_policy.updated" for e in events)


def test_an_agent_cannot_change_the_purchase_policy_or_limits(app, client, company):
    world = make_world(app, client, company)
    worker = world.agent()
    cid, wid = world.cid, world.team.worker["id"]
    r = worker.client.patch(
        api(cid, "/policy"),
        json={"purchase_cofounder_limit_micros": 900 * USD},
        headers=worker.headers,
    )
    assert r.status_code == 403
    r = worker.client.patch(
        api(cid, f"/wallet/agents/{wid}/purchase-limit"),
        json={"purchase_monthly_limit_micros": 10**12},
        headers=worker.headers,
    )
    assert r.status_code == 403
    assert client.get(api(cid, "/policy")).json()["purchase_cofounder_limit_micros"] == 0
    with app.state.session_factory() as db:
        assert db.get(Agent, wid).purchase_monthly_limit_micros is None
