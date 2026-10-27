"""Mercury adapter (docs/specs/m15, sections 4.2 and 4.3).

IMPLEMENTED FROM MERCURY'S PUBLIC DOCUMENTATION AND CHANGELOG, NOT VERIFIED AGAINST THE LIVE API.
It is unit-tested with ``httpx.MockTransport`` fixtures; no request was ever sent to
``api.mercury.com``. Mercury's reference pages were not reachable while this was written (HTTP 403),
so the following are **assumptions** that a person with a Mercury token must check first:

* the Cards API paths and bodies (``/account/{id}/cards``, a ``nickname``, a ``spendingLimit`` with
  ``amount`` and ``interval``, a ``status`` of ``active`` / ``frozen`` / ``canceled``). They are
  collected in ``_CARDS`` below so a correction is one edit.
* the transactions payload: the endpoint ``/account/{id}/transactions`` and its ``id``, ``amount``
  (signed dollars), ``status`` (``pending`` / ``sent`` / ``cancelled`` / ``failed``), ``kind``
  (``debitCardTransaction``), ``counterpartyName``, ``createdAt`` / ``postedAt`` fields, and that
  the
  card is named in ``details.debitCardInfo.id``. A transaction with no card id is not ours and is
  skipped.
* whether write tokens need an IP allowlist, and the token shape (``secret-token:mercury_...``).
* the app URL used for ``reveal_hint``.

Capability gaps (recorded, not hidden):

* **No single-use cards and no merchant lock.** Single use is *emulated*: the limit equals the
  approved amount and reconciliation closes the card after the first capture (and at its TTL). The
  merchant mismatch check at reconciliation freezes a card whose charge does not look like the
  vendor. ``per_authorization`` and ``all_time`` are therefore sent as a monthly limit of the same
  amount, and a yearly limit as a monthly limit of the yearly amount (a cap, not a schedule).
* **No webhooks** are used (``capabilities.webhooks`` is False); polling is the source of truth.

By design (and tested): the client has **no call that reads card credentials** (Mercury's Vault
API is deliberately not used), so no method here can return a card number. A human opens the card
in the Mercury app (``reveal_hint``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.core.time import utcnow
from app.integrations.cards._http import (
    CardApiClient,
    dollars_to_micros,
    from_iso,
    status_of,
)
from app.integrations.cards.base import (
    Balance,
    Capabilities,
    CardProviderError,
    CardProviderNotAvailable,
    CardTxn,
    IssuedCard,
    MerchantLock,
    RevealHint,
)

PAGE_SIZE = 100
NICKNAME_PREFIX = "cos-"
APP_CARDS_URL = "https://app.mercury.com/cards"

# Assumed Cards API shapes (unverified). One place to correct them.
_CARDS = {
    "list": "/account/{account}/cards",
    "create": "/account/{account}/cards",
    "one": "/account/{account}/cards/{card}",
    "status": {"active": "active", "frozen": "frozen", "closed": "canceled"},
}
MERCURY_INTERVALS = ("daily", "weekly", "monthly")


class MercuryProvider(CardApiClient):
    id = "mercury"
    provider = "mercury"
    base_url = "https://api.mercury.com/api/v1"
    capabilities = Capabilities(
        issue_cards=True,
        single_use=True,  # emulated: limit = amount, closed by reconciliation after use
        merchant_mcc_lock=False,
        per_auth_limit=False,
        interval_limits=(
            "per_authorization",
            "monthly",
            "yearly",
            "all_time",
        ),  # all sent as monthly
        list_transactions=True,
        balance=True,
        webhooks=False,
        programmatic_checkout=False,
        test_mode=True,
    )

    def __init__(
        self,
        http: httpx.Client,
        token: str,
        *,
        account_id: str | None = None,
        allow_raise: bool = False,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(http, token)
        self._token = token
        self.account_id = account_id
        self.allow_raise = allow_raise
        self._clock = clock

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @property
    def test_mode(self) -> bool:
        return "sandbox" in self._token.lower()

    # ------------------------------------------------------------------ helpers

    def _account(self) -> str:
        if self.account_id:
            return self.account_id
        body = self.json("GET", "/accounts", what="list accounts")
        accounts = [a for a in body.get("accounts", []) if isinstance(a, dict) and a.get("id")]
        active = [a for a in accounts if a.get("status", "active") == "active"]
        chosen = (active or accounts or [None])[0]
        if chosen is None:
            raise CardProviderError("mercury: the token can see no account")
        self.account_id = str(chosen["id"])
        return self.account_id

    def _path(self, key: str, **kw: str) -> str:
        return str(_CARDS[key]).format(account=self._account(), **kw)

    def _cards(self) -> list[dict[str, Any]]:
        body = self.json("GET", self._path("list"), what="list cards")
        rows = body.get("cards", body) if isinstance(body, dict) else body
        return [c for c in rows if isinstance(c, dict)] if isinstance(rows, list) else []

    def _get(self, provider_card_id: str) -> dict[str, Any]:
        return self.json("GET", self._path("one", card=provider_card_id), what="read the card")

    # ------------------------------------------------------------------ CardProvider

    def check(self) -> dict:
        out: dict[str, Any] = {
            "ok": False,
            "mode": "test" if self.test_mode else "live",
            "issuing_enabled": False,
            "balance_readable": False,
            "balance_micros": None,
        }
        try:
            account = self.json("GET", f"/account/{self._account()}", what="read the account")
            out["balance_readable"] = True
            out["balance_micros"] = dollars_to_micros(account.get("availableBalance"))
        except CardProviderError as exc:
            if status_of(exc) == 401:
                raise
            out["message"] = exc.message
        try:
            self._cards()
            out["issuing_enabled"] = True
        except CardProviderError as exc:
            if status_of(exc) == 401:
                raise
            out["message"] = exc.message
        out["ok"] = bool(out["issuing_enabled"])
        return out

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
        if limit_micros <= 0:
            raise CardProviderError("mercury: the card limit must be positive")
        if set(metadata) - {"cos_purchase", "cos_company"}:
            raise CardProviderError("mercury: card metadata may only carry ids")
        nickname = f"{NICKNAME_PREFIX}{idempotency_key}"[:60]
        # Mercury has no idempotency key: an existing card with this nickname IS the retry.
        for card in self._cards():
            if card.get("nickname") == nickname and card.get("status") != "canceled":
                return self._issued(card, limit_micros, limit_interval, single_use)
        interval = "monthly"  # see the module docstring: the cap is what matters
        body = self.json(
            "POST",
            self._path("create"),
            what="issue a card",
            json={
                "nickname": nickname,
                "type": "virtual",
                "spendingLimit": {
                    "amount": _dollars(limit_micros),
                    "interval": interval,
                },
            },
        )
        return self._issued(body, limit_micros, limit_interval, single_use)

    def update_limit(
        self,
        provider_card_id: str,
        *,
        limit_micros: int,
        limit_interval: str,
        idempotency_key: str,
    ) -> IssuedCard:
        if limit_micros <= 0:
            raise CardProviderError("mercury: invalid limit")
        current = self._get(provider_card_id)
        was = dollars_to_micros((current.get("spendingLimit") or {}).get("amount")) or 0
        if not self.allow_raise and limit_micros > was:
            raise CardProviderError(
                "mercury: raising a card limit is a human action; automation may only lower it"
            )
        body = self.json(
            "PATCH",
            self._path("one", card=provider_card_id),
            what="update the card limit",
            json={"spendingLimit": {"amount": _dollars(limit_micros), "interval": "monthly"}},
        )
        return self._issued(body, limit_micros, limit_interval, False)

    def freeze(self, provider_card_id: str) -> None:
        if self._get(provider_card_id).get("status") == _CARDS["status"]["closed"]:
            return
        self._status(provider_card_id, "frozen", "freeze the card")

    def unfreeze(self, provider_card_id: str) -> None:
        if self._get(provider_card_id).get("status") == _CARDS["status"]["closed"]:
            raise CardProviderError("mercury: a canceled card cannot be unfrozen")
        self._status(provider_card_id, "active", "unfreeze the card")

    def close(self, provider_card_id: str) -> None:
        if self._get(provider_card_id).get("status") == _CARDS["status"]["closed"]:
            return
        self._status(provider_card_id, "closed", "close the card")

    def list_transactions(
        self, *, since: datetime, cursor: str | None = None
    ) -> tuple[list[CardTxn], str | None]:
        offset = 0
        if cursor:
            prefix, _, value = cursor.partition(":")
            if prefix != "offset" or not value.isdigit():
                raise CardProviderError("mercury: invalid cursor")
            offset = int(value)
        body = self.json(
            "GET",
            f"/account/{self._account()}/transactions",
            what="list transactions",
            params={
                "limit": PAGE_SIZE,
                "offset": offset,
                "start": since.date().isoformat(),
                "order": "asc",
            },
        )
        rows = [r for r in body.get("transactions", []) if isinstance(r, dict)]
        items = [t for t in (_map_transaction(r) for r in rows) if t is not None]
        total = body.get("total")
        more = isinstance(total, int) and offset + len(rows) < total and bool(rows)
        return items, (f"offset:{offset + len(rows)}" if more else None)

    def balance(self) -> Balance:
        account = self.json("GET", f"/account/{self._account()}", what="read the account")
        available = dollars_to_micros(account.get("availableBalance"))
        if available is None:
            raise CardProviderError("mercury: the account balance is not readable")
        current = dollars_to_micros(account.get("currentBalance"))
        pending = current - available if current is not None else None
        return Balance(available_micros=available, pending_micros=pending, as_of=self._clock())

    def funding_instructions(self) -> dict:
        """The account's own deposit details (owner-only display, fetched live, never stored)."""
        out = {
            "method": "Wire (SWIFT or domestic) or ACH to your Mercury account",
            "note": "From India this is an overseas investment under FEMA: use your bank's ODI "
            "process and ask your CA first.",
        }
        try:
            account = self.json("GET", f"/account/{self._account()}", what="read the account")
        except CardProviderError:
            return out
        for src, label in (
            ("routingNumber", "routing number"),
            ("accountNumber", "account number"),
            ("name", "account name"),
        ):
            if account.get(src):
                out[label] = str(account[src])
        return out

    def reveal_hint(self, provider_card_id: str) -> RevealHint:
        return RevealHint("provider_dashboard", APP_CARDS_URL)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]:
        raise CardProviderNotAvailable("mercury: webhooks are not used; polling is the source")

    # ------------------------------------------------------------------ internals

    def _status(self, provider_card_id: str, ours: str, what: str) -> None:
        self.json(
            "PATCH",
            self._path("one", card=provider_card_id),
            what=what,
            json={"status": _CARDS["status"][ours]},
        )

    def _issued(
        self, card: Mapping[str, Any], limit_micros: int, interval: str, single_use: bool
    ) -> IssuedCard:
        status = {"active": "active", "frozen": "frozen", "canceled": "closed"}.get(
            str(card.get("status")), "frozen"
        )
        limit = dollars_to_micros((card.get("spendingLimit") or {}).get("amount"))
        return IssuedCard(
            provider_card_id=str(card["id"]),
            last4=str(card.get("lastFourDigits") or card.get("last4") or "")[-4:],
            brand=str(card["network"]).lower() if card.get("network") else None,
            exp_month=card.get("expMonth"),
            exp_year=card.get("expYear"),
            status=status,
            limit_micros=limit if limit is not None else limit_micros,
            limit_interval=interval,
            single_use=single_use,
            reveal=RevealHint("provider_dashboard", APP_CARDS_URL),
        )


def _dollars(micros: int) -> float:
    """Whole cents, rounded up (never under-authorize), as the JSON number Mercury expects."""
    cents = -(-micros // 10_000)
    return float(Decimal(cents) / 100)


def _map_transaction(row: Mapping[str, Any]) -> CardTxn | None:
    """A debit-card transaction of the account; wires, ACH and transfers are not ours."""
    details = (row.get("details") or {}).get("debitCardInfo") or {}
    card_id = details.get("id")
    if not card_id or not row.get("id"):
        return None
    signed = dollars_to_micros(row.get("amount"))
    if signed is None:
        return None
    spend = -signed  # Mercury: a debit is negative
    raw_status = str(row.get("status", ""))
    created = from_iso(row.get("createdAt")) or utcnow()
    posted = from_iso(row.get("postedAt"))
    name = row.get("counterpartyName") or row.get("bankDescription")
    reason = None
    if raw_status == "failed":
        kind, status, reason = "decline", "declined", "declined"
    elif raw_status == "cancelled":
        kind, status = "reversal", "reversed"
    elif spend < 0:
        kind, status = "refund", ("settled" if raw_status == "sent" else "pending")
    elif raw_status == "sent":
        kind, status = "capture", "settled"
    else:
        kind, status = "authorization", "pending"
    return CardTxn(
        provider_txn_id=str(row["id"]),
        provider_card_id=str(card_id),
        kind=kind,
        status=status,
        amount_micros=spend,
        merchant_amount_minor=None,
        merchant_currency=None,
        merchant_name=str(name)[:200] if name else None,
        merchant_mcc=None,
        merchant_country=None,
        occurred_at=created,
        settled_at=(posted or created) if status == "settled" else None,
        decline_reason=reason,
    )
