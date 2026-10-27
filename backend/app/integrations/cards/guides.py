"""Funding guides and eligibility text (docs/specs/m15, section 4.10): exactly what a person must do
to give the company a way to pay, per provider and per kind of company.

This is plain text with dates, not a promise. Eligibility changes; the text says what was true when
the guide was last reviewed (``GUIDE_UPDATED_ON``) and what was not verified. Indian tax and FEMA
points are not legal advice: every guide that touches them says to confirm with a CA or the bank.
The schema objects (``CardProviderInfo`` ...) are built lazily so this module stays importable from
the integrations layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.integrations.cards.manual import ManualCardProvider
from app.integrations.cards.mercury import MercuryProvider
from app.integrations.cards.stripe_issuing import StripeIssuingProvider

if TYPE_CHECKING:
    from app.schemas.purchasing import CardProviderInfo, WalletRecommendation

GUIDE_UPDATED_ON = "2026-10-27"
COMPANY_KINDS = ("india_company", "us_company", "not_incorporated", "other")


@dataclass(frozen=True)
class GuideStep:
    title: str
    body: str
    owner_only: bool = True  # only a person can do it (KYC, funding, an OTP ...)


@dataclass(frozen=True)
class ProviderGuide:
    provider: str
    label: str
    eligibility: str
    verification: str
    recommended_for: tuple[str, ...]
    needs_connected_account: bool
    steps: tuple[GuideStep, ...] = field(default_factory=tuple)


STRIPE_STEPS = (
    GuideStep(
        "Have a US company and a Stripe account in its name",
        "A US company with an EIN. Stripe Atlas works, with a condition: a representative with a "
        "US address and activity and cardholders primarily in the US. If you live in India without "
        "a US address Stripe may decline: apply anyway, or choose Mercury.",
    ),
    GuideStep(
        "Apply for Stripe Issuing",
        "Dashboard > Issuing > apply for the Standard program (no sales call). Default limits are "
        "25 cards a week, $5,000 a week and $500 per card per day; ask Stripe to raise them.",
    ),
    GuideStep(
        "Fund the Issuing balance",
        "Dashboard > Balances > Issuing balance > Add funds, by ACH or wire from your US bank "
        "account to the routing and account number Stripe shows (about 1 business day), or "
        "transfer from your Stripe payments balance.",
    ),
    GuideStep(
        "Create a restricted API key",
        "Developers > API keys > Create restricted key named 'Foundry agent cards'. Write: Issuing "
        "Cards and Issuing Cardholders. Read: Issuing Authorizations, Issuing Transactions and "
        "Balance. Nothing else.",
    ),
    GuideStep(
        "Paste the key into Foundry",
        "Use the hidden field. Foundry creates one cardholder '<Company> agents' with your "
        "business billing address and your phone number (3DS codes come to you).",
    ),
    GuideStep(
        "Optional: add a webhook",
        "Only with a stable public URL. Webhooks just make Foundry look sooner; it also checks "
        "every 15 minutes.",
        owner_only=False,
    ),
    GuideStep(
        "Test with a sandbox key first",
        "A sandbox key is instant and moves no money.",
        owner_only=False,
    ),
    GuideStep(
        "From India: talk to your CA first",
        "Wiring money from India to your own US company is an overseas investment (ODI) under "
        "FEMA. Do it through your bank's ODI process.",
    ),
)

MERCURY_STEPS = (
    GuideStep(
        "Open a Mercury business account for your US company",
        "Passport and EIN. Mercury serves many non-US founders; check that your country is "
        "eligible.",
    ),
    GuideStep(
        "Fund the account",
        "International wire (SWIFT) from India, or ACH/wire from another US account. From India "
        "this is an overseas investment under FEMA: use your bank's ODI process and ask your CA.",
    ),
    GuideStep(
        "Create an API token",
        "Settings > API tokens > a read/write token. Mercury may ask for an IP allowlist for write "
        "tokens: add your server's public IP. Paste the token into Foundry.",
    ),
    GuideStep(
        "What Foundry does with it",
        "It creates a virtual card per approved purchase with a limit equal to the amount, and "
        "closes it after the charge. Mercury has no single-use or merchant lock: the limit and "
        "Foundry's closing do the enforcement.",
        owner_only=False,
    ),
    GuideStep(
        "To see a card number",
        "Open the card in the Mercury app. Foundry never fetches card numbers.",
    ),
)

MANUAL_COMPANY_STEPS = (
    GuideStep(
        "Get a bank corporate credit card",
        "A bank corporate credit card or a RazorpayX corporate card. Prepaid cards (EnKash, "
        "Volopay) may lose cross-border use under the RBI's 2026 PPI draft.",
    ),
    GuideStep(
        "Enable online and international use",
        "In the bank app, switch on online and international transactions (off by default on "
        "Indian cards) and set the card's own limit to your monthly purchase budget.",
    ),
    GuideStep(
        "Keep the OTP phone with you",
        "Indian cards need an OTP for most online payments. A purchase waits for you at checkout.",
    ),
    GuideStep(
        "Subscriptions: accept the e-mandate once",
        "At the first payment (one OTP). Later renewals up to Rs 15,000 run automatically, with a "
        "bank notice 24 hours before.",
    ),
    GuideStep(
        "Enter a label and the last 4 digits in Foundry",
        "Only the last 4 digits. Never the full number: Foundry refuses it.",
    ),
    GuideStep(
        "Tax",
        "Foreign SaaS is an import of services (not LRS) for a company. Ask your CA about GST "
        "reverse charge and withholding (section 195).",
    ),
)

MANUAL_PERSONAL_STEPS = (
    GuideStep(
        "Use a personal international credit card",
        "Enable online and international transactions in the bank app and set a card limit.",
    ),
    GuideStep(
        "Keep the OTP phone with you",
        "Indian cards need an OTP for most online payments.",
    ),
    GuideStep(
        "Enter a label and the last 4 digits in Foundry",
        "Only the last 4 digits. Never the full number: Foundry refuses it.",
    ),
    GuideStep(
        "Tax and limits",
        "Payments abroad fall under LRS (USD 250k a year). Credit-card spend abroad was excluded "
        "from LRS/TCS in 2023; confirm the current rules with your CA.",
    ),
)

GUIDES: dict[str, ProviderGuide] = {
    "manual": ProviderGuide(
        provider="manual",
        label="My own card (manual)",
        eligibility="Works anywhere. Foundry cannot issue, limit or read your card: every purchase "
        "waits for you to pay with your own card and confirm the amount.",
        verification="Fully testable offline: tracked by Foundry's own records, no provider call.",
        recommended_for=("india_company", "not_incorporated", "other"),
        needs_connected_account=False,
        steps=MANUAL_COMPANY_STEPS,
    ),
    "stripe_issuing": ProviderGuide(
        provider="stripe_issuing",
        label="Stripe Issuing",
        eligibility="US businesses (EIN). Not available to India-incorporated companies. Atlas "
        "companies need a representative with a US address (reviewed 2026-10-27; Stripe decides).",
        verification="Implemented against Stripe's documented API and unit-tested with recorded "
        "fixtures. Not exercised against the real system unless you ran the sandbox check.",
        recommended_for=("us_company",),
        needs_connected_account=True,
        steps=STRIPE_STEPS,
    ),
    "mercury": ProviderGuide(
        provider="mercury",
        label="Mercury",
        eligibility="US business entities; non-resident founders are commonly accepted (India "
        "eligibility not verified). Mercury has no single-use cards or merchant lock.",
        verification="Implemented from Mercury's public documentation and unit-tested with "
        "fixtures; several field names are assumptions. Not exercised against the real system.",
        recommended_for=("us_company",),
        needs_connected_account=True,
        steps=MERCURY_STEPS,
    ),
}


def guide_for(provider: str, company_kind: str | None = None) -> ProviderGuide:
    """The guide of a provider. ``manual`` has a different checklist for a person who is not
    incorporated."""
    guide = GUIDES[provider]
    if provider == "manual" and company_kind == "not_incorporated":
        return replace(guide, steps=MANUAL_PERSONAL_STEPS)
    return guide


def capabilities_of(provider: str):
    return {
        "manual": ManualCardProvider.capabilities,
        "stripe_issuing": StripeIssuingProvider.capabilities,
        "mercury": MercuryProvider.capabilities,
    }[provider]


def _steps(guide: ProviderGuide):
    from app.schemas.purchasing import ProviderGuideStep

    return [
        ProviderGuideStep(title=s.title, body=s.body, owner_only=s.owner_only) for s in guide.steps
    ]


def provider_infos() -> list[CardProviderInfo]:
    """The catalog behind ``GET /wallet/providers`` (``fake`` is never listed)."""
    from app.schemas.purchasing import CardProviderInfo, ProviderCapabilitiesOut

    out = []
    for provider, guide in GUIDES.items():
        caps = capabilities_of(provider)
        out.append(
            CardProviderInfo(
                id=provider,  # type: ignore[arg-type]
                label=guide.label,
                capabilities=ProviderCapabilitiesOut(
                    issue_cards=caps.issue_cards,
                    single_use=caps.single_use,
                    merchant_mcc_lock=caps.merchant_mcc_lock,
                    per_auth_limit=caps.per_auth_limit,
                    interval_limits=list(caps.interval_limits),
                    list_transactions=caps.list_transactions,
                    balance=caps.balance,
                    webhooks=caps.webhooks,
                    programmatic_checkout=caps.programmatic_checkout,
                    test_mode=caps.test_mode,
                ),
                eligibility=guide.eligibility,
                recommended_for=list(guide.recommended_for),  # type: ignore[arg-type]
                steps=_steps(guide),
                verification=guide.verification,
                needs_connected_account=guide.needs_connected_account,
            )
        )
    return out


def recommend(
    company_kind: str, *, has_mercury: bool = False, has_stripe: bool = False
) -> WalletRecommendation:
    """The honest recommendation of the setup wizard (section 4.9)."""
    from app.schemas.purchasing import WalletRecommendation

    if company_kind == "us_company":
        if has_mercury or not has_stripe:
            provider = "mercury"
            reason = (
                "You can connect Mercury if you bank there, or if you lack the US address Stripe "
                "Issuing may ask for. Stripe Issuing gives the best controls (single-use cards, "
                "merchant categories) if you qualify."
            )
        else:
            provider = "stripe_issuing"
            reason = "Stripe Issuing has the best controls: single-use cards and category locks."
    elif company_kind == "not_incorporated":
        provider = "manual"
        reason = (
            "No provider will issue cards to a person without a US company. Use your own "
            "international credit card; Foundry prepares the purchase and you pay."
        )
    else:
        provider = "manual"
        reason = (
            "No provider offers programmable cards to an India-incorporated company today. Use a "
            "bank corporate credit card; Foundry prepares each purchase and you pay (an OTP "
            "keeps a person in the loop on Indian cards anyway)."
        )
    guide = guide_for(provider, company_kind)
    return WalletRecommendation(
        company_kind=company_kind,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        reason=reason,
        steps=_steps(guide),
    )
