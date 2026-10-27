"""A stateful in-memory card provider. Every M15 test uses this; nothing in the suite may call
Stripe or Mercury.

It behaves like a prepaid issuing account:

* ``fund()`` adds money; ``balance()`` is funded minus open authorization holds minus captures.
* ``issue_card`` is idempotent on the key, rejects a limit above the available balance only when
  ``strict_funding`` is on, and always returns a last4 that is NOT a real card (``0000``-``0099``).
* ``authorize`` / ``capture`` / ``refund`` script what a merchant does. An authorization is refused
  (a ``decline`` transaction is recorded) when the card is not active, the amount is over the card
  limit, or the balance is too low. A capture turns its authorization ``reversed`` (the hold moves
  into the capture), as Stripe's authorization ``closed`` does. A single-use card is closed by the
  provider after its first capture, like Stripe; ``emulate_single_use=False`` leaves it open, like
  Mercury (our reconciliation then closes it).
* ``fail_next(method, error)`` injects a failure into the next call of a method (transient or not).
* There is no way to read a card number: none is ever generated.

``calls`` records every call as ``(method, args)`` so tests can assert what automation did, e.g.
that ``update_limit`` was never used to raise a limit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from app.core.time import utcnow
from app.integrations.cards.base import (
    Balance,
    Capabilities,
    CardProviderError,
    CardTxn,
    IssuedCard,
    MerchantLock,
    RevealHint,
)

FAKE_REVEAL_BASE = "https://fake-issuer.invalid/cards/"
SIGNATURE_HEADER = "x-fake-signature"


class FakeCardProvider:
    id = "fake"
    capabilities = Capabilities(
        issue_cards=True,
        single_use=True,
        merchant_mcc_lock=True,
        per_auth_limit=True,
        interval_limits=("per_authorization", "monthly", "yearly", "all_time"),
        list_transactions=True,
        balance=True,
        webhooks=True,
        programmatic_checkout=False,
        test_mode=True,
    )

    def __init__(
        self,
        *,
        funded_micros: int = 0,
        now: Callable[[], datetime] | None = None,
        page_size: int = 100,
        strict_funding: bool = False,
        emulate_single_use: bool = True,
        mode: str = "test",
    ) -> None:
        self._now = now or utcnow
        self.page_size = page_size
        self.strict_funding = strict_funding
        self.emulate_single_use = emulate_single_use
        self.mode = mode
        self.funded_micros = funded_micros
        self.cards: dict[str, IssuedCard] = {}
        self.card_meta: dict[str, dict[str, Any]] = {}
        self.txns: list[CardTxn] = []
        self.calls: list[tuple[str, tuple]] = []
        self._by_key: dict[str, str] = {}
        self._failures: dict[str, list[CardProviderError]] = {}
        self._seq = 0

    # ------------------------------------------------------------------ test scripting

    def fund(self, micros: int) -> None:
        self.funded_micros += micros

    def fail_next(
        self,
        method: str,
        error: CardProviderError | None = None,
        *,
        transient: bool = True,
        times: int = 1,
    ) -> None:
        """The next ``times`` calls of ``method`` raise ``error`` (default: a transient or a
        permanent ``CardProviderError``)."""
        for _ in range(times):
            err = error or CardProviderError("injected failure", transient=transient)
            self._failures.setdefault(method, []).append(err)

    def call_count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def authorize(
        self,
        provider_card_id: str,
        amount_micros: int,
        *,
        merchant_name: str = "Fake Vendor",
        merchant_mcc: str | None = "5734",
        merchant_country: str | None = "US",
        merchant_amount_minor: int | None = None,
        merchant_currency: str | None = None,
        at: datetime | None = None,
    ) -> CardTxn:
        """A merchant asks to hold ``amount_micros``. Returns the authorization, or the decline."""
        card = self._card(provider_card_id)
        when = at or self._now()
        reason = self._decline_reason(card, amount_micros)
        txn = CardTxn(
            provider_txn_id=self._next_id("iauth" if reason is None else "idecl"),
            provider_card_id=provider_card_id,
            kind="authorization" if reason is None else "decline",
            status="pending" if reason is None else "declined",
            amount_micros=amount_micros,
            merchant_amount_minor=merchant_amount_minor,
            merchant_currency=merchant_currency,
            merchant_name=merchant_name,
            merchant_mcc=merchant_mcc,
            merchant_country=merchant_country,
            occurred_at=when,
            settled_at=None,
            decline_reason=reason,
        )
        self.txns.append(txn)
        return txn

    def capture(
        self, authorization_id: str, amount_micros: int | None = None, *, at: datetime | None = None
    ) -> CardTxn:
        """Settle an open authorization (optionally for a different amount, e.g. tax added)."""
        auth = self._txn(authorization_id)
        if auth.kind != "authorization" or auth.status != "pending":
            raise CardProviderError("authorization is not open")
        when = at or self._now()
        self._replace(authorization_id, status="reversed")
        cap = replace(
            auth,
            provider_txn_id=self._next_id("itxn"),
            kind="capture",
            status="settled",
            amount_micros=auth.amount_micros if amount_micros is None else amount_micros,
            occurred_at=when,
            settled_at=when,
        )
        self.txns.append(cap)
        card = self.cards[auth.provider_card_id]
        if card.single_use and self.emulate_single_use and card.status == "active":
            self.cards[card.provider_card_id] = replace(card, status="closed")
        return cap

    def refund(
        self, capture_id: str, amount_micros: int | None = None, *, at: datetime | None = None
    ) -> CardTxn:
        cap = self._txn(capture_id)
        if cap.kind != "capture":
            raise CardProviderError("only a capture can be refunded")
        when = at or self._now()
        value = cap.amount_micros if amount_micros is None else amount_micros
        txn = replace(
            cap,
            provider_txn_id=self._next_id("irfd"),
            kind="refund",
            status="settled",
            amount_micros=-abs(value),
            occurred_at=when,
            settled_at=when,
        )
        self.txns.append(txn)
        return txn

    # ------------------------------------------------------------------ CardProvider

    def check(self) -> dict:
        self._maybe_fail("check")
        self.calls.append(("check", ()))
        return {
            "ok": True,
            "mode": self.mode,
            "issuing_enabled": True,
            "balance_readable": True,
            "balance_micros": self._available(),
        }

    def issue_card(
        self,
        *,
        limit_micros: int,
        limit_interval: str,
        merchant_lock: MerchantLock | None,
        single_use: bool,
        expires_at: datetime | None,
        idempotency_key: str,
        metadata: dict[str, str],
    ) -> IssuedCard:
        self.calls.append(("issue_card", (idempotency_key, limit_micros, single_use)))
        self._maybe_fail("issue_card")
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return self.cards[existing]
        if limit_micros <= 0:
            raise CardProviderError("limit must be positive")
        if limit_interval not in self.capabilities.interval_limits:
            raise CardProviderError("unsupported limit interval")
        if self.strict_funding and limit_micros > self._available():
            raise CardProviderError("insufficient funds for this limit")
        bad = set(metadata) - {"cos_purchase", "cos_company"}
        if bad:
            raise CardProviderError(f"unexpected metadata keys: {sorted(bad)}")
        card_id = self._next_id("ic")
        now = self._now()
        card = IssuedCard(
            provider_card_id=card_id,
            last4=f"{self._seq % 100:04d}",
            brand="visa",
            exp_month=(expires_at or now + timedelta(days=365 * 3)).month,
            exp_year=(expires_at or now + timedelta(days=365 * 3)).year,
            status="active",
            limit_micros=limit_micros,
            limit_interval=limit_interval,
            single_use=single_use,
            reveal=RevealHint("provider_dashboard", f"{FAKE_REVEAL_BASE}{card_id}"),
        )
        self.cards[card_id] = card
        self.card_meta[card_id] = {
            "metadata": dict(metadata),
            "merchant_lock": merchant_lock,
            "expires_at": expires_at,
        }
        self._by_key[idempotency_key] = card_id
        return card

    def update_limit(
        self,
        provider_card_id: str,
        *,
        limit_micros: int,
        limit_interval: str,
        idempotency_key: str,
    ) -> IssuedCard:
        self.calls.append(("update_limit", (provider_card_id, limit_micros, limit_interval)))
        self._maybe_fail("update_limit")
        card = self._card(provider_card_id)
        if card.status == "closed":
            raise CardProviderError("card is closed")
        if limit_micros <= 0:
            raise CardProviderError("limit must be positive")
        card = replace(card, limit_micros=limit_micros, limit_interval=limit_interval)
        self.cards[provider_card_id] = card
        return card

    def freeze(self, provider_card_id: str) -> None:
        self.calls.append(("freeze", (provider_card_id,)))
        self._maybe_fail("freeze")
        card = self._card(provider_card_id)
        if card.status == "active":
            self.cards[provider_card_id] = replace(card, status="frozen")

    def unfreeze(self, provider_card_id: str) -> None:
        self.calls.append(("unfreeze", (provider_card_id,)))
        self._maybe_fail("unfreeze")
        card = self._card(provider_card_id)
        if card.status == "closed":
            raise CardProviderError("a closed card cannot be unfrozen")
        self.cards[provider_card_id] = replace(card, status="active")

    def close(self, provider_card_id: str) -> None:
        self.calls.append(("close", (provider_card_id,)))
        self._maybe_fail("close")
        card = self._card(provider_card_id)
        self.cards[provider_card_id] = replace(card, status="closed")

    def list_transactions(
        self, *, since: datetime, cursor: str | None = None
    ) -> tuple[list[CardTxn], str | None]:
        self.calls.append(("list_transactions", (since, cursor)))
        self._maybe_fail("list_transactions")
        rows = [t for t in self.txns if t.occurred_at >= since]
        start = int(cursor) if cursor else 0
        page = rows[start : start + self.page_size]
        more = start + self.page_size < len(rows)
        return page, str(start + self.page_size) if more else None

    def balance(self) -> Balance:
        self.calls.append(("balance", ()))
        self._maybe_fail("balance")
        return Balance(
            available_micros=self._available(),
            pending_micros=self._held(),
            as_of=self._now(),
        )

    def funding_instructions(self) -> dict:
        self.calls.append(("funding_instructions", ()))
        self._maybe_fail("funding_instructions")
        return {
            "method": "bank transfer (fake)",
            "reference": "FAKE-ISSUING-0001",
            "note": "Test instructions; no real account exists.",
        }

    def reveal_hint(self, provider_card_id: str) -> RevealHint:
        self.calls.append(("reveal_hint", (provider_card_id,)))
        self._card(provider_card_id)
        return RevealHint("provider_dashboard", f"{FAKE_REVEAL_BASE}{provider_card_id}")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]:
        self.calls.append(("parse_webhook", ()))
        lowered = {k.lower(): v for k, v in headers.items()}
        expected = self.sign(body, secret)
        if not hmac.compare_digest(lowered.get(SIGNATURE_HEADER, ""), expected):
            raise CardProviderError("invalid webhook signature")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise CardProviderError("invalid webhook body") from exc
        wanted = set(payload.get("txn_ids", []))
        return [t for t in self.txns if t.provider_txn_id in wanted]

    @staticmethod
    def sign(body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ internals

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:05d}"

    def _maybe_fail(self, method: str) -> None:
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)

    def _card(self, provider_card_id: str) -> IssuedCard:
        try:
            return self.cards[provider_card_id]
        except KeyError:
            raise CardProviderError(f"unknown card {provider_card_id}") from None

    def _txn(self, provider_txn_id: str) -> CardTxn:
        for txn in self.txns:
            if txn.provider_txn_id == provider_txn_id:
                return txn
        raise CardProviderError(f"unknown transaction {provider_txn_id}")

    def _replace(self, provider_txn_id: str, **changes: Any) -> None:
        for i, txn in enumerate(self.txns):
            if txn.provider_txn_id == provider_txn_id:
                self.txns[i] = replace(txn, **changes)
                return

    def _held(self) -> int:
        return sum(t.amount_micros for t in self.txns if t.status == "pending")

    def _captured(self) -> int:
        return sum(
            t.amount_micros
            for t in self.txns
            if t.kind in ("capture", "refund") and t.status == "settled"
        )

    def _available(self) -> int:
        return self.funded_micros - self._held() - self._captured()

    def _decline_reason(self, card: IssuedCard, amount_micros: int) -> str | None:
        if card.status != "active":
            return "card_inactive"
        if amount_micros > card.limit_micros:
            return "spending_limit"
        if amount_micros > self._available():
            return "insufficient_funds"
        return None
