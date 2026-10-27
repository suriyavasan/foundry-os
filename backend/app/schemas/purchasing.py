"""M15 request / response models: card accounts, cards, purchase requests, subscriptions, the
wallet overview, the purchasing policy and the agent-API bodies.

No model here has a field that could carry a card number or CVC (a test walks every schema in this
module). Money is integer micro-USD (``*_micros``); amounts that arrive from a person or an agent
as decimal dollars are converted with ``Decimal`` in the service, never with floats. Free text that
came from an agent (item, vendor, url, justification) is untrusted: clients render it as text.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.models.policy import (
    MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS,
    MAX_PURCHASE_AUTO_APPROVE_MICROS,
    MAX_PURCHASE_COFOUNDER_LIMIT_MICROS,
)
from app.schemas.agent import MAX_BUDGET_MICROS
from app.schemas.common import ApiModel, Page, RequestModel, Timestamp

CardProviderLiteral = Literal["manual", "stripe_issuing", "mercury", "fake"]
CardAccountStatusLiteral = Literal["setup", "active", "attention", "disabled"]
CardModeLiteral = Literal["live", "test"]
CardKindLiteral = Literal["single_use", "subscription"]
CardStatusLiteral = Literal["active", "frozen", "closed", "expired"]
LimitIntervalLiteral = Literal["per_authorization", "monthly", "yearly", "all_time"]
PurchaseStatusLiteral = Literal[
    "requested",
    "pending_approval",
    "approved",
    "issuing",
    "issue_failed",
    "card_issued",
    "awaiting_checkout",
    "authorized",
    "settled",
    "human_task",
    "rejected",
    "expired",
    "cancelled",
]
PurchaseDecisionLiteral = Literal["auto", "cofounder", "human", "report_only"]
CheckoutPathLiteral = Literal["programmatic", "human_checkout", "human_buys"]
RecurrenceLiteral = Literal["one_time", "monthly", "yearly"]
AgentRecurrenceLiteral = Literal["none", "monthly", "yearly"]
CardTxnKindLiteral = Literal["authorization", "capture", "refund", "decline", "reversal"]
CardTxnStatusLiteral = Literal["pending", "settled", "reversed", "declined"]
CardTxnMatchLiteral = Literal["matched", "unmatched", "mismatch"]
CardTxnSourceLiteral = Literal["provider", "human"]
SubscriptionStatusLiteral = Literal[
    "active", "review_due", "cancel_requested", "cancelled", "lapsed"
]
CostKindLiteral = Literal["verified", "estimated", "unknown"]
RevealKindLiteral = Literal["provider_dashboard"]  # M16 adds "issuing_elements"
CompanyKindLiteral = Literal["india_company", "us_company", "not_incorporated", "other"]

_PAN_RUN = re.compile(r"(?<![\d])(?:\d[ -]?){13,19}(?![\d])")


def looks_like_card_number(text: str) -> bool:
    """A 13-19 digit run (spaces / dashes allowed) that passes the Luhn check. Used to reject a
    full card number typed into a label; the DLP scanner (``secrets_scan``) redacts the same shape
    everywhere else."""
    for match in _PAN_RUN.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
            continue
        total = 0
        for i, ch in enumerate(reversed(digits)):
            n = int(ch)
            if i % 2 == 1:
                n = n * 2 - 9 if n * 2 > 9 else n * 2
            total += n
        if total % 10 == 0:
            return True
    return False


def _reject_card_number(value: str | None) -> str | None:
    if value and looks_like_card_number(value):
        raise ValueError("a full card number must never be entered; give only the last 4 digits")
    return value


# ---------------------------------------------------------------- providers (catalog)


class ProviderCapabilitiesOut(ApiModel):
    issue_cards: bool
    single_use: bool
    merchant_mcc_lock: bool
    per_auth_limit: bool
    interval_limits: list[str]
    list_transactions: bool
    balance: bool
    webhooks: bool
    programmatic_checkout: bool
    test_mode: bool


class ProviderGuideStep(ApiModel):
    title: str
    body: str
    owner_only: bool = True  # a step only the human can do (KYC, funding, OTP ...)


class CardProviderInfo(ApiModel):
    id: CardProviderLiteral
    label: str
    capabilities: ProviderCapabilitiesOut
    eligibility: str  # honest, dated text per country
    recommended_for: list[CompanyKindLiteral]
    steps: list[ProviderGuideStep]  # what the human must do
    verification: str  # what was and was not exercised against the real system
    needs_connected_account: bool
    available: bool = True  # False for a provider that is registered but not usable here


class CardProviderList(ApiModel):
    items: list[CardProviderInfo]
    guide_updated_on: str  # the date the guide text was last reviewed


class WalletRecommendation(ApiModel):
    company_kind: CompanyKindLiteral
    provider: CardProviderLiteral
    reason: str
    steps: list[ProviderGuideStep]


# ---------------------------------------------------------------- card accounts


class CardAccountCreate(RequestModel):
    """Connect a provider (``connected_account_id`` of the M8 vault) or register the owner's own
    card (``manual``: a label, a brand and the LAST FOUR digits only)."""

    provider: CardProviderLiteral
    label: str = Field(min_length=1, max_length=120)
    mode: CardModeLiteral = "live"
    connected_account_id: str | None = Field(default=None, max_length=36)
    external_ref: str | None = Field(default=None, max_length=120)
    manual_brand: str | None = Field(default=None, min_length=1, max_length=24)
    manual_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    make_default: bool = True

    @field_validator("label", "manual_brand", "external_ref")
    @classmethod
    def _no_card_number(cls, value: str | None) -> str | None:
        return _reject_card_number(value)


class CardAccountOut(ApiModel):
    id: str
    company_id: str
    provider: CardProviderLiteral
    label: str
    status: CardAccountStatusLiteral
    mode: CardModeLiteral
    connected_account_id: str | None
    external_ref: str | None
    manual_brand: str | None
    manual_last4: str | None
    is_default: bool
    last_balance_micros: int | None
    balance_checked_at: Timestamp | None
    last_synced_at: Timestamp | None
    last_error: str | None
    created_at: Timestamp
    updated_at: Timestamp


class CardAccountCheck(ApiModel):
    """Result of a live check of an account: never contains a key or a card number."""

    account_id: str
    ok: bool
    mode: CardModeLiteral | None = None
    issuing_enabled: bool | None = None
    balance_readable: bool | None = None
    balance_micros: int | None = None
    message: str | None = None


class FundingInstructions(ApiModel):
    """Fetched live from the provider for the owner, never stored."""

    account_id: str
    provider: CardProviderLiteral
    instructions: dict[str, str]
    fetched_at: Timestamp


# ---------------------------------------------------------------- cards


class MerchantLockOut(BaseModel):
    name: str | None = None
    domain: str | None = None
    mcc_allow: list[str] = []
    country: str | None = None


class AgentCardOut(ApiModel):
    """A card as the control plane knows it: provider id, last4, brand, expiry and limits. There
    is deliberately no ``number`` / ``cvc`` field."""

    id: str
    company_id: str
    card_account_id: str
    provider_card_id: str
    kind: CardKindLiteral
    status: CardStatusLiteral
    purchase_request_id: str | None
    subscription_id: str | None
    agent_id: str | None
    last4: str
    brand: str | None
    exp_month: int | None
    exp_year: int | None
    limit_micros: int
    limit_interval: LimitIntervalLiteral
    merchant_lock: MerchantLockOut
    expires_at: Timestamp | None
    frozen_reason: str | None
    closed_at: Timestamp | None
    created_at: Timestamp


class CardActionRequest(RequestModel):
    reason: str | None = Field(default=None, max_length=200)


class CardReveal(ApiModel):
    """How a HUMAN can see the number: a link to the provider's own surface. M16 adds
    ``issuing_elements``. The number never passes through this server."""

    kind: RevealKindLiteral
    url: str
    provider: CardProviderLiteral
    note: str


# ---------------------------------------------------------------- purchase requests


def _check_https_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("the url must be an https:// address")
    if parts.username or parts.password:
        raise ValueError("the url must not contain credentials")
    return value


class PurchaseRequestCreate(RequestModel):
    """The body of ``POST /api/agent/v1/purchases`` (the ``company_request_purchase`` tool)."""

    item: str = Field(min_length=1, max_length=200)
    vendor: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=9, max_length=2000)
    amount_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    original_amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)
    original_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    recurring: AgentRecurrenceLiteral
    quantity: int = Field(default=1, ge=1, le=10_000)
    justification: str = Field(min_length=40, max_length=2000)
    alternatives_considered: str | None = Field(default=None, max_length=2000)
    task_id: str = Field(min_length=1, max_length=36)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        return _check_https_url(value)

    @field_validator("item", "vendor", "justification", "alternatives_considered")
    @classmethod
    def _no_card_number(cls, value: str | None) -> str | None:
        return _reject_card_number(value)


class PurchaseAck(ApiModel):
    """What the agent gets back: no card data, ever."""

    purchase_request_id: str
    status: PurchaseStatusLiteral
    decision: PurchaseDecisionLiteral | None
    next_step: str


class CheckoutPacket(ApiModel):
    """What the human needs to complete the checkout. ``vendor_domain`` is shown prominently; the
    url, item and vendor are untrusted agent text."""

    vendor: str
    vendor_domain: str
    url: str
    item: str
    quantity: int
    amount_micros: int
    card_limit_micros: int | None = None
    recurrence: RecurrenceLiteral
    company_email: str | None = None  # the M8 identity to sign up with, if any
    bring_back: str | None = None  # e.g. where to store the API key that results
    card_last4: str | None = None
    card_brand: str | None = None
    card_exp_month: int | None = None
    card_exp_year: int | None = None
    steps: list[str] = []


class CardTransactionOut(ApiModel):
    id: str
    company_id: str
    card_account_id: str
    card_id: str | None
    purchase_request_id: str | None
    subscription_id: str | None
    provider_txn_id: str
    kind: CardTxnKindLiteral
    status: CardTxnStatusLiteral
    amount_micros: int
    cost_kind: CostKindLiteral = "verified"
    merchant_amount_minor: int | None
    merchant_currency: str | None
    merchant_name: str | None
    merchant_mcc: str | None
    merchant_country: str | None
    match_status: CardTxnMatchLiteral
    source: CardTxnSourceLiteral  # "human" is shown as "confirmed by you"
    occurred_at: Timestamp
    settled_at: Timestamp | None
    created_at: Timestamp


class PurchaseApprovalSummary(ApiModel):
    id: str
    status: str
    approver: str
    decided_by_type: str | None = None
    decision_note: str | None = None
    decided_at: Timestamp | None = None


class PurchaseRequestOut(ApiModel):
    id: str
    company_id: str
    agent_id: str
    agent_name: str | None = None
    run_id: str | None
    task_id: str
    project_id: str | None
    item: str
    vendor: str
    vendor_domain: str
    url: str
    quantity: int
    amount_micros: int
    original_amount_minor: int | None
    original_currency: str | None
    recurrence: RecurrenceLiteral
    justification: str
    alternatives: str | None
    status: PurchaseStatusLiteral
    decision: PurchaseDecisionLiteral | None
    checkout_path: CheckoutPathLiteral | None
    approval_id: str | None
    card_id: str | None
    subscription_id: str | None
    checkout_task_id: str | None
    held_micros: int
    settled_micros: int | None
    cost_kind: CostKindLiteral | None
    checkout_packet: CheckoutPacket | None = None
    expires_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class PurchaseRequestDetail(PurchaseRequestOut):
    approval: PurchaseApprovalSummary | None = None
    card: AgentCardOut | None = None
    transactions: list[CardTransactionOut] = []


class PurchaseDecisionRequest(RequestModel):
    """Approve / reject through the Wallet (a thin wrapper over ``approvals.decide``)."""

    note: str | None = Field(default=None, max_length=2000)


class PurchaseMarkPaid(RequestModel):
    """Manual mode (or a human checkout the provider could not see): the amount the human
    confirms is recorded ``verified``, source ``human`` ("confirmed by you")."""

    merchant_amount_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    billed_amount_inr: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)
    receipt_artifact_id: str | None = Field(default=None, max_length=36)
    note: str | None = Field(default=None, max_length=500)


class PurchaseCancelRequest(RequestModel):
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- subscriptions


class SubscriptionOut(ApiModel):
    id: str
    company_id: str
    purchase_request_id: str
    agent_id: str | None
    agent_name: str | None = None
    vendor: str
    vendor_domain: str
    item: str
    amount_micros: int
    recurrence: RecurrenceLiteral
    card_id: str | None
    status: SubscriptionStatusLiteral
    started_on: date
    next_renewal_on: date | None
    last_charged_on: date | None
    last_charge_micros: int | None
    review_task_id: str | None
    cancel_task_id: str | None
    reviewed_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class SubscriptionReviewIn(RequestModel):
    """Body of ``POST /api/agent/v1/subscriptions/{id}/review`` (the review tool)."""

    decision: Literal["keep", "cancel"]
    reason: str = Field(min_length=1, max_length=1000)


class SubscriptionHumanAction(RequestModel):
    """``POST /subscriptions/{id}/cancelled`` and ``.../keep`` (owner/admin)."""

    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- wallet overview


class WalletBalance(ApiModel):
    available_micros: int | None
    pending_micros: int | None = None
    as_of: Timestamp | None
    source: Literal["provider", "manual"]  # "manual" has no balance to report


class WalletMonth(ApiModel):
    period: str  # YYYY-MM
    settled_verified_micros: int
    held_micros: int  # committed, NOT charged
    budget_micros: int | None


class WalletCheckoutItem(ApiModel):
    purchase_request_id: str
    item: str
    vendor: str
    vendor_domain: str
    amount_micros: int
    checkout_task_id: str | None
    expires_at: Timestamp | None


class WalletRenewal(ApiModel):
    subscription_id: str
    vendor: str
    amount_micros: int
    next_renewal_on: date


class PurchasePolicyRecommendation(ApiModel):
    purchase_cofounder_limit_micros: int
    purchase_cofounder_monthly_micros: int
    purchase_auto_approve_micros: int
    purchase_recurring_requires_human: bool
    purchase_new_vendor_requires_human: bool


class WalletSetupHint(ApiModel):
    """Shown when purchasing is not set up: the recommended values once the owner opts in."""

    providers: list[CardProviderLiteral]
    recommended_policy: PurchasePolicyRecommendation | None = None


class WalletOverview(ApiModel):
    available: bool = True
    milestone: str | None = None  # "M15" when available is False
    enabled: bool  # purchasing_enabled and an active account
    accounts: list[CardAccountOut]
    balance: WalletBalance | None
    month: WalletMonth
    awaiting_checkout: list[WalletCheckoutItem]
    pending_approvals: int
    renewals_due: list[WalletRenewal]
    low_balance: bool = False
    attention: str | None = None  # provider check failing, or frozen after an emergency stop
    setup: WalletSetupHint | None = None


class WalletSyncResult(ApiModel):
    accounts_synced: int
    transactions_seen: int
    transactions_new: int
    errors: list[str] = []


# ---------------------------------------------------------------- purchasing policy (human-only)


class PurchasePolicy(ApiModel):
    """The purchasing part of the company policy. WP1 folds these fields into ``Policy``."""

    purchasing_enabled: bool = False
    purchase_auto_approve_micros: int = 0
    purchase_agent_auto_monthly_micros: int = 0
    purchase_cofounder_limit_micros: int = 0
    purchase_cofounder_monthly_micros: int = 0
    purchase_monthly_budget_micros: int | None = None
    purchase_recurring_requires_human: bool = True
    purchase_new_vendor_requires_human: bool = True
    purchase_tolerance_bps: int = 1000
    purchase_tolerance_min_micros: int = 2_000_000
    purchase_card_ttl_hours: int = 72
    purchase_checkout_member_id: str | None = None
    subscription_renewal_alert_days: int = 7


class PurchasePolicyUpdate(RequestModel):
    """Ceilings are validated here as well as in the service (the same constants)."""

    purchasing_enabled: bool | None = None
    purchase_auto_approve_micros: int | None = Field(
        default=None, ge=0, le=MAX_PURCHASE_AUTO_APPROVE_MICROS
    )
    purchase_agent_auto_monthly_micros: int | None = Field(
        default=None, ge=0, le=MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS
    )
    purchase_cofounder_limit_micros: int | None = Field(
        default=None, ge=0, le=MAX_PURCHASE_COFOUNDER_LIMIT_MICROS
    )
    purchase_cofounder_monthly_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    purchase_monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    purchase_recurring_requires_human: bool | None = None
    purchase_new_vendor_requires_human: bool | None = None
    purchase_tolerance_bps: int | None = Field(default=None, ge=0, le=5000)
    purchase_tolerance_min_micros: int | None = Field(default=None, ge=0, le=100_000_000)
    purchase_card_ttl_hours: int | None = Field(default=None, ge=1, le=720)
    purchase_checkout_member_id: str | None = Field(default=None, max_length=36)
    subscription_renewal_alert_days: int | None = Field(default=None, ge=1, le=60)


class AgentPurchaseLimitUpdate(RequestModel):
    """``purchase_monthly_limit_micros`` of one agent: human-only, null = inherit the company."""

    purchase_monthly_limit_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)


class AgentPurchaseLimitOut(ApiModel):
    agent_id: str
    purchase_monthly_limit_micros: int | None


# ---------------------------------------------------------------- list envelopes

CardAccountPage = Page[CardAccountOut]
AgentCardPage = Page[AgentCardOut]
PurchaseRequestPage = Page[PurchaseRequestOut]
SubscriptionPage = Page[SubscriptionOut]
CardTransactionPage = Page[CardTransactionOut]
