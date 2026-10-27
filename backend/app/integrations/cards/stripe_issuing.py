"""Stripe Issuing adapter (docs/specs/m15, sections 4.2 and 4.3).

IMPLEMENTED AGAINST STRIPE'S DOCUMENTED API, NOT VERIFIED AGAINST THE LIVE API. It is unit-tested
with ``httpx.MockTransport`` fixtures modelled on the documented object shapes; no request was ever
sent to ``api.stripe.com`` while writing it. Details I could not confirm are listed here so that a
person running it against a Stripe **sandbox** key (``foundryos wallet check``) knows what to look
at first:

* ``lifecycle_controls[cancel_after][payment_count]=1`` as the way to make a card single-use. If
  Stripe rejects or ignores it, reconciliation still closes the card after the first capture.
* the sign of ``amount`` on Issuing transactions (the mapping uses ``abs`` and the transaction
  ``type``, so it holds either way), and ``request_history[].reason`` values on declines.
* ``POST /v1/funding_instructions`` as the source of live Issuing funding instructions (needs
  more than the restricted key of the setup guide, so it falls back to dashboard steps).
* the dashboard URL shapes used for ``reveal_hint``.
* ``POST /v1/issuing/cardholders`` field names (``ensure_cardholder``).
* that a TTL cannot be set on a card: ``expires_at`` is not sent, our own sweep closes the card.

What is firm by design (and tested):

* **No method returns a card number or CVC.** The client never sends ``expand`` with ``number`` or
  ``cvc`` (``request`` refuses it), and there is no code path that reads those fields.
* We do not use the 2 s real-time authorization webhook. Static card controls (spending limits,
  allowed categories) do the enforcement; the webhooks only trigger a sync.
* Only ``spending_controls`` that the owner's approval scope allows are sent. Automation never
  raises a limit (``update_limit`` refuses it unless the adapter was built with ``allow_raise``,
  which no automation path does).
* The key is a restricted key (Issuing cards and cardholders write; authorizations, transactions
  and balance read). It never leaves the control plane and is masked from every error.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.time import utcnow
from app.integrations.base import micros_to_cents
from app.integrations.cards._http import (
    CardApiClient,
    cents_to_micros,
    from_unix,
    status_of,
)
from app.integrations.cards.base import (
    Balance,
    Capabilities,
    CardProviderError,
    CardTxn,
    IssuedCard,
    MerchantLock,
    RevealHint,
)

WEBHOOK_TOLERANCE_SECONDS = 300
PAGE_SIZE = 100
FORBIDDEN_EXPAND = ("number", "cvc")
STRIPE_INTERVALS = ("per_authorization", "daily", "weekly", "monthly", "yearly", "all_time")

_DECLINE_REASONS = {
    "insufficient_funds": "insufficient_funds",
    "spending_controls": "spending_limit",
    "card_inactive": "card_inactive",
    "cardholder_inactive": "card_inactive",
    "account_disabled": "account_disabled",
    "webhook_declined": "declined",
    "webhook_error": "declined",
    "webhook_timeout": "declined",
}


class StripeIssuingProvider(CardApiClient):
    id = "stripe_issuing"
    provider = "stripe_issuing"
    base_url = "https://api.stripe.com"
    capabilities = Capabilities(
        issue_cards=True,
        single_use=True,
        merchant_mcc_lock=True,
        per_auth_limit=True,
        interval_limits=("per_authorization", "monthly", "yearly", "all_time"),
        list_transactions=True,
        balance=True,
        webhooks=True,
        programmatic_checkout=False,  # M16
        test_mode=True,
    )

    def __init__(
        self,
        http: httpx.Client,
        api_key: str,
        *,
        cardholder_id: str | None = None,
        webhook_secret: str | None = None,
        allow_raise: bool = False,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(http, api_key, webhook_secret)
        self._key = api_key
        self.cardholder_id = cardholder_id
        self.webhook_secret = webhook_secret
        self.allow_raise = allow_raise
        self._clock = clock

    # ------------------------------------------------------------------ plumbing

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    @property
    def test_mode(self) -> bool:
        return "_test_" in self._key

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        _refuse_card_data(kwargs.get("params"), kwargs.get("data"), kwargs.get("json"))
        return super().request(method, path, **kwargs)

    def _form(self, method: str, path: str, *, what: str, data: dict[str, Any], key: str | None):
        headers = {"Idempotency-Key": key} if key else None
        return self.json(method, path, what=what, data=data, headers=headers)

    # ------------------------------------------------------------------ CardProvider

    def check(self) -> dict:
        """Authenticated, Issuing enabled, balance readable, test or live. Creates no card."""
        out: dict[str, Any] = {
            "ok": False,
            "mode": "test" if self.test_mode else "live",
            "issuing_enabled": False,
            "balance_readable": False,
            "balance_micros": None,
        }
        try:
            balance = self.json("GET", "/v1/balance", what="read the balance")
            out["balance_readable"] = True
            if isinstance(balance.get("livemode"), bool):
                out["mode"] = "live" if balance["livemode"] else "test"
            out["balance_micros"] = _issuing_available(balance)
        except CardProviderError as exc:
            if status_of(exc) == 401:
                raise
            out["message"] = exc.message
        try:
            self.json("GET", "/v1/issuing/cards", what="list cards", params={"limit": 1})
            out["issuing_enabled"] = True
        except CardProviderError as exc:
            if status_of(exc) == 401:
                raise
            out["message"] = exc.message
        if self.cardholder_id:
            try:
                holder = self.json(
                    "GET", f"/v1/issuing/cardholders/{self.cardholder_id}", what="read cardholder"
                )
                out["cardholder_active"] = holder.get("status") == "active"
            except CardProviderError as exc:
                out["cardholder_active"] = False
                out.setdefault("message", exc.message)
        out["ok"] = bool(out["issuing_enabled"])
        return out

    def ensure_cardholder(
        self,
        *,
        name: str,
        email: str,
        phone_number: str,
        billing_address: Mapping[str, str],
        idempotency_key: str,
    ) -> str:
        """Create the one cardholder ("<Company> agents") that owns every agent card and return its
        id. The phone number is the human's (3DS codes go to a person)."""
        data: dict[str, Any] = {
            "type": "company",
            "name": name,
            "email": email,
            "phone_number": phone_number,
            "status": "active",
        }
        for field in ("line1", "line2", "city", "state", "postal_code", "country"):
            if billing_address.get(field):
                data[f"billing[address][{field}]"] = billing_address[field]
        body = self._form(
            "POST",
            "/v1/issuing/cardholders",
            what="create the cardholder",
            data=data,
            key=idempotency_key,
        )
        self.cardholder_id = str(body["id"])
        return self.cardholder_id

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
        if not self.cardholder_id:
            raise CardProviderError("stripe_issuing: no cardholder is configured for this account")
        if limit_micros <= 0:
            raise CardProviderError("stripe_issuing: the card limit must be positive")
        if limit_interval not in STRIPE_INTERVALS:
            raise CardProviderError(f"stripe_issuing: unsupported limit interval {limit_interval}")
        bad = set(metadata) - {"cos_purchase", "cos_company"}
        if bad:
            raise CardProviderError("stripe_issuing: card metadata may only carry ids")
        data: dict[str, Any] = {
            "cardholder": self.cardholder_id,
            "currency": "usd",
            "type": "virtual",
            "status": "active",
            "spending_controls[spending_limits][0][amount]": micros_to_cents(limit_micros),
            "spending_controls[spending_limits][0][interval]": limit_interval,
        }
        if merchant_lock and merchant_lock.mcc_allow:
            data["spending_controls[allowed_categories][]"] = list(merchant_lock.mcc_allow)
        if single_use:
            data["lifecycle_controls[cancel_after][payment_count]"] = 1
        for key, value in metadata.items():
            data[f"metadata[{key}]"] = value
        body = self._form(
            "POST", "/v1/issuing/cards", what="issue a card", data=data, key=idempotency_key
        )
        return _issued(body, fallback_single_use=single_use)

    def update_limit(
        self,
        provider_card_id: str,
        *,
        limit_micros: int,
        limit_interval: str,
        idempotency_key: str,
    ) -> IssuedCard:
        if limit_micros <= 0 or limit_interval not in STRIPE_INTERVALS:
            raise CardProviderError("stripe_issuing: invalid limit")
        current = _issued(self._get_card(provider_card_id))
        if not self.allow_raise and limit_micros > current.limit_micros:
            raise CardProviderError(
                "stripe_issuing: raising a card limit is a human action; "
                "automation may only lower it"
            )
        data = {
            "spending_controls[spending_limits][0][amount]": micros_to_cents(limit_micros),
            "spending_controls[spending_limits][0][interval]": limit_interval,
        }
        body = self._form(
            "POST",
            f"/v1/issuing/cards/{provider_card_id}",
            what="update the card limit",
            data=data,
            key=idempotency_key,
        )
        return _issued(body, fallback_single_use=current.single_use)

    def freeze(self, provider_card_id: str) -> None:
        if self._get_card(provider_card_id).get("status") == "canceled":
            return
        self._set_status(provider_card_id, "inactive", "freeze the card")

    def unfreeze(self, provider_card_id: str) -> None:
        if self._get_card(provider_card_id).get("status") == "canceled":
            raise CardProviderError("stripe_issuing: a canceled card cannot be unfrozen")
        self._set_status(provider_card_id, "active", "unfreeze the card")

    def close(self, provider_card_id: str) -> None:
        if self._get_card(provider_card_id).get("status") == "canceled":
            return  # Stripe already closed it (single-use cards cancel themselves)
        self._set_status(provider_card_id, "canceled", "close the card")

    def list_transactions(
        self, *, since: datetime, cursor: str | None = None
    ) -> tuple[list[CardTxn], str | None]:
        """Captures and refunds (``/v1/issuing/transactions``) first, then authorizations
        (``/v1/issuing/authorizations``: pending holds and declines). The cursor is opaque:
        ``txn:<starting_after>`` / ``auth:<starting_after>``."""
        phase, _, after = (cursor or "txn:").partition(":")
        if phase not in ("txn", "auth"):
            raise CardProviderError("stripe_issuing: invalid cursor")
        params: dict[str, Any] = {"limit": PAGE_SIZE, "created[gte]": int(since.timestamp())}
        if after:
            params["starting_after"] = after
        path = "/v1/issuing/transactions" if phase == "txn" else "/v1/issuing/authorizations"
        body = self.json("GET", path, what=f"list {phase} records", params=params)
        rows = [r for r in body.get("data", []) if isinstance(r, dict)]
        mapper = _map_transaction if phase == "txn" else _map_authorization
        items = [t for t in (mapper(r) for r in rows) if t is not None]
        if body.get("has_more") and rows:
            return items, f"{phase}:{rows[-1]['id']}"
        return items, ("auth:" if phase == "txn" else None)

    def balance(self) -> Balance:
        body = self.json("GET", "/v1/balance", what="read the balance")
        available = _issuing_available(body)
        if available is None:
            raise CardProviderError("stripe_issuing: the Issuing balance is not readable")
        return Balance(available_micros=available, pending_micros=None, as_of=self._clock())

    def funding_instructions(self) -> dict:
        """Live instructions for adding funds to the Issuing balance, shown to the owner only. When
        the restricted key cannot read them (or the call differs from the docs) the dashboard path
        is returned instead; the owner always has that path."""
        fallback = {
            "method": "Stripe Dashboard",
            "where": "Dashboard > Balances > Issuing balance > Add funds",
            "how": "ACH or wire from your US bank account to the routing and account number "
            "Stripe shows there (about 1 business day), or transfer from your Stripe balance.",
        }
        try:
            body = self.json(
                "POST",
                "/v1/funding_instructions",
                what="read funding instructions",
                data={
                    "funding_type": "bank_transfer",
                    "bank_transfer[type]": "us_bank_transfer",
                    "currency": "usd",
                },
            )
        except CardProviderError:
            return fallback
        out = dict(fallback)
        transfer = (body.get("bank_transfer") or {}) if isinstance(body, dict) else {}
        for address in transfer.get("financial_addresses", []) or []:
            aba = address.get("aba") or {}
            for src, label in (
                ("routing_number", "routing number"),
                ("account_number", "account number"),
                ("bank_name", "bank"),
            ):
                if aba.get(src):
                    out[label] = str(aba[src])
            break
        return out

    def reveal_hint(self, provider_card_id: str) -> RevealHint:
        prefix = (
            "https://dashboard.stripe.com/test"
            if self.test_mode
            else "https://dashboard.stripe.com"
        )
        return RevealHint("provider_dashboard", f"{prefix}/issuing/cards/{provider_card_id}")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]:
        event = verify_webhook(headers, body, secret, now=self._clock())
        return transactions_from_event(event)

    # ------------------------------------------------------------------ internals

    def _get_card(self, provider_card_id: str) -> dict[str, Any]:
        return self.json("GET", f"/v1/issuing/cards/{provider_card_id}", what="read the card")

    def _set_status(self, provider_card_id: str, status: str, what: str) -> None:
        self._form(
            "POST",
            f"/v1/issuing/cards/{provider_card_id}",
            what=what,
            data={"status": status},
            key=None,
        )


# ---------------------------------------------------------------------- webhooks


def verify_webhook(
    headers: Mapping[str, str],
    body: bytes,
    secret: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check a ``Stripe-Signature`` header (``t=<unix>,v1=<hmac>``: HMAC-SHA256 of ``<t>.<body>``)
    within the 5 minute tolerance and return the parsed event. Raises ``CardProviderError``."""
    lowered = {k.lower(): v for k, v in headers.items()}
    header = lowered.get("stripe-signature", "")
    parts: dict[str, list[str]] = {}
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts.setdefault(key, []).append(value)
    stamp = (parts.get("t") or [""])[0]
    if not secret or not stamp.isdigit() or not parts.get("v1"):
        raise CardProviderError("invalid webhook signature")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts["v1"]):
        raise CardProviderError("invalid webhook signature")
    current = (now or utcnow()).astimezone(UTC)
    if abs(current.timestamp() - int(stamp)) > WEBHOOK_TOLERANCE_SECONDS:
        raise CardProviderError("webhook timestamp outside the tolerance")
    try:
        event = json.loads(body)
    except ValueError as exc:
        raise CardProviderError("invalid webhook body") from exc
    if not isinstance(event, dict):
        raise CardProviderError("invalid webhook body")
    return event


def transactions_from_event(event: Mapping[str, Any]) -> list[CardTxn]:
    kind = str(event.get("type", ""))
    obj = ((event.get("data") or {}).get("object")) or {}
    if not isinstance(obj, dict):
        return []
    mapped = None
    if kind.startswith("issuing_transaction."):
        mapped = _map_transaction(obj)
    elif kind.startswith("issuing_authorization."):
        mapped = _map_authorization(obj)
    return [mapped] if mapped else []


# ---------------------------------------------------------------------- mapping


def _refuse_card_data(*bags: Any) -> None:
    """The adapter never asks Stripe for a card number or CVC."""
    for bag in bags:
        if not isinstance(bag, dict):
            continue
        for key, value in bag.items():
            if str(key).startswith("expand"):
                values = value if isinstance(value, (list, tuple)) else [value]
                flat = " ".join(str(v) for v in values) + " " + str(key)
                if any(word in flat for word in FORBIDDEN_EXPAND):
                    raise CardProviderError(
                        "stripe_issuing: expanding card credentials is forbidden"
                    )


def _issuing_available(balance: Mapping[str, Any]) -> int | None:
    issuing = balance.get("issuing")
    if not isinstance(issuing, dict):
        return None
    for entry in issuing.get("available", []) or []:
        if isinstance(entry, dict) and str(entry.get("currency", "")).lower() == "usd":
            return cents_to_micros(entry.get("amount"))
    return 0 if issuing.get("available") == [] else None


def _issued(card: Mapping[str, Any], *, fallback_single_use: bool = False) -> IssuedCard:
    controls = card.get("spending_controls") or {}
    limits = controls.get("spending_limits") or []
    first = limits[0] if limits and isinstance(limits[0], dict) else {}
    status = {"active": "active", "inactive": "frozen", "canceled": "closed"}.get(
        str(card.get("status")), "frozen"
    )
    cancel_after = ((card.get("lifecycle_controls") or {}).get("cancel_after")) or {}
    single_use = cancel_after.get("payment_count") == 1 or (
        fallback_single_use and "lifecycle_controls" not in card
    )
    test = card.get("livemode") is False
    card_id = str(card["id"])
    base = "https://dashboard.stripe.com/test" if test else "https://dashboard.stripe.com"
    return IssuedCard(
        provider_card_id=card_id,
        last4=str(card.get("last4", ""))[-4:],
        brand=str(card["brand"]).lower() if card.get("brand") else None,
        exp_month=card.get("exp_month"),
        exp_year=card.get("exp_year"),
        status=status,
        limit_micros=cents_to_micros(first.get("amount")),
        limit_interval=str(first.get("interval") or "all_time"),
        single_use=bool(single_use),
        reveal=RevealHint("provider_dashboard", f"{base}/issuing/cards/{card_id}"),
    )


def _card_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id", ""))
    return str(value or "")


def _merchant(row: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    data = row.get("merchant_data") or {}
    name = data.get("name")
    mcc = data.get("category_code")
    country = data.get("country")
    return (
        str(name)[:200] if name else None,
        str(mcc)[:8] if mcc else None,
        str(country)[:2].upper() if country else None,
    )


def _map_transaction(row: Mapping[str, Any]) -> CardTxn | None:
    """``issuing.transaction``: type ``capture`` (a settled purchase) or ``refund``."""
    kind = {"capture": "capture", "refund": "refund"}.get(str(row.get("type")))
    if kind is None or not row.get("id"):
        return None
    sign = 1 if kind == "capture" else -1
    cents = abs(int(row.get("amount") or 0))
    name, mcc, country = _merchant(row)
    merchant_minor = row.get("merchant_amount")
    created = from_unix(row.get("created")) or utcnow()
    return CardTxn(
        provider_txn_id=str(row["id"]),
        provider_card_id=_card_id(row.get("card")),
        kind=kind,
        status="settled",
        amount_micros=sign * cents * 10_000,
        merchant_amount_minor=sign * abs(int(merchant_minor))
        if merchant_minor is not None
        else None,
        merchant_currency=str(row["merchant_currency"]).upper()
        if row.get("merchant_currency")
        else None,
        merchant_name=name,
        merchant_mcc=mcc,
        merchant_country=country,
        occurred_at=created,
        settled_at=created,
    )


def _map_authorization(row: Mapping[str, Any]) -> CardTxn | None:
    """``issuing.authorization``: an approved one is a hold (``pending``) until its capture
    arrives as a transaction; a refused one is a ``decline`` with Stripe's reason."""
    if not row.get("id"):
        return None
    name, mcc, country = _merchant(row)
    merchant_minor = row.get("merchant_amount")
    created = from_unix(row.get("created")) or utcnow()
    approved = bool(row.get("approved"))
    reason = None
    if approved:
        kind = "authorization"
        status = "pending" if row.get("status") == "pending" else "reversed"
    else:
        kind, status = "decline", "declined"
        history = [h for h in (row.get("request_history") or []) if isinstance(h, dict)]
        raw = str(history[-1].get("reason", "")) if history else ""
        reason = _DECLINE_REASONS.get(raw, raw[:60] or "declined")
    return CardTxn(
        provider_txn_id=str(row["id"]),
        provider_card_id=_card_id(row.get("card")),
        kind=kind,
        status=status,
        amount_micros=abs(int(row.get("amount") or 0)) * 10_000,
        merchant_amount_minor=abs(int(merchant_minor)) if merchant_minor is not None else None,
        merchant_currency=str(row["merchant_currency"]).upper()
        if row.get("merchant_currency")
        else None,
        merchant_name=name,
        merchant_mcc=mcc,
        merchant_country=country,
        occurred_at=created,
        settled_at=None,
        decline_reason=reason,
    )
