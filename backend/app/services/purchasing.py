"""Purchase requests: validation, the deterministic band decision, the approval, the issuance job,
mark-paid and cancel (docs/specs/m15, sections 4.4 and 4.5).

STEP 0 STUB. These are the final signatures; WP1 implements them. Until then every function raises
``NotAvailable(milestone="M15")``, nothing calls them, and no existing path changes behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.errors import NotAvailable
from app.core.principal import CompanyContext
from app.integrations.cards.base import CardProvider
from app.models.purchasing import PurchaseRequest
from app.schemas.purchasing import (
    PurchaseAck,
    PurchaseCancelRequest,
    PurchaseDecisionRequest,
    PurchaseMarkPaid,
    PurchaseRequestCreate,
)


def _not_built() -> NoReturn:
    raise NotAvailable("Agent cards and purchasing are not built yet", milestone="M15")


@dataclass(frozen=True)
class BandDecision:
    """Result of the deterministic policy (section 4.4.2): who must approve and why."""

    decision: str  # auto | cofounder | human | report_only | deny
    reason: str
    card_limit_micros: int  # amount + max(bps of amount, minimum): room for tax and FX


def decide_band(
    db: Session,
    company_id: str,
    agent_id: str,
    *,
    amount_micros: int,
    recurrence: str,
    vendor_domain: str,
    now: datetime | None = None,
) -> BandDecision:
    """Deny / human / cofounder / auto from the policy, the monthly totals, the recurring and
    new-vendor floors and the guardrail rules (the strictest wins). Pure reads, no side effects."""
    _not_built()


def request_purchase(
    db: Session, ctx: CompanyContext, data: PurchaseRequestCreate, *, run_id: str | None = None
) -> PurchaseRequest:
    """Agent entry (``POST /api/agent/v1/purchases``): validates, dedupes, decides, and either
    approves, parks behind an Approval, or degrades to a human task. Audited in one transaction."""
    _not_built()


def ack_for(purchase: PurchaseRequest) -> PurchaseAck:
    """The agent's answer: id, status, decision and a plain-language next step; no card data."""
    _not_built()


def get_purchase(db: Session, ctx: CompanyContext, purchase_id: str) -> PurchaseRequest:
    """Members read; an agent reads only its own. Another company's id is a 404."""
    _not_built()


def list_purchases(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    agent_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[PurchaseRequest], str | None]:
    _not_built()


def approve(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseDecisionRequest
) -> PurchaseRequest:
    """Human approve through the Wallet: a thin wrapper over ``approvals.decide``."""
    _not_built()


def reject(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseDecisionRequest
) -> PurchaseRequest:
    _not_built()


def issue_card(
    db: Session, purchase_id: str, *, provider: CardProvider | None = None
) -> PurchaseRequest:
    """The idempotent issuing job: consumes the approval, issues the card (after commit, with the
    purchase id as idempotency key), creates the checkout task. Transient provider errors back off,
    permanent ones alert; it never falls back to another provider or account."""
    _not_built()


def mark_paid(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseMarkPaid
) -> PurchaseRequest:
    """Manual mode: the amount the human confirms is recorded ``verified``, source ``human``."""
    _not_built()


def cancel(
    db: Session, ctx: CompanyContext, purchase_id: str, data: PurchaseCancelRequest
) -> PurchaseRequest:
    """Human cancel: closes the card, releases the hold, wakes the agent."""
    _not_built()


def expire_stale(db: Session, company_id: str, *, now: datetime | None = None) -> int:
    """Scheduler pass: closes unused cards past their TTL and expires the request. Returns the
    number of requests expired."""
    return 0
