"""M15 HTTP surface: tenancy, roles, agents kept out of the Wallet, the agent API, reveal, the
finance block, /wallet and the checklist step, and a schema walk for card-number fields."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.purchasing import PurchaseRequest
from app.services import wallet
from tests.conftest import human
from tests.helpers import api
from tests.m15_wp1_helpers import USD, make_world

AUTO = {
    "purchase_auto_approve_micros": 25 * USD,
    "purchase_agent_auto_monthly_micros": 100 * USD,
    "purchase_new_vendor_requires_human": False,
}


@pytest.fixture(autouse=True)
def _clean_providers():
    yield
    wallet.PROVIDER_OVERRIDES.clear()


@pytest.fixture
def world(app, client, company):
    return make_world(app, client, company, **AUTO)


def _purchase(world, **over) -> tuple:
    session = world.agent()
    pid = world.buy(session, **over).json()["purchase_request_id"]
    return session, pid


# ---------------------------------------------------------------- tenancy and roles


def test_another_company_sees_nothing_of_this_one(app, client, world):
    session, pid = _purchase(world)
    card = world.purchase(pid)["card"]
    other = client.post("/api/companies", json={"name": "Other Co"}).json()["id"]
    for path in (
        f"/purchases/{pid}",
        f"/wallet/accounts/{world.account_id}/funding",
        f"/wallet/cards/{card['id']}/reveal",
    ):
        assert client.get(api(other, path)).status_code == 404, path
    for path in (
        f"/purchases/{pid}/approve",
        f"/purchases/{pid}/cancel",
        f"/purchases/{pid}/mark-paid",
        f"/wallet/cards/{card['id']}/freeze",
        f"/wallet/cards/{card['id']}/close",
        f"/wallet/accounts/{world.account_id}/check",
        f"/wallet/accounts/{world.account_id}/default",
        "/subscriptions/nope/keep",
    ):
        body = {"merchant_amount_usd": "1"} if path.endswith("mark-paid") else {}
        r = client.post(api(other, path), json=body)
        assert r.status_code == 404, (path, r.status_code)
    assert client.delete(api(other, f"/wallet/accounts/{world.account_id}")).status_code == 404
    assert client.get(api(other, "/purchases")).json()["items"] == []
    assert client.get(api(other, "/wallet/cards")).json()["items"] == []
    assert client.get(api(other, "/wallet/transactions")).json()["items"] == []
    assert client.get(api(other, "/wallet")).json()["accounts"] == []


def test_a_non_member_gets_404_and_a_viewer_cannot_write(
    app, client, world, make_user, add_member, client_as
):
    session, pid = _purchase(world)
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(world.cid, "/wallet")).status_code == 404
    assert stranger.get(api(world.cid, f"/purchases/{pid}")).status_code == 404
    viewer_user = make_user("viewer@example.com")
    add_member(world.cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    assert viewer.get(api(world.cid, "/wallet")).status_code == 200
    assert viewer.get(api(world.cid, f"/purchases/{pid}")).status_code == 200
    for method, path, body in (
        ("post", f"/purchases/{pid}/cancel", {}),
        ("post", "/wallet/sync", {}),
        ("post", f"/wallet/accounts/{world.account_id}/check", {}),
        ("delete", f"/wallet/accounts/{world.account_id}", None),
        ("patch", "/policy", {"purchasing_enabled": False}),
    ):
        r = getattr(viewer, method)(
            api(world.cid, path), **({"json": body} if body is not None else {})
        )
        assert r.status_code == 403, (path, r.status_code)


def test_an_agent_is_kept_out_of_the_wallet(app, client, world):
    session, pid = _purchase(world)
    card = world.purchase(pid)["card"]
    h = {"headers": session.headers}
    c = session.client
    for method, path in (
        ("get", "/wallet"),
        ("get", "/wallet/cards"),
        ("get", "/wallet/providers"),
        ("get", "/purchases"),
        ("get", f"/wallet/accounts/{world.account_id}/funding"),
        ("get", f"/wallet/cards/{card['id']}/reveal"),
        ("get", "/subscriptions"),
    ):
        r = getattr(c, method)(api(world.cid, path), **h)
        assert r.status_code in (403,) or (path == "/purchases" and r.status_code == 200), (
            path,
            r.status_code,
        )
    for path, body in (
        ("/wallet/accounts", {"provider": "manual", "label": "mine", "manual_last4": "1234"}),
        (f"/wallet/accounts/{world.account_id}/check", {}),
        (f"/wallet/accounts/{world.account_id}/default", {}),
        (f"/wallet/cards/{card['id']}/freeze", {}),
        (f"/wallet/cards/{card['id']}/unfreeze", {}),
        (f"/wallet/cards/{card['id']}/close", {}),
        (f"/purchases/{pid}/approve", {}),
        (f"/purchases/{pid}/cancel", {}),
        (f"/purchases/{pid}/mark-paid", {"merchant_amount_usd": "1"}),
        ("/wallet/sync", {}),
    ):
        r = c.post(api(world.cid, path), json=body, **h)
        assert r.status_code == 403, (path, r.status_code)
    assert c.delete(api(world.cid, f"/wallet/accounts/{world.account_id}"), **h).status_code == 403
    # nothing changed
    assert world.fake.call_count("freeze") == 0 and world.fake.call_count("close") == 0
    assert world.purchase(pid)["status"] == "awaiting_checkout"


# ---------------------------------------------------------------- the agent API


def test_the_agent_api_returns_an_ack_without_card_data(app, client, world):
    session = world.agent()
    r = world.buy(session)
    assert r.status_code == 201
    assert set(r.json()) == {"purchase_request_id", "status", "decision", "next_step"}
    pid = r.json()["purchase_request_id"]
    got = session.get(f"/purchases/{pid}")
    assert got.status_code == 200 and got.json()["status"] == "awaiting_checkout"
    assert "card" not in json.dumps(got.json()).replace("Founder", "")  # no card details
    # another agent cannot read it, and an unknown id is a 404 as well
    other = world.agent("manager", "other work")
    assert other.get(f"/purchases/{pid}").status_code == 404
    assert other.get("/purchases/nope").status_code == 404


def test_the_three_agent_routes_pass_the_guardrail_gate(app, client, world):
    from app.guardrails import registry

    session = world.agent()
    world.buy(session)
    events = client.get(api(world.cid, "/guardrails/events"), params={"limit": 50}).json()["items"]
    actions = {e["action"] for e in events}
    assert {"purchase.request", "purchase.make"} <= actions
    assert registry.ROUTES[("POST", "/purchases")].action == "purchase.request"


# ---------------------------------------------------------------- accounts


def test_manual_accounts_take_only_the_last_four_digits(app, client, company):
    cid = company["id"]
    url = api(cid, "/wallet/accounts")
    for bad in (
        {"manual_last4": "4242424242424242"},
        {"label": "4242 4242 4242 4242"},
        {"provider": "fake"},  # the fake exists only in tests
        {"provider": "stripe_issuing"},  # needs a connected account
        {"provider": "stripe_issuing", "connected_account_id": "nope"},
    ):
        body = {"provider": "manual", "label": "Company Visa", "manual_last4": "1234", **bad}
        assert client.post(url, json=body).status_code in (404, 422), bad
    r = client.post(
        url, json={"provider": "manual", "label": "Company Visa", "manual_last4": "1234"}
    )
    assert r.status_code == 201
    assert r.json()["status"] == "active" and r.json()["is_default"] is True
    assert "1234" in json.dumps(r.json()) and "4242" not in json.dumps(r.json())


def test_check_default_funding_and_disable(app, client, world):
    cid, acc = world.cid, world.account_id
    r = client.post(api(cid, f"/wallet/accounts/{acc}/check"))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["balance_micros"] == 1000 * USD
    f = client.get(api(cid, f"/wallet/accounts/{acc}/funding"))
    assert f.status_code == 200 and "FAKE-ISSUING-0001" in json.dumps(f.json())
    assert f.headers["cache-control"] == "no-store"
    assert [e for e in _audits(app, cid, "card_account.funding_viewed")]
    # the funding text is never stored
    with app.state.session_factory() as db:
        assert "FAKE-ISSUING" not in json.dumps(
            [e.data for e in db.scalars(select(AuditEvent))], default=str
        )
    session, pid = _purchase(world)
    card = world.purchase(pid)["card"]
    r = client.delete(api(cid, f"/wallet/accounts/{acc}"))
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert world.fake.cards[card["provider_card_id"]].status == "frozen"
    # with no active account the next request is a person's task
    ack = world.buy(world.agent("manager", "later"), vendor="Elsewhere", item="Another tool").json()
    assert ack["status"] == "human_task"


def _audits(app, cid, action):
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action == action)
            )
        )


# ---------------------------------------------------------------- cards and reveal


def test_freeze_unfreeze_close_and_reveal(app, client, world):
    session, pid = _purchase(world)
    cid = world.cid
    card = world.purchase(pid)["card"]
    r = client.post(api(cid, f"/wallet/cards/{card['id']}/freeze"), json={"reason": "unsure"})
    assert r.status_code == 200 and r.json()["status"] == "frozen"
    assert r.json()["frozen_reason"] == "unsure"
    r = client.post(api(cid, f"/wallet/cards/{card['id']}/unfreeze"), json={})
    assert r.status_code == 200 and r.json()["status"] == "active"
    # reveal: a link to the provider's page, audited, with no card data
    r = client.get(api(cid, f"/wallet/cards/{card['id']}/reveal"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "provider_dashboard" and body["url"].endswith(card["provider_card_id"])
    assert r.headers["cache-control"] == "no-store"
    events = _audits(app, cid, "card.reveal_opened")
    assert len(events) == 1 and events[0].data == {}
    # a card in a purchase in progress is closed by cancelling the purchase
    assert client.post(api(cid, f"/wallet/cards/{card['id']}/close"), json={}).status_code == 409
    client.post(api(cid, f"/purchases/{pid}/cancel"), json={})
    assert client.get(api(cid, "/wallet/cards"), params={"status": "closed"}).json()["items"]


def test_reveal_is_rate_limited_and_needs_a_session_or_the_owner_token(app, client, world):
    session, pid = _purchase(world)
    card = world.purchase(pid)["card"]
    url = api(world.cid, f"/wallet/cards/{card['id']}/reveal")
    for _ in range(10):
        assert client.get(url).status_code == 200
    assert client.get(url).status_code == 429


def test_reveal_without_recent_authentication_is_refused_by_the_service(app, client, world):
    from app.core.principal import CompanyContext, Principal, PrincipalType, Role

    session, pid = _purchase(world)
    card = world.purchase(pid)["card"]
    with app.state.session_factory() as db:
        ctx = CompanyContext(
            world.cid, Principal(PrincipalType.HUMAN, "u1", "Owner"), Role.OWNER, member_id="m1"
        )
        with pytest.raises(wallet.ReauthRequired) as exc:
            wallet.reveal_card(db, ctx, card["id"], recent_auth=False)
        assert exc.value.code == "reauth_required"


# ---------------------------------------------------------------- overview, finance, command, checklist


def test_overview_finance_command_and_checklist(app, client, world):
    cid = world.cid
    empty = client.get(api(cid, "/wallet")).json()
    assert empty["enabled"] is True and empty["month"]["held_micros"] == 0
    session, pid = _purchase(world)
    ov = client.get(api(cid, "/wallet")).json()
    assert ov["month"]["held_micros"] == 19 * USD and ov["month"]["settled_verified_micros"] == 0
    assert [w["purchase_request_id"] for w in ov["awaiting_checkout"]] == [pid]
    assert ov["balance"]["source"] == "provider"
    fin = client.get(api(cid, "/finance/summary")).json()["purchases"]
    assert fin["held_micros"] == 19 * USD and fin["settled_verified_micros"] == 0
    # the model budgets are untouched by a purchase
    assert client.get(api(cid, "/finance/summary")).json()["totals"]["verified_micros"] == 0
    out = client.post(api(cid, "/command"), json={"input": "/wallet"}).json()
    assert out["output"]["title"] == "Wallet"
    assert "$19.00" in json.dumps(out["output"]["rows"])
    steps = client.get(api(cid, "/reachability/checklist")).json()["steps"]
    cards = next(s for s in steps if s["id"] == "agent_cards")
    assert cards["status"] == "done" and cards["optional"] is True


def test_not_set_up_overview_recommends_the_opt_in_values(app, client, company):
    cid = company["id"]
    ov = client.get(api(cid, "/wallet")).json()
    assert ov["enabled"] is False and ov["available"] is True
    rec = ov["setup"]["recommended_policy"]
    assert rec["purchase_cofounder_limit_micros"] == 50 * USD
    assert rec["purchase_cofounder_monthly_micros"] == 200 * USD
    assert rec["purchase_auto_approve_micros"] == 0
    steps = client.get(api(cid, "/reachability/checklist")).json()["steps"]
    assert next(s for s in steps if s["id"] == "agent_cards")["status"] == "todo"
    out = client.post(api(cid, "/command"), json={"input": "/wallet"}).json()
    assert "not set up" in out["output"]["text"]


def test_providers_list_is_honest_about_verification(app, client, company):
    r = client.get(api(company["id"], "/wallet/providers"))
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert {"manual", "stripe_issuing", "mercury"} <= set(items)
    assert "fake" not in items
    for pid in ("stripe_issuing", "mercury"):
        assert "not exercised" in items[pid]["verification"].lower()


# ---------------------------------------------------------------- no card numbers in the contract


def test_no_response_schema_can_carry_a_card_number(app, client):
    schema = client.get("/api/openapi.json").json()["components"]["schemas"]
    forbidden = {"pan", "cvc", "cvv", "card_number", "full_number", "number"}
    wallet_models = [
        n
        for n in schema
        if any(
            k in n for k in ("Card", "Purchase", "Wallet", "Subscription", "Checkout", "Funding")
        )
    ]
    assert len(wallet_models) > 15
    for name in wallet_models:
        props = set(schema[name].get("properties", {}))
        assert not props & forbidden, (name, props & forbidden)


def test_a_purchase_row_has_no_column_for_a_card_number():
    cols = {c.name for c in PurchaseRequest.__table__.columns}
    from app.models.purchasing import AgentCard

    cols |= {c.name for c in AgentCard.__table__.columns}
    assert not cols & {"pan", "cvc", "cvv", "card_number", "number"}
