"""Self-tests of the stateful fake card provider every M15 test builds on, and of the adapter
interface's hard rule: no card number can pass through it."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.cards import base
from app.integrations.cards.base import CardProvider, CardProviderError, MerchantLock
from app.integrations.cards.fake import SIGNATURE_HEADER, FakeCardProvider

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
FORBIDDEN_FIELDS = {"pan", "cvc", "cvv", "number", "card_number", "full_number", "secret"}
USD = 1_000_000


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


def issue(p: FakeCardProvider, key="p1", limit=50 * USD, *, single_use=True, **kw):
    return p.issue_card(
        limit_micros=limit,
        limit_interval=kw.pop("interval", "per_authorization"),
        merchant_lock=kw.pop("lock", MerchantLock(name="Vendor", domain="vendor.example")),
        single_use=single_use,
        expires_at=None,
        idempotency_key=key,
        metadata={"cos_purchase": key, "cos_company": "c1"},
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def prov(clock) -> FakeCardProvider:
    return FakeCardProvider(funded_micros=1000 * USD, now=clock)


def test_the_fake_satisfies_the_protocol_and_exposes_no_card_number(prov):
    assert isinstance(prov, CardProvider)
    for cls in (base.IssuedCard, base.CardTxn, base.Balance, base.Capabilities, base.RevealHint):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & FORBIDDEN_FIELDS, (cls, names & FORBIDDEN_FIELDS)
    card = issue(prov)
    blob = json.dumps(dataclasses.asdict(card), default=str)
    assert len(card.last4) == 4 and card.last4.isdigit()
    # nothing in a card resembles a full number (13-19 digits)
    assert not any(
        len(run) >= 13 for run in "".join(c if c.isdigit() else " " for c in blob).split()
    )
    # and the provider has no method that could return one
    assert not [n for n in dir(prov) if "pan" in n.lower() or "cvc" in n.lower()]


def test_issue_is_idempotent_and_validates(prov):
    a = issue(prov, "k1")
    b = issue(prov, "k1", limit=999 * USD)  # the same key returns the same card, unchanged
    assert a == b and len(prov.cards) == 1
    c = issue(prov, "k2")
    assert c.provider_card_id != a.provider_card_id
    assert prov.card_meta[a.provider_card_id]["metadata"] == {
        "cos_purchase": "k1",
        "cos_company": "c1",
    }
    with pytest.raises(CardProviderError):
        issue(prov, "k3", limit=0)
    with pytest.raises(CardProviderError):
        issue(prov, "k4", interval="weekly")
    with pytest.raises(CardProviderError, match="metadata"):
        prov.issue_card(
            limit_micros=USD,
            limit_interval="all_time",
            merchant_lock=None,
            single_use=True,
            expires_at=None,
            idempotency_key="k5",
            metadata={"task": "buy a thing for the task"},  # task text must never reach a provider
        )


def test_strict_funding_refuses_a_limit_above_the_balance():
    p = FakeCardProvider(funded_micros=10 * USD, strict_funding=True)
    with pytest.raises(CardProviderError, match="insufficient"):
        issue(p, limit=50 * USD)
    assert issue(p, "ok", limit=10 * USD).limit_micros == 10 * USD


def test_authorize_capture_refund_flow_and_balance(prov, clock):
    card = issue(prov, single_use=False)
    auth = prov.authorize(card.provider_card_id, 20 * USD, merchant_name="Vendor Inc")
    assert (auth.kind, auth.status) == ("authorization", "pending")
    bal = prov.balance()
    assert (bal.available_micros, bal.pending_micros) == (980 * USD, 20 * USD)

    clock.advance(hours=2)
    cap = prov.capture(auth.provider_txn_id, 22 * USD)  # tax added at capture
    assert (cap.kind, cap.status, cap.amount_micros) == ("capture", "settled", 22 * USD)
    assert cap.settled_at == clock.t
    states = {t.provider_txn_id: t.status for t in prov.txns}
    assert states[auth.provider_txn_id] == "reversed"  # the hold moved into the capture
    assert prov.balance().available_micros == 978 * USD and prov.balance().pending_micros == 0

    refund = prov.refund(cap.provider_txn_id, 5 * USD)
    assert (refund.kind, refund.amount_micros) == ("refund", -5 * USD)
    assert prov.balance().available_micros == 983 * USD
    with pytest.raises(CardProviderError):
        prov.capture(auth.provider_txn_id)  # no longer open
    with pytest.raises(CardProviderError):
        prov.refund(auth.provider_txn_id)  # only a capture can be refunded


def test_declines_are_recorded_with_a_reason(prov):
    card = issue(prov, limit=30 * USD)
    over = prov.authorize(card.provider_card_id, 31 * USD)
    assert (over.kind, over.status, over.decline_reason) == (
        "decline",
        "declined",
        "spending_limit",
    )
    prov.funded_micros = 5 * USD
    poor = prov.authorize(card.provider_card_id, 10 * USD)
    assert poor.decline_reason == "insufficient_funds"
    prov.freeze(card.provider_card_id)
    frozen = prov.authorize(card.provider_card_id, 1 * USD)
    assert frozen.decline_reason == "card_inactive"
    assert [t.kind for t in prov.txns] == ["decline"] * 3
    assert prov.balance().pending_micros == 0  # a decline holds nothing


def test_single_use_closes_after_the_first_capture_unless_emulation_is_off(clock):
    for emulate, expected in ((True, "closed"), (False, "active")):
        p = FakeCardProvider(funded_micros=100 * USD, now=clock, emulate_single_use=emulate)
        card = issue(p)
        p.capture(p.authorize(card.provider_card_id, 10 * USD).provider_txn_id)
        assert p.cards[card.provider_card_id].status == expected
    # a closed card declines the next attempt
    p = FakeCardProvider(funded_micros=100 * USD, now=clock)
    card = issue(p)
    p.capture(p.authorize(card.provider_card_id, 10 * USD).provider_txn_id)
    assert p.authorize(card.provider_card_id, 1 * USD).decline_reason == "card_inactive"


def test_freeze_unfreeze_close_lifecycle(prov):
    card = issue(prov, single_use=False)
    cid = card.provider_card_id
    prov.freeze(cid)
    assert prov.cards[cid].status == "frozen"
    prov.unfreeze(cid)
    assert prov.cards[cid].status == "active"
    prov.close(cid)
    assert prov.cards[cid].status == "closed"
    prov.freeze(cid)  # freezing a closed card does not reopen it
    assert prov.cards[cid].status == "closed"
    with pytest.raises(CardProviderError, match="closed"):
        prov.unfreeze(cid)
    with pytest.raises(CardProviderError, match="closed"):
        prov.update_limit(cid, limit_micros=USD, limit_interval="all_time", idempotency_key="x")
    with pytest.raises(CardProviderError, match="unknown"):
        prov.freeze("nope")


def test_update_limit_is_recorded_so_tests_can_see_automation_never_raises(prov):
    card = issue(prov, limit=50 * USD)
    lowered = prov.update_limit(
        card.provider_card_id,
        limit_micros=20 * USD,
        limit_interval="per_authorization",
        idempotency_key="u1",
    )
    assert lowered.limit_micros == 20 * USD
    assert prov.call_count("update_limit") == 1
    assert prov.calls[-1] == (
        "update_limit",
        (card.provider_card_id, 20 * USD, "per_authorization"),
    )


def test_list_transactions_filters_by_time_and_pages(clock):
    p = FakeCardProvider(funded_micros=1000 * USD, now=clock, page_size=2)
    card = issue(p, single_use=False)
    ids = []
    for _ in range(5):
        clock.advance(hours=1)
        ids.append(p.authorize(card.provider_card_id, USD).provider_txn_id)
    seen, cursor, pages = [], None, 0
    while True:
        page, cursor = p.list_transactions(since=T0, cursor=cursor)
        seen += [t.provider_txn_id for t in page]
        pages += 1
        if cursor is None:
            break
    assert seen == ids and pages == 3
    recent, nxt = p.list_transactions(since=T0 + timedelta(hours=4))
    assert [t.provider_txn_id for t in recent] == ids[3:] and nxt is None


def test_failure_injection_transient_and_permanent(prov):
    prov.fail_next("issue_card", transient=True)
    with pytest.raises(CardProviderError) as exc:
        issue(prov, "f1")
    assert exc.value.transient is True
    assert issue(prov, "f1").provider_card_id  # the next call works, and nothing half-created
    prov.fail_next("list_transactions", transient=False, times=2)
    for _ in range(2):
        with pytest.raises(CardProviderError) as exc:
            prov.list_transactions(since=T0)
        assert exc.value.transient is False
    prov.list_transactions(since=T0)
    custom = CardProviderError("custom", transient=True)
    prov.fail_next("balance", custom)
    with pytest.raises(CardProviderError) as exc:
        prov.balance()
    assert exc.value is custom


def test_check_funding_and_reveal_hint_are_links_only(prov):
    check = prov.check()
    assert check["ok"] and check["mode"] == "test" and check["balance_micros"] == 1000 * USD
    assert "reference" in prov.funding_instructions()
    card = issue(prov)
    hint = prov.reveal_hint(card.provider_card_id)
    assert hint == card.reveal and hint.kind == "provider_dashboard"
    assert hint.url.startswith("https://") and card.provider_card_id in hint.url
    with pytest.raises(CardProviderError):
        prov.reveal_hint("nope")


def test_webhook_signature_is_verified(prov):
    card = issue(prov, single_use=False)
    txn = prov.authorize(card.provider_card_id, USD)
    body = json.dumps({"txn_ids": [txn.provider_txn_id]}).encode()
    good = {SIGNATURE_HEADER.title(): prov.sign(body, "whsec")}
    assert [t.provider_txn_id for t in prov.parse_webhook(good, body, "whsec")] == [
        txn.provider_txn_id
    ]
    with pytest.raises(CardProviderError, match="signature"):
        prov.parse_webhook({SIGNATURE_HEADER: "00"}, body, "whsec")
    with pytest.raises(CardProviderError, match="signature"):
        prov.parse_webhook({}, body, "whsec")
    with pytest.raises(CardProviderError, match="signature"):
        prov.parse_webhook(good, body + b" ", "whsec")  # a tampered body
    bad_body = b"not json"
    with pytest.raises(CardProviderError, match="body"):
        prov.parse_webhook({SIGNATURE_HEADER: prov.sign(bad_body, "whsec")}, bad_body, "whsec")


def test_card_provider_error_is_a_provider_error_with_a_transient_flag():
    from app.integrations.base import ProviderError

    err = base.CardProviderError("x")
    assert isinstance(err, ProviderError) and err.transient is False and err.status_code == 502
    na = base.CardProviderNotAvailable()
    assert isinstance(na, base.CardProviderError) and na.status_code == 501
