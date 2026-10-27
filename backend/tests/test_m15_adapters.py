"""Stripe Issuing and Mercury adapters against ``httpx.MockTransport`` fixtures modelled on the
providers' documented object shapes. NOTHING here reaches a real API, and the shapes are not
verified against the live ones (see the adapter module docstrings). Also: the hard rule that no
adapter method can return a card number or CVC."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import hmac
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.integrations.cards import base, guides, manual, mercury, registry, stripe_issuing
from app.integrations.cards.base import (
    CardProvider,
    CardProviderError,
    CardProviderNotAvailable,
    MerchantLock,
)
from app.integrations.cards.manual import ManualCardProvider
from app.integrations.cards.mercury import MercuryProvider
from app.integrations.cards.stripe_issuing import StripeIssuingProvider
from app.integrations.catalog import build_client, check_connection

USD = 1_000_000
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
SK = "rk_test_" + "AbCd1234" * 4
MERCURY_TOKEN = "secret-token:mercury_sandbox_wma_AbCdEf123456"
FORBIDDEN_NAMES = {"pan", "cvc", "cvv", "number", "card_number", "full_number", "expiry"}


class Recorder:
    """A MockTransport handler that records requests and answers from a route table."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for (method, path), answer in self.routes.items():
            if request.method == method and request.url.path == path:
                value = answer(request) if callable(answer) else answer
                if isinstance(value, httpx.Response):
                    return value
                return httpx.Response(200, json=value)
        return httpx.Response(404, json={"error": {"message": f"no route {request.url.path}"}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def form(self, index: int = -1) -> dict[str, list[str]]:
        return parse_qs(self.requests[index].content.decode())


def stripe_card(**over):
    card = {
        "id": "ic_1PQ",
        "object": "issuing.card",
        "brand": "Visa",
        "cardholder": {"id": "ich_1"},
        "created": 1_790_000_000,
        "currency": "usd",
        "exp_month": 10,
        "exp_year": 2029,
        "last4": "4242",
        "livemode": False,
        "metadata": {"cos_purchase": "p1", "cos_company": "c1"},
        "spending_controls": {
            "spending_limits": [{"amount": 2200, "interval": "all_time"}],
        },
        "lifecycle_controls": {"cancel_after": {"payment_count": 1}},
        "status": "active",
        "type": "virtual",
    }
    card.update(over)
    return card


def stripe(routes):
    rec = Recorder(routes)
    return StripeIssuingProvider(
        rec.client(),
        SK,
        cardholder_id="ich_1",
        webhook_secret="whsec_" + "Zz9Y" * 6,
        clock=lambda: NOW,
    ), rec


# ------------------------------------------------------------------ the hard rule


def test_no_adapter_class_or_dataclass_can_carry_a_card_number():
    for cls in (base.IssuedCard, base.CardTxn, base.Balance, base.Capabilities, base.RevealHint):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & FORBIDDEN_NAMES, (cls, names)
    for adapter in (StripeIssuingProvider, MercuryProvider, ManualCardProvider):
        methods = [n for n, _ in inspect.getmembers(adapter, inspect.isfunction)]
        assert not [
            m
            for m in methods
            if any(w in m.lower() for w in ("pan", "cvc", "cvv", "reveal_number"))
        ]
        assert (
            isinstance(adapter.__new__(adapter), CardProvider) or adapter is not ManualCardProvider
        )
    # every public CardProvider method returns one of the safe types (or nothing / a plain dict)
    safe = {
        "IssuedCard",
        "None",
        "tuple[list[CardTxn], str | None]",
        "Balance",
        "dict",
        "RevealHint",
        "list[CardTxn]",
    }
    for name, fn in inspect.getmembers(base.CardProvider, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert str(fn.__annotations__.get("return")) in safe | {"'IssuedCard'"}, name


def test_adapter_sources_never_reach_for_card_credentials():
    """No Vault / reveal endpoint, no expand of number or cvc, in code (docstrings may explain)."""
    forbidden = ("/vault", "vault/", "expand[]=number", "cvc", "cvv", "/number", "card_number")
    for module in (stripe_issuing, mercury, manual):
        tree = ast.parse(Path(module.__file__).read_text())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    first = node.body[0]
                    docstrings.add(id(first.value))  # type: ignore[attr-defined]
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                text = node.value.lower()
                if module is stripe_issuing and text in ("number", "cvc"):
                    continue  # the FORBIDDEN_EXPAND guard list itself
                assert not any(w in text for w in forbidden), (module.__name__, node.value)


def test_stripe_client_refuses_to_expand_the_number_or_cvc():
    client, rec = stripe({("GET", "/v1/issuing/cards/ic_1PQ"): stripe_card()})
    for params in ({"expand[]": "number"}, {"expand[]": "cvc"}, {"expand": ["number", "cvc"]}):
        with pytest.raises(CardProviderError, match="forbidden"):
            client.request("GET", "/v1/issuing/cards/ic_1PQ", what="x", params=params)
    with pytest.raises(CardProviderError):
        client.request("POST", "/v1/issuing/cards/ic_1PQ", what="x", data={"expand[]": "number"})
    assert rec.requests == []  # refused before anything was sent
    client.request("GET", "/v1/issuing/cards/ic_1PQ", what="x", params={"expand[]": "cardholder"})


def test_a_card_in_a_provider_response_never_leaks_a_number_into_our_types():
    leaky = stripe_card(number="4242424242424242", cvc="123")
    client, _ = stripe({("POST", "/v1/issuing/cards"): leaky})
    card = client.issue_card(
        limit_micros=22 * USD,
        limit_interval="all_time",
        merchant_lock=None,
        single_use=True,
        expires_at=None,
        idempotency_key="p1",
        metadata={"cos_purchase": "p1", "cos_company": "c1"},
    )
    assert "4242424242424242" not in repr(card) and "123" not in repr(dataclasses.asdict(card))


def test_the_network_guard_blocks_stripe_and_mercury():
    for url in ("https://api.stripe.com/v1/balance", "https://api.mercury.com/api/v1/accounts"):
        with httpx.Client() as c, pytest.raises(AssertionError, match="never call"):
            c.get(url)
    import socket

    with pytest.raises(AssertionError):
        socket.getaddrinfo("api.stripe.com", 443)


# ------------------------------------------------------------------ Stripe


def test_stripe_issue_card_sends_exact_controls_and_idempotency_key():
    client, rec = stripe({("POST", "/v1/issuing/cards"): stripe_card()})
    card = client.issue_card(
        limit_micros=22 * USD,
        limit_interval="all_time",
        merchant_lock=MerchantLock(
            name="Plausible", domain="plausible.io", mcc_allow=("software",)
        ),
        single_use=True,
        expires_at=NOW + timedelta(days=3),
        idempotency_key="purchase-1",
        metadata={"cos_purchase": "purchase-1", "cos_company": "c1"},
    )
    request = rec.requests[0]
    form = rec.form()
    assert request.headers["Idempotency-Key"] == "purchase-1"
    assert request.headers["Authorization"] == f"Bearer {SK}"
    assert form["cardholder"] == ["ich_1"] and form["type"] == ["virtual"]
    assert form["spending_controls[spending_limits][0][amount]"] == ["2200"]
    assert form["spending_controls[spending_limits][0][interval]"] == ["all_time"]
    assert form["spending_controls[allowed_categories][]"] == ["software"]
    assert form["lifecycle_controls[cancel_after][payment_count]"] == ["1"]
    assert form["metadata[cos_purchase]"] == ["purchase-1"]
    assert set(k for k in form if k.startswith("metadata")) == {
        "metadata[cos_purchase]",
        "metadata[cos_company]",
    }
    assert not [k for k in form if "expand" in k]
    assert (card.provider_card_id, card.last4, card.brand) == ("ic_1PQ", "4242", "visa")
    assert card.limit_micros == 22 * USD and card.limit_interval == "all_time"
    assert card.single_use and card.status == "active"
    assert card.reveal.kind == "provider_dashboard"
    assert card.reveal.url == "https://dashboard.stripe.com/test/issuing/cards/ic_1PQ"


def test_stripe_rounds_the_limit_up_and_rejects_bad_input_before_calling():
    client, rec = stripe({("POST", "/v1/issuing/cards"): stripe_card()})
    client.issue_card(
        limit_micros=19_991_000,
        limit_interval="monthly",
        merchant_lock=None,
        single_use=False,
        expires_at=None,
        idempotency_key="k",
        metadata={},
    )
    assert rec.form()["spending_controls[spending_limits][0][amount]"] == ["2000"]
    assert "lifecycle_controls[cancel_after][payment_count]" not in rec.form()
    n = len(rec.requests)
    for kw in ({"limit_micros": 0}, {"limit_interval": "hourly"}, {"metadata": {"task": "buy"}}):
        args = dict(
            limit_micros=USD,
            limit_interval="monthly",
            merchant_lock=None,
            single_use=False,
            expires_at=None,
            idempotency_key="k",
            metadata={},
        )
        args.update(kw)
        with pytest.raises(CardProviderError):
            client.issue_card(**args)
    assert len(rec.requests) == n
    no_holder = StripeIssuingProvider(rec.client(), SK)
    with pytest.raises(CardProviderError, match="cardholder"):
        no_holder.issue_card(
            limit_micros=USD,
            limit_interval="monthly",
            merchant_lock=None,
            single_use=False,
            expires_at=None,
            idempotency_key="k",
            metadata={},
        )


def test_stripe_update_limit_only_lowers():
    client, rec = stripe(
        {
            ("GET", "/v1/issuing/cards/ic_1PQ"): stripe_card(),
            ("POST", "/v1/issuing/cards/ic_1PQ"): stripe_card(
                spending_controls={"spending_limits": [{"amount": 1000, "interval": "all_time"}]}
            ),
        }
    )
    lowered = client.update_limit(
        "ic_1PQ", limit_micros=10 * USD, limit_interval="all_time", idempotency_key="u1"
    )
    assert lowered.limit_micros == 10 * USD
    with pytest.raises(CardProviderError, match="human action"):
        client.update_limit(
            "ic_1PQ", limit_micros=500 * USD, limit_interval="all_time", idempotency_key="u2"
        )
    assert [r.method for r in rec.requests].count("POST") == 1


def test_stripe_freeze_unfreeze_close_and_idempotent_close():
    state = {"status": "active"}

    def get_card(_):
        return stripe_card(status=state["status"])

    def post_card(request):
        state["status"] = parse_qs(request.content.decode())["status"][0]
        return stripe_card(status=state["status"])

    client, rec = stripe(
        {
            ("GET", "/v1/issuing/cards/ic_1PQ"): get_card,
            ("POST", "/v1/issuing/cards/ic_1PQ"): post_card,
        }
    )
    client.freeze("ic_1PQ")
    assert state["status"] == "inactive"
    client.unfreeze("ic_1PQ")
    assert state["status"] == "active"
    client.close("ic_1PQ")
    assert state["status"] == "canceled"
    posts = [r for r in rec.requests if r.method == "POST"]
    client.close("ic_1PQ")  # already canceled (single-use cards cancel themselves): no new POST
    client.freeze("ic_1PQ")
    assert len([r for r in rec.requests if r.method == "POST"]) == len(posts)
    with pytest.raises(CardProviderError, match="canceled"):
        client.unfreeze("ic_1PQ")


CAPTURE = {
    "id": "ipi_1",
    "object": "issuing.transaction",
    "type": "capture",
    "amount": -2150,
    "currency": "usd",
    "card": "ic_1PQ",
    "authorization": "iauth_1",
    "created": 1_790_000_100,
    "merchant_amount": -2150,
    "merchant_currency": "usd",
    "merchant_data": {"name": "PLAUSIBLE.IO", "category_code": "5734", "country": "EE"},
}
REFUND = {**CAPTURE, "id": "ipi_2", "type": "refund", "amount": 500, "merchant_amount": 500}
AUTH_OK = {
    "id": "iauth_1",
    "object": "issuing.authorization",
    "approved": True,
    "status": "pending",
    "amount": 2150,
    "card": {"id": "ic_1PQ", "last4": "4242"},
    "created": 1_790_000_050,
    "merchant_amount": 2150,
    "merchant_currency": "usd",
    "merchant_data": {"name": "PLAUSIBLE.IO", "category_code": "5734", "country": "EE"},
    "request_history": [{"approved": True, "reason": "webhook_approved"}],
}
AUTH_DECLINED = {
    **AUTH_OK,
    "id": "iauth_2",
    "approved": False,
    "status": "closed",
    "request_history": [{"approved": False, "reason": "insufficient_funds"}],
}
AUTH_LIMIT = {
    **AUTH_OK,
    "id": "iauth_3",
    "approved": False,
    "status": "closed",
    "request_history": [{"approved": False, "reason": "spending_controls"}],
}


def test_stripe_lists_transactions_then_authorizations_with_an_opaque_cursor():
    pages = {
        ("GET", "/v1/issuing/transactions"): lambda r: (
            {"data": [CAPTURE], "has_more": True}
            if "starting_after" not in r.url.params
            else {"data": [REFUND], "has_more": False}
        ),
        ("GET", "/v1/issuing/authorizations"): {
            "data": [AUTH_OK, AUTH_DECLINED, AUTH_LIMIT],
            "has_more": False,
        },
    }
    client, rec = stripe(pages)
    since = NOW - timedelta(days=3)
    items, cursor = client.list_transactions(since=since)
    assert cursor == "txn:ipi_1" and [t.kind for t in items] == ["capture"]
    assert rec.requests[0].url.params["created[gte]"] == str(int(since.timestamp()))
    items2, cursor = client.list_transactions(since=since, cursor=cursor)
    assert cursor == "auth:" and [t.kind for t in items2] == ["refund"]
    items3, cursor = client.list_transactions(since=since, cursor=cursor)
    assert cursor is None
    assert [(t.kind, t.status, t.decline_reason) for t in items3] == [
        ("authorization", "pending", None),
        ("decline", "declined", "insufficient_funds"),
        ("decline", "declined", "spending_limit"),
    ]
    with pytest.raises(CardProviderError):
        client.list_transactions(since=since, cursor="bogus:1")


def test_stripe_transaction_mapping_is_signed_in_micro_usd():
    client, _ = stripe(
        {
            ("GET", "/v1/issuing/transactions"): {"data": [CAPTURE, REFUND]},
        }
    )
    items, _ = client.list_transactions(since=NOW)
    cap, ref = items
    assert (cap.amount_micros, cap.status, cap.provider_card_id) == (
        21_500_000,
        "settled",
        "ic_1PQ",
    )
    assert ref.amount_micros == -5_000_000 and ref.kind == "refund"
    assert cap.merchant_name == "PLAUSIBLE.IO" and cap.merchant_mcc == "5734"
    assert cap.merchant_country == "EE" and cap.merchant_currency == "USD"
    assert cap.occurred_at == datetime.fromtimestamp(1_790_000_100, tz=UTC)
    # an unknown type is skipped rather than guessed at
    odd = {**CAPTURE, "id": "ipi_3", "type": "dispute"}
    client2, _ = stripe({("GET", "/v1/issuing/transactions"): {"data": [odd]}})
    assert client2.list_transactions(since=NOW)[0] == []


def test_stripe_balance_check_and_funding_fallback():
    balance = {
        "object": "balance",
        "livemode": False,
        "available": [],
        "issuing": {"available": [{"amount": 150_000, "currency": "usd"}]},
    }
    client, _ = stripe(
        {
            ("GET", "/v1/balance"): balance,
            ("GET", "/v1/issuing/cards"): {"data": []},
            ("GET", "/v1/issuing/cardholders/ich_1"): {"id": "ich_1", "status": "active"},
            ("POST", "/v1/funding_instructions"): httpx.Response(
                403, json={"error": {"message": "no"}}
            ),
        }
    )
    assert client.balance().available_micros == 1_500 * USD
    info = client.check()
    assert info["ok"] and info["mode"] == "test" and info["issuing_enabled"]
    assert info["balance_micros"] == 1_500 * USD and info["cardholder_active"]
    assert "Issuing balance" in client.funding_instructions()["where"]  # fell back, did not raise
    assert SK not in json.dumps(info)


def test_stripe_check_reports_issuing_not_enabled_and_bad_key_raises():
    client, _ = stripe(
        {
            ("GET", "/v1/balance"): {"livemode": False, "issuing": {"available": []}},
            ("GET", "/v1/issuing/cards"): httpx.Response(
                403, json={"error": {"message": "Issuing is not enabled"}}
            ),
        }
    )
    info = client.check()
    assert not info["ok"] and not info["issuing_enabled"] and "not enabled" in info["message"]
    bad, _ = stripe(
        {
            ("GET", "/v1/balance"): httpx.Response(
                401, json={"error": {"message": f"Invalid API Key provided: {SK}"}}
            )
        }
    )
    with pytest.raises(CardProviderError) as exc:
        bad.check()
    assert SK not in str(exc.value)  # the key is masked out of the message


def test_stripe_errors_carry_the_transient_flag():
    for status, transient in ((429, True), (500, True), (503, True), (400, False), (404, False)):
        client, _ = stripe(
            {
                ("GET", "/v1/issuing/transactions"): httpx.Response(
                    status, json={"error": {"message": "x"}}
                )
            }
        )
        with pytest.raises(CardProviderError) as exc:
            client.list_transactions(since=NOW)
        assert exc.value.transient is transient, status
    boom = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r))
        )
    )
    with pytest.raises(CardProviderError) as exc:
        StripeIssuingProvider(boom, SK).list_transactions(since=NOW)
    assert exc.value.transient is True


def sign(body: bytes, secret: str, stamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def test_stripe_webhook_signature_and_event_mapping():
    secret = "whsec_" + "Zz9Y" * 6
    client, _ = stripe({})
    body = json.dumps({"type": "issuing_transaction.created", "data": {"object": CAPTURE}}).encode()
    stamp = int(NOW.timestamp())
    events = client.parse_webhook({"Stripe-Signature": sign(body, secret, stamp)}, body, secret)
    assert [e.provider_txn_id for e in events] == ["ipi_1"]
    auth = json.dumps(
        {"type": "issuing_authorization.created", "data": {"object": AUTH_OK}}
    ).encode()
    assert (
        client.parse_webhook({"stripe-signature": sign(auth, secret, stamp)}, auth, secret)[0].kind
        == "authorization"
    )
    other = json.dumps({"type": "customer.created", "data": {"object": {}}}).encode()
    assert (
        client.parse_webhook({"stripe-signature": sign(other, secret, stamp)}, other, secret) == []
    )
    bad = [
        {},  # no header
        {"stripe-signature": sign(body, "whsec_other", stamp)},  # wrong secret
        {"stripe-signature": sign(body + b" ", secret, stamp)},  # body tampered
        {"stripe-signature": sign(body, secret, stamp - 3600)},  # too old
        {"stripe-signature": "t=abc,v1=00"},
    ]
    for headers in bad:
        with pytest.raises(CardProviderError):
            client.parse_webhook(headers, body, secret)
    with pytest.raises(CardProviderError):
        client.parse_webhook({"stripe-signature": sign(body, "", stamp)}, body, "")


def test_stripe_ensure_cardholder_and_funding_with_details():
    client, rec = stripe(
        {
            ("POST", "/v1/issuing/cardholders"): {"id": "ich_9", "status": "active"},
            ("POST", "/v1/funding_instructions"): {
                "bank_transfer": {
                    "financial_addresses": [
                        {
                            "aba": {
                                "routing_number": "110000000",
                                "account_number": "000123456789",
                                "bank_name": "Test Bank",
                            }
                        }
                    ]
                }
            },
        }
    )
    holder = StripeIssuingProvider(rec.client(), SK)
    cid = holder.ensure_cardholder(
        name="Acme agents",
        email="o@acme.test",
        phone_number="+15555550100",
        billing_address={
            "line1": "1 Main",
            "city": "SF",
            "state": "CA",
            "postal_code": "94111",
            "country": "US",
        },
        idempotency_key="c1",
    )
    assert cid == "ich_9" and holder.cardholder_id == "ich_9"
    assert rec.form()["billing[address][country]"] == ["US"]
    assert client.funding_instructions()["routing number"] == "110000000"


# ------------------------------------------------------------------ Mercury


MERC_ACCOUNT = {
    "id": "acc_1",
    "name": "Acme Checking",
    "status": "active",
    "kind": "checking",
    "availableBalance": 1234.56,
    "currentBalance": 1300.06,
    "routingNumber": "091311229",
    "accountNumber": "9000012345",
}
MERC_CARD = {
    "id": "mcard_1",
    "nickname": "cos-p1",
    "status": "active",
    "lastFourDigits": "1881",
    "network": "mastercard",
    "spendingLimit": {"amount": 22.0, "interval": "monthly"},
}


def merc(routes):
    rec = Recorder(routes)
    return MercuryProvider(rec.client(), MERCURY_TOKEN, account_id="acc_1", clock=lambda: NOW), rec


def test_mercury_issue_card_emulates_single_use_and_is_idempotent_by_nickname():
    cards: list[dict] = []

    def create(request):
        body = json.loads(request.content)
        card = {**MERC_CARD, "nickname": body["nickname"], "spendingLimit": body["spendingLimit"]}
        cards.append(card)
        return card

    client, rec = merc(
        {
            ("GET", "/api/v1/account/acc_1/cards"): lambda r: {"cards": cards},
            ("POST", "/api/v1/account/acc_1/cards"): create,
        }
    )
    args = dict(
        limit_micros=22 * USD,
        limit_interval="all_time",
        merchant_lock=None,
        single_use=True,
        expires_at=None,
        idempotency_key="p1",
        metadata={"cos_purchase": "p1", "cos_company": "c1"},
    )
    first = client.issue_card(**args)
    posted = json.loads([r for r in rec.requests if r.method == "POST"][0].content)
    assert posted["nickname"] == "cos-p1" and posted["spendingLimit"] == {
        "amount": 22.0,
        "interval": "monthly",
    }  # all_time is sent as a monthly cap
    assert first.single_use and first.last4 == "1881" and first.limit_micros == 22 * USD
    assert first.reveal.url.startswith("https://app.mercury.com/")
    again = client.issue_card(**args)  # the retry finds the card by its nickname
    assert again.provider_card_id == first.provider_card_id
    assert len([r for r in rec.requests if r.method == "POST"]) == 1
    assert not MercuryProvider.capabilities.merchant_mcc_lock
    assert not MercuryProvider.capabilities.webhooks


def test_mercury_update_limit_only_lowers_and_status_changes():
    state = {"status": "active", "amount": 22.0}

    def one(request):
        if request.method == "PATCH":
            body = json.loads(request.content)
            state["status"] = body.get("status", state["status"])
            state["amount"] = body.get("spendingLimit", {}).get("amount", state["amount"])
        return {
            **MERC_CARD,
            "status": state["status"],
            "spendingLimit": {"amount": state["amount"]},
        }

    client, _ = merc(
        {
            ("GET", "/api/v1/account/acc_1/cards/mcard_1"): one,
            ("PATCH", "/api/v1/account/acc_1/cards/mcard_1"): one,
        }
    )
    client.update_limit(
        "mcard_1", limit_micros=10 * USD, limit_interval="monthly", idempotency_key="u"
    )
    assert state["amount"] == 10.0
    with pytest.raises(CardProviderError, match="human action"):
        client.update_limit(
            "mcard_1", limit_micros=99 * USD, limit_interval="monthly", idempotency_key="u"
        )
    client.freeze("mcard_1")
    assert state["status"] == "frozen"
    client.unfreeze("mcard_1")
    client.close("mcard_1")
    assert state["status"] == "canceled"
    client.close("mcard_1")  # idempotent


def test_mercury_transactions_only_card_activity_and_amount_signs():
    def tx(i, amount, status, kind="debitCardTransaction", card="mcard_1"):
        return {
            "id": f"tx{i}",
            "amount": amount,
            "status": status,
            "kind": kind,
            "counterpartyName": "PLAUSIBLE",
            "createdAt": "2026-10-26T10:00:00Z",
            "postedAt": "2026-10-27T01:00:00Z",
            "details": {"debitCardInfo": {"id": card}} if card else {},
        }

    rows = [
        tx(1, -21.5, "pending"),
        tx(2, -21.5, "sent"),
        tx(3, 5.0, "sent"),
        tx(4, -3.0, "cancelled"),
        tx(5, -3.0, "failed"),
        tx(6, -1000, "sent", kind="externalTransfer", card=None),
    ]
    client, rec = merc(
        {
            ("GET", "/api/v1/account/acc_1/transactions"): lambda r: {
                "total": 6,
                "transactions": rows,
            }
        }
    )
    items, cursor = client.list_transactions(since=NOW - timedelta(days=3))
    assert cursor is None
    assert [(t.provider_txn_id, t.kind, t.status, t.amount_micros) for t in items] == [
        ("tx1", "authorization", "pending", 21_500_000),
        ("tx2", "capture", "settled", 21_500_000),
        ("tx3", "refund", "settled", -5_000_000),
        ("tx4", "reversal", "reversed", 3_000_000),
        ("tx5", "decline", "declined", 3_000_000),
    ]  # the wire (tx6) has no card and is not ours
    assert rec.requests[0].url.params["start"] == "2026-10-24"
    assert items[1].settled_at == datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


def test_mercury_pagination_balance_funding_and_check():
    def page(request):
        offset = int(request.url.params["offset"])
        row = {
            "id": f"tx{offset}",
            "amount": -1,
            "status": "sent",
            "createdAt": "2026-10-26T10:00:00Z",
            "details": {"debitCardInfo": {"id": "mcard_1"}},
        }
        return {"total": 2, "transactions": [row]}

    client, _ = merc(
        {
            ("GET", "/api/v1/account/acc_1/transactions"): page,
            ("GET", "/api/v1/account/acc_1"): MERC_ACCOUNT,
            ("GET", "/api/v1/account/acc_1/cards"): {"cards": []},
        }
    )
    items, cursor = client.list_transactions(since=NOW)
    assert cursor == "offset:1"
    items2, cursor = client.list_transactions(since=NOW, cursor=cursor)
    assert cursor is None and items2[0].provider_txn_id == "tx1"
    bal = client.balance()
    assert bal.available_micros == 1_234_560_000 and bal.pending_micros == 65_500_000
    assert client.funding_instructions()["routing number"] == "091311229"
    info = client.check()
    assert info["ok"] and info["mode"] == "test" and info["balance_micros"] == 1_234_560_000
    with pytest.raises(CardProviderNotAvailable):
        client.parse_webhook({}, b"{}", "x")
    with pytest.raises(CardProviderError):
        client.list_transactions(since=NOW, cursor="junk")


def test_mercury_discovers_the_account_when_none_is_configured():
    rec = Recorder(
        {
            ("GET", "/api/v1/accounts"): {
                "accounts": [{"id": "acc_old", "status": "archived"}, MERC_ACCOUNT]
            },
            ("GET", "/api/v1/account/acc_1"): MERC_ACCOUNT,
        }
    )
    provider = MercuryProvider(rec.client(), MERCURY_TOKEN)
    assert provider.balance().available_micros == 1_234_560_000
    assert provider.account_id == "acc_1"


# ------------------------------------------------------------------ manual, catalog, registry, guides


def test_manual_provider_is_honest_about_what_it_cannot_do():
    m = ManualCardProvider(label="Visa", brand="visa", last4="1234")
    assert isinstance(m, CardProvider) and not m.capabilities.issue_cards
    assert m.check()["ok"] and m.list_transactions(since=NOW) == ([], None)
    for call in (
        lambda: m.issue_card(
            limit_micros=1,
            limit_interval="monthly",
            merchant_lock=None,
            single_use=True,
            expires_at=None,
            idempotency_key="k",
            metadata={},
        ),
        lambda: m.balance(),
        lambda: m.freeze("x"),
        lambda: m.close("x"),
        lambda: m.reveal_hint("x"),
        lambda: m.update_limit("x", limit_micros=1, limit_interval="monthly", idempotency_key="k"),
    ):
        with pytest.raises(CardProviderNotAvailable):
            call()
    assert ManualCardProvider(last4="12345678").last4 is None  # only exactly 4 digits are kept


def test_catalog_builds_and_checks_the_card_providers(make_settings):
    from app.integrations.base import IntegrationHttp
    from app.integrations.catalog import SPECS

    assert SPECS["stripe_issuing"].category == "cards" and SPECS["mercury"].category == "cards"
    assert not SPECS["stripe_issuing"].actions and not SPECS["mercury"].actions  # agents get no key
    rec = Recorder(
        {
            ("GET", "/v1/balance"): {"livemode": False, "issuing": {"available": []}},
            ("GET", "/v1/issuing/cards"): {"data": []},
        }
    )
    http = IntegrationHttp(transport=httpx.MockTransport(rec))
    client = build_client(
        "stripe_issuing",
        {"api_key": SK, "webhook_secret": "whsec_" + "a1" * 12},
        {"cardholder_id": "ich_1"},
        http,
        make_settings(),
    )
    assert isinstance(client, StripeIssuingProvider) and client.cardholder_id == "ich_1"
    result = check_connection("stripe_issuing", client, {})
    assert result.ok and "cards" in result.scopes and SK not in (result.message or "")
    mclient = build_client(
        "mercury", {"api_token": MERCURY_TOKEN}, {"account_id": "acc_1"}, http, make_settings()
    )
    assert isinstance(mclient, MercuryProvider) and mclient.account_id == "acc_1"
    bad = IntegrationHttp(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
        )
    )
    failing = build_client("stripe_issuing", {"api_key": SK}, {}, bad, make_settings())
    assert check_connection("stripe_issuing", failing, {}).ok is False


def test_registry_refuses_fake_and_unknown_providers_outside_tests():
    from app.models.purchasing import CardAccount

    fake = CardAccount(id="a1", company_id="c", provider="fake", label="x")
    with pytest.raises(CardProviderNotAvailable):
        registry.provider_for(None, fake)  # type: ignore[arg-type]
    other = CardAccount(id="a2", company_id="c", provider="brex", label="x")
    with pytest.raises(CardProviderNotAvailable):
        registry.provider_for(None, other)  # type: ignore[arg-type]
    manual_account = CardAccount(
        id="a3", company_id="c", provider="manual", label="x", manual_last4="1234"
    )
    assert registry.provider_for(None, manual_account).id == "manual"  # type: ignore[arg-type]
    with pytest.raises(CardProviderNotAvailable):
        registry.webhook_events("mercury", {}, b"{}", "s")


def test_guides_cover_every_provider_with_dated_honest_text():
    infos = guides.provider_infos()
    assert {i.id for i in infos} == {"manual", "stripe_issuing", "mercury"}  # never "fake"
    assert guides.GUIDE_UPDATED_ON == "2026-10-27"
    for info in infos:
        assert info.steps and info.eligibility and info.verification
        assert any(s.owner_only for s in info.steps)
    stripe_info = next(i for i in infos if i.id == "stripe_issuing")
    assert "Not exercised against the real system" in stripe_info.verification
    assert guides.recommend("india_company").provider == "manual"
    assert guides.recommend("not_incorporated").steps[0].title.startswith("Use a personal")
    assert guides.recommend("us_company", has_stripe=True).provider == "stripe_issuing"
    assert guides.recommend("us_company", has_mercury=True, has_stripe=True).provider == "mercury"
    assert "restricted" in " ".join(s.body for s in guides.GUIDES["stripe_issuing"].steps).lower()
