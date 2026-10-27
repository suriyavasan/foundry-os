"""The card provider interface (docs/specs/m15, section 4.2).

Hard rule, enforced by tests: **no method and no dataclass here can carry a card number or CVC**.
``IssuedCard`` has no ``pan`` / ``cvc`` / ``number`` field and ``reveal_hint`` returns a link to the
provider's own page, which a human opens. The server therefore stays out of PCI DSS
cardholder-data scope. Adapters take their secrets in the constructor for the lifetime of one
operation and never put them in an error message (``ProviderError`` masks them).

Amounts are integer micro-USD (``*_micros``). A provider that reports another currency fills the
``merchant_*`` fields and converts to USD for ``amount_micros`` (the provider's own conversion).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.integrations.base import ProviderError


class CardProviderError(ProviderError):
    """A card provider call failed. ``transient`` tells the issuing job whether to retry with
    backoff (network, 429, 5xx) or raise an alert (rejected request, bad credential)."""

    code = "card_provider_error"
    default_message = "The card provider returned an error"

    def __init__(
        self, message: str | None = None, details: object = None, *, transient: bool = False
    ) -> None:
        super().__init__(message, details)
        self.transient = transient


class CardProviderNotAvailable(CardProviderError):
    """The provider (or this capability of it) cannot do that, e.g. ``issue_card`` on ``manual``."""

    status_code = 501
    code = "card_provider_not_available"
    default_message = "The card provider does not support that"


@dataclass(frozen=True)
class MerchantLock:
    name: str | None = None  # display / matching only
    domain: str | None = None  # the vendor_domain we expect (matching only)
    mcc_allow: tuple[str, ...] = ()  # provider category codes, if supported
    country: str | None = None


@dataclass(frozen=True)
class RevealHint:
    """How a HUMAN can see the card number: a page on the provider's own surface."""

    kind: str  # "provider_dashboard" (M16: "issuing_elements")
    url: str


@dataclass(frozen=True)
class IssuedCard:  # NOTE: no pan / cvc fields, by design
    provider_card_id: str
    last4: str
    brand: str | None
    exp_month: int | None
    exp_year: int | None
    status: str  # active | frozen | closed
    limit_micros: int
    limit_interval: str  # per_authorization | monthly | yearly | all_time
    single_use: bool
    reveal: RevealHint


@dataclass(frozen=True)
class CardTxn:
    provider_txn_id: str
    provider_card_id: str
    kind: str  # authorization | capture | refund | decline | reversal
    status: str  # pending | settled | reversed | declined
    amount_micros: int  # USD, signed (a refund is negative)
    merchant_amount_minor: int | None
    merchant_currency: str | None
    merchant_name: str | None
    merchant_mcc: str | None
    merchant_country: str | None
    occurred_at: datetime
    settled_at: datetime | None
    # Provider's reason on a decline (e.g. "insufficient_funds", "spending_limit"); never PII.
    decline_reason: str | None = None


@dataclass(frozen=True)
class Balance:
    available_micros: int | None
    pending_micros: int | None
    as_of: datetime
    currency: str = "usd"


@dataclass(frozen=True)
class Capabilities:
    issue_cards: bool  # manual: False
    single_use: bool  # stripe: True, mercury: emulated
    merchant_mcc_lock: bool  # stripe: True, mercury: False (unverified)
    per_auth_limit: bool
    interval_limits: tuple[str, ...]
    list_transactions: bool
    balance: bool
    webhooks: bool
    programmatic_checkout: bool  # M16
    test_mode: bool


@runtime_checkable
class CardProvider(Protocol):
    id: str  # "stripe_issuing" | "mercury" | "manual" | "fake"
    capabilities: Capabilities

    def check(self) -> dict:
        """Auth ok, issuing enabled, balance readable, mode (test/live). Creates no card in live
        mode. Never contains a key."""
        ...

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
        """``metadata`` carries only ``{"cos_purchase": <id>, "cos_company": <id>}``, never task
        text. The same ``idempotency_key`` returns the same card."""
        ...

    def update_limit(
        self,
        provider_card_id: str,
        *,
        limit_micros: int,
        limit_interval: str,
        idempotency_key: str,
    ) -> IssuedCard:
        """Automation only ever calls this to LOWER a limit; raising one is a human action."""
        ...

    def freeze(self, provider_card_id: str) -> None: ...

    def unfreeze(self, provider_card_id: str) -> None:
        """Human-only path."""
        ...

    def close(self, provider_card_id: str) -> None: ...

    def list_transactions(
        self, *, since: datetime, cursor: str | None = None
    ) -> tuple[list[CardTxn], str | None]:
        """One page of transactions at or after ``since`` and the cursor of the next page."""
        ...

    def balance(self) -> Balance: ...

    def funding_instructions(self) -> dict:
        """Owner-only display, fetched live, never stored."""
        ...

    def reveal_hint(self, provider_card_id: str) -> RevealHint: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]:
        """Optional. Verifies the signature (raises ``CardProviderError`` when it is wrong) and
        returns the transactions the event carries; callers only use it to trigger a sync."""
        ...
