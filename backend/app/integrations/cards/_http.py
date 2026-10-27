"""Shared plumbing for the real card adapters (Stripe Issuing, Mercury).

* Errors become ``CardProviderError`` with a ``transient`` flag (network, 429, 5xx are retried with
  backoff by the issuing job; everything else is permanent and raises an alert).
* A rejected credential (401 / 403) is a ``CardProviderAuthError``, which is also a
  ``ProviderAuthError`` so the connected-accounts test reports it like any other provider.
* Secrets are masked out of every message by ``ApiClient`` (``scrub`` also removes card numbers).
* ``USD`` helpers convert the provider's decimal / minor-unit amounts to integer micro-USD.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.integrations.base import ApiClient, ProviderAuthError
from app.integrations.cards.base import CardProviderError

CENTS_TO_MICROS = 10_000


class CardProviderAuthError(CardProviderError, ProviderAuthError):
    """The provider rejected the credential (revoked, wrong scope, expired)."""

    code = "card_provider_auth_failed"
    default_message = "The card provider rejected the credential"


class CardApiClient(ApiClient):
    """``ApiClient`` whose failures are card-provider errors with a transient flag."""

    def _fail(self, status: int | None, text: str, what: str) -> CardProviderError:
        base = super()._fail(status, text, what)
        if status in (401, 403):
            return CardProviderAuthError(base.message, base.details)
        transient = status is None or status == 429 or status >= 500
        return CardProviderError(base.message, base.details, transient=transient)


def status_of(exc: Exception) -> int | None:
    details = getattr(exc, "details", None)
    return details.get("status") if isinstance(details, dict) else None


def cents_to_micros(cents: int | float | str | None) -> int:
    return int(Decimal(str(cents or 0)) * CENTS_TO_MICROS)


def dollars_to_micros(value: Any) -> int | None:
    """``12.5`` / ``"12.50"`` -> 12_500_000 (signed). None for a value that is not a number."""
    try:
        amount = Decimal(str(value).strip())
    except Exception:
        return None
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))


def from_unix(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def from_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
