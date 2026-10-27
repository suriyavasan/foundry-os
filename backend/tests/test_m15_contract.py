"""The M15 step-0 contract: registry entries, schemas without card-number fields, the PAN-in-a-label
guard, the stubs that must not change behaviour, and the fail-closed policy ceilings."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.guardrails import ratelimit, registry
from app.models.policy import (
    MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS,
    MAX_PURCHASE_AUTO_APPROVE_MICROS,
    MAX_PURCHASE_COFOUNDER_LIMIT_MICROS,
)
from app.schemas import purchasing as sp

FORBIDDEN = {"pan", "cvc", "cvv", "number", "card_number", "full_number", "expiry"}
VALID_REQUEST = {
    "item": "Plausible Analytics Growth plan, 10k pageviews",
    "vendor": "Plausible Insights OU",
    "url": "https://plausible.io/pricing",
    "amount_usd": "19.00",
    "recurring": "monthly",
    "justification": "We need privacy-friendly analytics for the launch page and free options lack API access.",
    "task_id": "t1",
}


# ---------------------------------------------------------------- registry


def test_m15_actions_are_registered_with_the_right_tiers():
    get = registry.get_action
    assert get("purchase.request").tier == "internal_write"
    assert get("purchase.request").rate_keys == (registry.PURCHASE_REQUESTS,)
    assert get("purchase.read").tier == "read"
    assert get("subscription.review").tier == "internal_write"
    for name in ("card.issue", "card.freeze"):
        assert get(name).kind == "internal", name
    assert get("card.issue").tier == "spend" and get("card.freeze").tier == "internal_write"
    # the platform's money actions are not reachable from the agent API
    assert "card.issue" not in registry.PROVIDER_ACTIONS
    assert all(spec.action != "card.issue" for spec in registry.ROUTES.values())


def test_m15_human_only_actions_cannot_be_allowed_by_any_rule():
    for name in (
        "card_account.connect",
        "card.unfreeze",
        "card.limit_raise",
        "card.reveal",
        "purchase_policy.update",
    ):
        spec = registry.get_action(name)
        assert spec is not None and spec.human_only and name in registry.HUMAN_ONLY, name


def test_the_two_runtime_tools_map_to_agent_actions():
    tools = registry.tool_registry()["company_tools"]
    assert tools["company_request_purchase"] == {"action": "purchase.request"}
    assert tools["company_review_subscription"] == {"action": "subscription.review"}
    assert registry.tier_of_tool("company_request_purchase") == "internal_write"
    # the existing spend actions that carry the money moment are untouched
    assert registry.get_action("purchase.make").tier == "spend"
    assert registry.get_action("subscription.start").tier == "spend"
    assert registry.get_action("domain.purchase").human_required is True


def test_m15_agent_routes_are_registered_with_their_actions():
    for key, action in {
        ("POST", "/purchases"): "purchase.request",
        ("GET", "/purchases/{purchase_id}"): "purchase.read",
        ("POST", "/subscriptions/{subscription_id}/review"): "subscription.review",
    }.items():
        assert registry.ROUTES[key].action == action, key
        assert registry.get_action(action) is not None


def test_purchase_requests_are_rate_limited_per_agent_and_company():
    lim = ratelimit.LIMITS[registry.PURCHASE_REQUESTS]
    assert (lim.key, lim.window_seconds, lim.per_agent, lim.per_company) == (
        "purchase_requests_per_day",
        86_400,
        5,
        20,
    )


# ---------------------------------------------------------------- schemas


def _fields_of(model) -> set[str]:
    return set(model.model_fields)


def test_no_schema_in_the_module_has_a_card_number_field():
    seen = 0
    for name in dir(sp):
        obj = getattr(sp, name)
        if isinstance(obj, type) and hasattr(obj, "model_fields"):
            seen += 1
            assert not _fields_of(obj) & FORBIDDEN, (name, _fields_of(obj) & FORBIDDEN)
    assert seen > 25
    schema = json.dumps(sp.WalletOverview.model_json_schema())
    assert '"pan"' not in schema and '"cvc"' not in schema


def test_purchase_request_validation():
    ok = sp.PurchaseRequestCreate(**VALID_REQUEST)
    assert ok.amount_usd == Decimal("19.00") and ok.quantity == 1
    bad = (
        {"url": "http://plausible.io/pricing"},  # https only
        {"url": "https://user:pw@plausible.io/"},
        {"amount_usd": "0"},
        {"amount_usd": "-3"},
        {"amount_usd": "19.001"},  # no sub-cent amounts
        {"justification": "too short"},
        {"recurring": "weekly"},
        {"quantity": 0},
        {"original_currency": "EURO"},
        {"item": "Card 4242 4242 4242 4242 for the plan"},  # a card number in agent text
        {"extra_field": 1},
    )
    for change in bad:
        with pytest.raises(ValidationError):
            sp.PurchaseRequestCreate(**{**VALID_REQUEST, **change})


def test_manual_card_accepts_only_the_last_four_digits():
    ok = sp.CardAccountCreate(provider="manual", label="Company Visa", manual_last4="1234")
    assert ok.manual_last4 == "1234"
    for bad in (
        {"manual_last4": "4242424242424242"},
        {"manual_last4": "123"},
        {"manual_last4": "12a4"},
        {"label": "4242 4242 4242 4242"},
        {"label": "my card 4111-1111-1111-1111"},
        {"manual_brand": "4012888888881881"},
    ):
        with pytest.raises(ValidationError):
            sp.CardAccountCreate(**{"provider": "manual", "label": "L", **bad})
    # an order number that is not Luhn-valid, or a short run, is fine
    assert sp.CardAccountCreate(provider="manual", label="Ref 1234567890123").label.startswith(
        "Ref"
    )
    assert not sp.looks_like_card_number("call 555 0100")
    assert sp.looks_like_card_number("4242424242424242")
    assert not sp.looks_like_card_number("1111111111111111")  # all one digit


def test_policy_update_ceilings_match_the_model_constants():
    PU = sp.PurchasePolicyUpdate
    assert PU(purchase_auto_approve_micros=MAX_PURCHASE_AUTO_APPROVE_MICROS)
    assert PU(purchase_agent_auto_monthly_micros=MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS)
    assert PU(purchase_cofounder_limit_micros=MAX_PURCHASE_COFOUNDER_LIMIT_MICROS)
    assert MAX_PURCHASE_AUTO_APPROVE_MICROS == 100_000_000
    assert MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS == 500_000_000
    assert MAX_PURCHASE_COFOUNDER_LIMIT_MICROS == 1_000_000_000
    for bad in (
        {"purchase_auto_approve_micros": MAX_PURCHASE_AUTO_APPROVE_MICROS + 1},
        {"purchase_agent_auto_monthly_micros": MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS + 1},
        {"purchase_cofounder_limit_micros": MAX_PURCHASE_COFOUNDER_LIMIT_MICROS + 1},
        {"purchase_auto_approve_micros": -1},
        {"purchase_card_ttl_hours": 0},
        {"unknown": 1},
    ):
        with pytest.raises(ValidationError):
            PU(**bad)
    default = sp.PurchasePolicy()
    assert default.purchasing_enabled is False
    assert (default.purchase_auto_approve_micros, default.purchase_cofounder_limit_micros) == (0, 0)
    assert default.purchase_recurring_requires_human and default.purchase_new_vendor_requires_human


def test_mark_paid_takes_decimal_dollars_never_floats_in_the_schema():
    body = sp.PurchaseMarkPaid(merchant_amount_usd="19.50", billed_amount_inr="1650.25")
    assert body.merchant_amount_usd == Decimal("19.50")
    with pytest.raises(ValidationError):
        sp.PurchaseMarkPaid(merchant_amount_usd="0")
