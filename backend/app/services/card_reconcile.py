"""Reconciliation of provider transactions into the ledger (docs/specs/m15, section 4.5).

STEP 0 STUB. ``sync_company`` is the signature WP1 calls; WP2 implements it (upsert transactions
idempotently, match to purchases, write ``verified`` expenses, close / freeze, alert). It is a no-op
until then, so the scheduler and the Wallet can already call it safely.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.integrations.cards.base import CardProvider
from app.models.purchasing import CardAccount
from app.schemas.purchasing import WalletSyncResult

ProviderFactory = Callable[[Session, CardAccount], CardProvider]


def sync_company(
    db: Session,
    company_id: str,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> WalletSyncResult:
    """Poll every active card account of the company (``list_transactions(since=cursor - 3 days)``)
    and reconcile. ``provider_factory`` lets tests inject the fake provider."""
    return WalletSyncResult(accounts_synced=0, transactions_seen=0, transactions_new=0, errors=[])


def run_all(db: Session, *, now: datetime | None = None) -> int:
    """Scheduler pass (every 15 minutes): ``sync_company`` for each company with an active card
    account. Returns the number of companies synced."""
    return 0


def check_balances(db: Session, *, now: datetime | None = None) -> int:
    """Hourly pass: raises ``wallet_low_balance`` when the balance does not cover holds, renewals
    due in 30 days and a buffer. Returns the number of alerts raised."""
    return 0
