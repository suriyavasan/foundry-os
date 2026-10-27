"""Subscriptions: created when a recurring purchase settles, reviewed by the owning agent before
each renewal, cancelled by a human on the vendor's site (docs/specs/m15, section 4.5.3).

STEP 0 STUB. Final signatures. The read/write functions raise ``NotAvailable(milestone="M15")``;
``check`` is a no-op so the daily scheduler pass can already call it.
"""

from __future__ import annotations

from datetime import datetime
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.errors import NotAvailable
from app.core.principal import CompanyContext
from app.models.purchasing import PurchaseRequest, Subscription
from app.schemas.purchasing import SubscriptionHumanAction, SubscriptionReviewIn


def _not_built() -> NoReturn:
    raise NotAvailable("Agent cards and purchasing are not built yet", milestone="M15")


def create_from_settled(db: Session, purchase: PurchaseRequest) -> Subscription:
    """Called by reconciliation when a recurring purchase settles."""
    _not_built()


def list_subscriptions(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[Subscription], str | None]:
    _not_built()


def get_subscription(db: Session, ctx: CompanyContext, subscription_id: str) -> Subscription:
    _not_built()


def review(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionReviewIn
) -> Subscription:
    """Agent entry (``company_review_subscription``): keep, or cancel (a human cancel task)."""
    _not_built()


def mark_cancelled(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionHumanAction
) -> Subscription:
    """Human: the vendor subscription is cancelled; the card closes after the paid period."""
    _not_built()


def keep(
    db: Session, ctx: CompanyContext, subscription_id: str, data: SubscriptionHumanAction
) -> Subscription:
    _not_built()


def check(db: Session, company_id: str | None = None, *, now: datetime | None = None) -> int:
    """Daily pass: renewal reviews, cancel reminders, price-increase and missed-renewal alerts.
    Returns the number of subscriptions acted on."""
    return 0
