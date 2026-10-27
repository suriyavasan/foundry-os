"""The Wallet: card accounts, cards, provider catalog, funding instructions, reveal hint and the
overview behind the canvas widget (docs/specs/m15, sections 4.8 to 4.10).

STEP 0 STUB. Final signatures; WP1 implements them. Every function raises
``NotAvailable(milestone="M15")``; nothing calls them yet.
"""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.errors import NotAvailable
from app.core.principal import CompanyContext
from app.integrations.cards.base import CardProvider
from app.models.purchasing import AgentCard, CardAccount, CardTransaction
from app.schemas.purchasing import (
    CardAccountCheck,
    CardAccountCreate,
    CardActionRequest,
    CardProviderList,
    CardReveal,
    FundingInstructions,
    WalletOverview,
    WalletSyncResult,
)


def _not_built() -> NoReturn:
    raise NotAvailable("Agent cards and purchasing are not built yet", milestone="M15")


def list_providers(db: Session, ctx: CompanyContext) -> CardProviderList:
    """The catalog with capabilities, eligibility, the human steps and what is verified."""
    _not_built()


def overview(db: Session, ctx: CompanyContext) -> WalletOverview:
    """Members read. Not set up answers ``{available: true, enabled: false, setup: {...}}``."""
    _not_built()


def provider_for(db: Session, account: CardAccount) -> CardProvider:
    """The adapter for an account, built from its connected account's vault secrets (the control
    plane makes every call; agents never see a key). ``fake`` only when the environment is test."""
    _not_built()


def list_accounts(db: Session, ctx: CompanyContext) -> list[CardAccount]:
    _not_built()


def create_account(db: Session, ctx: CompanyContext, data: CardAccountCreate) -> CardAccount:
    """Owner/admin humans only (``card_account.connect``). Audited."""
    _not_built()


def check_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccountCheck:
    """Live check. No card is created in live mode; test mode may create and close a $1 card."""
    _not_built()


def funding_instructions(db: Session, ctx: CompanyContext, account_id: str) -> FundingInstructions:
    """Fetched live for the owner, never cached or stored."""
    _not_built()


def set_default_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccount:
    _not_built()


def disable_account(db: Session, ctx: CompanyContext, account_id: str) -> CardAccount:
    """Disabling freezes the account's cards."""
    _not_built()


def list_cards(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AgentCard], str | None]:
    _not_built()


def freeze_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    _not_built()


def unfreeze_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    """Human only (``card.unfreeze``)."""
    _not_built()


def close_card(
    db: Session, ctx: CompanyContext, card_id: str, data: CardActionRequest
) -> AgentCard:
    _not_built()


def reveal_card(db: Session, ctx: CompanyContext, card_id: str) -> CardReveal:
    """Owner/admin with recent authentication (step-up); rate limited; audited as
    ``card.reveal_opened`` with no card data. Returns a link to the provider's own page."""
    _not_built()


def list_transactions(
    db: Session, ctx: CompanyContext, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[CardTransaction], str | None]:
    _not_built()


def sync(db: Session, ctx: CompanyContext) -> WalletSyncResult:
    """``POST /wallet/sync``: reconcile now (calls ``card_reconcile.sync_company``)."""
    _not_built()


def freeze_all_cards(db: Session, company_id: str, *, reason: str) -> int:
    """Emergency stop / company pause: freeze every active agent card (best effort, one audit line
    per card). Resume does not unfreeze. A no-op until WP1: there are no cards yet."""
    return 0
