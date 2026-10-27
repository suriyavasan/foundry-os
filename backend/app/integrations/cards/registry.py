"""Provider id -> adapter, built from a card account and its connected account (the vault).

The only place that turns a ``CardAccount`` into something that can call a provider. Secrets are
decrypted for the lifetime of one operation inside ``connected_accounts.client_for`` and never leave
the adapter. ``fake`` is NOT a production provider: it resolves only to an instance a test has
registered with ``register_test_provider`` and is otherwise refused.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.integrations.cards.base import (
    CardProvider,
    CardProviderError,
    CardProviderNotAvailable,
    CardTxn,
)
from app.integrations.cards.manual import ManualCardProvider
from app.integrations.cards.stripe_issuing import transactions_from_event, verify_webhook

if TYPE_CHECKING:
    from app.models.purchasing import CardAccount

REAL_PROVIDERS = ("stripe_issuing", "mercury")
WEBHOOK_PROVIDERS = ("stripe_issuing",)

# Tests only: card account id -> an in-memory provider. Empty in production.
_TEST_PROVIDERS: dict[str, CardProvider] = {}


def register_test_provider(account_id: str, provider: CardProvider) -> None:
    _TEST_PROVIDERS[account_id] = provider


def clear_test_providers() -> None:
    _TEST_PROVIDERS.clear()


def _wallet_override(account_id: str) -> CardProvider | None:
    """The instance a test installed through the Wallet service's ``PROVIDER_OVERRIDES``, so the
    scheduler and the webhook see the same provider as the Wallet routes do."""
    try:
        from app.services import wallet  # local: the wallet service imports this package lazily
    except Exception:  # pragma: no cover - a partial checkout
        return None
    return getattr(wallet, "PROVIDER_OVERRIDES", {}).get(account_id)


def provider_for(db: Session, account: CardAccount) -> CardProvider:
    """The adapter of ``account``. The caller closes nothing: HTTP clients are short-lived and the
    adapter owns its own (``release`` closes it)."""
    found = _TEST_PROVIDERS.get(account.id) or _wallet_override(account.id)
    if found is not None:
        return found
    if account.provider == "manual":
        return ManualCardProvider(
            label=account.label, brand=account.manual_brand, last4=account.manual_last4
        )
    if account.provider == "fake":
        raise CardProviderNotAvailable("The fake card provider exists only in tests")
    if account.provider not in REAL_PROVIDERS:
        raise CardProviderNotAvailable(f"Unknown card provider {account.provider!r}")
    ic = db.info.get("integrations")
    if ic is None:
        raise CardProviderError("card providers are not available in this context")
    if not account.connected_account_id:
        raise CardProviderError("This card account has no connected provider account")
    from app.models.reachability import ConnectedAccount
    from app.services import connected_accounts

    connected = db.get(ConnectedAccount, account.connected_account_id)
    if connected is None or connected.company_id != account.company_id:
        raise CardProviderError("The connected provider account was not found")
    client = connected_accounts.client_for(db, ic, connected)
    if account.external_ref:  # the cardholder / bank account chosen at setup wins over metadata
        if account.provider == "stripe_issuing":
            client.cardholder_id = account.external_ref
        else:
            client.account_id = account.external_ref
    return client


def release(provider: Any) -> None:
    """Close the HTTP client of a real adapter (a no-op for manual and fake providers)."""
    http = getattr(provider, "http", None)
    close = getattr(http, "close", None)
    if callable(close):
        close()


def webhook_events(
    provider_id: str, headers: Mapping[str, str], body: bytes, secret: str
) -> list[CardTxn]:
    """Verify a provider webhook and return its transactions. Raises ``CardProviderError`` when the
    signature is wrong or the provider has no webhooks. Needs no API key."""
    if provider_id not in WEBHOOK_PROVIDERS:
        raise CardProviderNotAvailable("This provider sends no webhooks")
    return transactions_from_event(verify_webhook(headers, body, secret))


ProviderFactory = Callable[["Session", "CardAccount"], CardProvider]
