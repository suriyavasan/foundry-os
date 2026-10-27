"""The ``manual`` provider: the owner's own card, which Foundry cannot issue, limit or read.

Nothing here talks to a network. A manual account stores a label, a brand and the LAST FOUR digits
(display only); every purchase on it is a human checkout where the owner pays with their own card
and confirms the amount ("Mark paid"), recorded ``verified`` with source ``human``. There are no
provider transactions to poll, no balance and nothing to freeze, so those operations are not
available rather than faked (AGENTS.md rule 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.integrations.cards.base import (
    Balance,
    Capabilities,
    CardProviderNotAvailable,
    CardTxn,
    IssuedCard,
    MerchantLock,
    RevealHint,
)


def _no(what: str) -> CardProviderNotAvailable:
    return CardProviderNotAvailable(
        f"A manual card cannot {what}: Foundry only tracks it; you pay with your own card"
    )


class ManualCardProvider:
    id = "manual"
    capabilities = Capabilities(
        issue_cards=False,
        single_use=False,
        merchant_mcc_lock=False,
        per_auth_limit=False,
        interval_limits=(),
        list_transactions=False,
        balance=False,
        webhooks=False,
        programmatic_checkout=False,
        test_mode=False,
    )

    def __init__(self, *, label: str = "", brand: str | None = None, last4: str | None = None):
        self.label = label
        self.brand = brand
        self.last4 = last4 if last4 and len(last4) == 4 and last4.isdigit() else None

    def check(self) -> dict:
        return {
            "ok": True,
            "mode": "live",
            "issuing_enabled": False,
            "balance_readable": False,
            "balance_micros": None,
            "message": "Manual card: nothing to verify, you pay with your own card.",
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
        raise _no("issue cards")

    def update_limit(
        self,
        provider_card_id: str,
        *,
        limit_micros: int,
        limit_interval: str,
        idempotency_key: str,
    ) -> IssuedCard:
        raise _no("change a limit")

    def freeze(self, provider_card_id: str) -> None:
        raise _no("be frozen")

    def unfreeze(self, provider_card_id: str) -> None:
        raise _no("be unfrozen")

    def close(self, provider_card_id: str) -> None:
        raise _no("be closed")

    def list_transactions(
        self, *, since: datetime, cursor: str | None = None
    ) -> tuple[list[CardTxn], str | None]:
        return [], None  # the owner confirms each payment ("Mark paid")

    def balance(self) -> Balance:
        raise _no("report a balance")

    def funding_instructions(self) -> dict:
        return {
            "method": "Your own card",
            "note": "Nothing to fund here: you pay with your own card and keep its own limit.",
        }

    def reveal_hint(self, provider_card_id: str) -> RevealHint:
        raise _no("reveal a number")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]:
        raise _no("receive webhooks")
