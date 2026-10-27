# M15 — Agent cards and purchasing — spec

Owner request (2026-10-27): "When an agent wants to buy something, e.g. an online tool, look for an agent card and
give an option to set up agent cards. The setup should run autonomously; just tell the user how to fund the account
and what the user must do. Add a new column on the dashboard for it, with the functionality."

Status: **step 0 (contract) done, 2026-10-27**: migration `0014`, models, enums, schemas, the card provider
interface and a stateful fake, the guardrail registry entries and two stub-signature service modules. Work
packages WP1 to WP4 (section 4.13) are not started. Nothing in this milestone has been exercised against a real
provider (rule 8); section 4.14 says what may be claimed when it is done.

Research and design were done by the planner on 2026-10-27; every claim about a provider has a citation (section 8),
and "unverified" means exactly that. The facts are current as of that date.

---

## 0. TL;DR

1. **No provider today gives an India-resident individual or an India-incorporated company a programmable "agent card".**
   - Every real agent-card product (Stripe Issuing for agents, Mercury Agent Cards, Ramp Agent Cards, Lithic, Privacy.com,
     Brex) needs a **US entity** (Privacy.com: a US person).
   - Indian options (EnKash, Volopay, RazorpayX corporate cards) are corporate or prepaid cards with little or no API.
   - RBI authentication rules (an extra factor, usually an OTP, on domestic card-not-present payments, and on cross-border
     ones when the merchant asks) keep a human in the loop anyway.
2. **Our agents have no browser, and almost every SaaS vendor sells through a browser checkout.**
   - The protocol rails (Stripe Shared Payment Tokens via MPP/UCP, ACP, AP2, x402, Visa/Mastercard agent tokens) are
     real, but they are either preview/pilot or accepted only by a narrow set of sellers (mostly API and data sellers).
   - OpenAI scaled back ACP Instant Checkout in March 2026.
   - So the **realistic primary path is a human checkout with a single-use, merchant-scoped card issued with an exact
     limit**: the agent and the control plane do everything except click "Pay".
3. **Design:** a provider adapter interface with these M15 adapters:
   - `fake` (tests);
   - `manual` (a card the owner controls; we track it only; works in India);
   - `stripe_issuing` (US entity; best controls, instant sandbox);
   - `mercury` (US entity; most common bank for Indian founders' US LLCs; Cards API can create and limit cards).
4. **What the system does by itself:**
   - purchase requests via a new agent tool `company_request_purchase`;
   - a three-band policy (auto / AI Co-founder / human) enforced in code;
   - per-purchase card issuance and a "checkout packet" handed to the human as a task;
   - polling reconciliation into the ledger (`cost_kind=verified`), subscription tracking and renewal reviews;
   - low-balance funding alerts.
5. **Card numbers (PAN/CVV):** they never touch our server. The adapter interface has no method that returns them. The
   human sees them only on the provider's own surface (dashboard deep link by default; Stripe Issuing Elements as an
   opt-in later).
6. **Dashboard:** "dashboard" means the **canvas widget grid** (`web/src/features/canvas/CanvasPage.tsx`), not the Command
   Center (a chat-style feed with no columns). Add a **Wallet widget** next to Spend, plus a `?panel=wallet` panel with
   the setup wizard.
7. **Work:** migration `0014_agent_cards.py`; step 0 (the contract), then four parallel work packages (backend core,
   adapters + reconciliation, runtime tool, web).

---

## 1. What "a new column on the dashboard" means

I read `web/src/features/canvas/CanvasPage.tsx`, `features/commandcenter/*` and `features/finance/*`.

- **The dashboard is the canvas.**
  - It is the sidebar-less page from decision D11: eleven `Widget` tiles in a 12-column grid (`xl:grid-cols-12`).
  - Row 1: Org / Reflections / Command Center.
  - Row 2: Live activity / Goals.
  - Then two rows of three `xl:col-span-4` tiles: Work, Approvals, Spend, Artifacts, Routines, Skills.
  - A "column" in the owner's words is most plausibly **one more tile in that grid** (a column-width card).
- **The Command Center is not a board.** It is a command input with an exchange feed (`CommandInput`, `ExchangeCard`).
  It has no columns to add to.
- **Finance** is a panel (`?panel=finance`) reached from the Spend widget. Purchases belong next to it, but the owner
  asked for a dashboard-level surface.

**Recommendation:**
- A new **`WalletWidget`** ("Agent cards"), placed directly next to Spend.
- A new panel **`?panel=wallet`**, with tabs Overview · Purchases · Cards · Subscriptions · Transactions · Setup.
- Proposed layout:
  - Row 3: Approvals · Spend · Wallet.
  - Row 4: Work · Artifacts · Routines.
  - Row 5: Skills at `xl:col-span-12` in compact form.
  - Alternatively, accept one empty slot.
- Update the comment in `CanvasPage.tsx` ("eleven widgets") to match.
- Also add a **slash command** `/wallet` in the Command Center (instant output: balance, awaiting checkout, renewals),
  because D29 says every input gets an output.

---

## 2. Research: who can give software or agents a card or a way to pay (late 2026)

### 2.1 Comparison table

The "Agent sees PAN?" column describes what the provider offers. Our design never fetches the PAN either way.

| Provider | Who can sign up · India | KYC | Funding | Limits / merchant lock / single-use | Card API | Agent sees PAN? (PCI) | Fees | Headless purchase? | Status (marketing vs real) | Fit for us |
|---|---|---|---|---|---|---|---|---|---|---|
| **Stripe Issuing** (+ Issuing for agents) | US/UK/EEA/CA businesses. **No India.** Atlas users need a representative with a US address and US-based activity and cardholders [S3] | Stripe KYB + Issuing eligibility review | Prefunded Issuing balance / financial account: ACH or wire from a bank, or from the Stripe payments balance [S2] | Per-authorization and interval limits, allowed/blocked MCC categories, **single-use cards**, real-time auth webhook (2 s) [S1] | `POST /v1/issuing/cards` with `spending_controls`; `GET /v1/issuing/transactions` [S1][S2] | Only if you call `expand[]=number,cvc` [S2]. Own-use cards: no PCI program needed (per docs, via agent A); Issuing Elements iframe for display | $0.10 per virtual card; others per pricing page (unverified). Standard program: 25 cards/week, $5k/week, $500/card/day [S2] | Browser checkout needs the PAN. **Issuing → Shared Payment Token** works headless, but only for MPP/UCP sellers and is preview [S1][S4] | Issuing GA (US); "for agents" is documentation + preview SPT path | **First real adapter** (US entity); instant sandbox [S2] |
| **Mercury** (Agent Cards) | US business entities; non-resident founders commonly accepted (India eligibility unverified) | KYB, passport | US deposit account (ACH, domestic or international wire) | Daily/weekly/monthly limit set by a human; MCC lock not documented; no single-use (emulate by closing after use) | Cards API: create virtual card, update limits, freeze, cancel; **PAN never returned** [S5]. Agent PAN only via Vault API [S6] | Cards API: no. Vault API: yes (we will not call it) | Card fees not found; 3% on non-USD card spend (agent A, unverified) | Same as any card: browser or human checkout | Agent Cards launched 11–12 Aug 2026 [S7][S8] | **Second adapter** (likely bank of an Indian founder's US LLC) |
| **Ramp** (Agent Cards) | US entity, EIN, ~$25k in a US bank, US address; passport OK for non-US founders [S9] | KYB + credit underwriting | Charge card repaid from a linked US bank | Vendor lock, per-transaction cap | Developer API / MCP / CLI; one-time tokenized credentials [S10] | One-time token (fields unverified) | Core free | Token filled at browser checkout; also x402 / MPP paths [S11] | **Early access** (Apr 2026) | Later, if the owner qualifies |
| **Brex** | US-organized companies only [S12] | KYB | Charge card | Card spend controls | `POST /v2/cards`, `GET /v2/cards/{id}/pan` [S13] | Via the `/pan` endpoint | FX markup up to 3% [S14] | Browser | Card API GA; MCP beta; no agent-card product | No |
| **Lithic** | Card programs (sales-led), US-focused | You are the program manager (KYC/KYB) | Sponsor-bank program funding | `spend_limit` by duration; `SINGLE_USE` and `MERCHANT_LOCKED` types [S15] | `POST /v1/cards` | Raw PAN only for PCI-verified clients; embed iframe otherwise [S16] | Contact sales | Browser / PCI backend | GA; "agentic" page is marketing; MCP server Apr 2026 [S17] | No (program-scale, not one small company) |
| **Privacy.com** | **US citizens/residents, SSN, US bank** [S18]; business plan needs a US business | Personal KYC | Debit from a linked US bank | Single-use, merchant-locked, category-locked, limits | `POST /v1/cards` (paid plan) [S19] | **Yes**, the create response includes PAN/CVV [S20] | $0–25/mo; 3% FX on lower tiers [S19] | Browser | GA | No (US persons only; PAN exposure) |
| **Airwallex** | Issuing regions exclude India [S21]; Indian firms reportedly via the Singapore entity (unverified) | KYB | Prefunded multi-currency wallet | MCC allowlist, transaction limits, single/multi-use | Issuing `cards/create` | Sensitive-details API needs PCI; secure iframe does not [S22] | FX 0.5–1% [S23] | Browser | Issuing GA | Possible later for a Singapore/US entity |
| **Wise Business** | Business card not offered in India [S24] | KYB | Balance | Per-transaction/daily/monthly limits | Card ordering is a partner-platform API [S25] | n/a | Low FX | No practical API | Not built for this | No |
| **Revolut Business** | India-incorporated companies not eligible; US company with an India-resident owner reportedly OK (unverified) [S26] | KYB, EIN | Balance | One periodic + one per-transaction limit, MCC allowlist | `POST /api/1.0/cards`, sensitive details behind special scope + IP allowlist [S27] | Only via that special scope | $10–180/mo plans | Browser | API GA; no agent-card product | Possible future adapter |
| **Crossmint** (Agent cards / Agentic Payments) | Developer platform; card rail only for **eligible US-issued Visa cards** [S28] | Platform + cardholder | User's own card in a vault; stablecoin wallets | Spend limit, expiry, optional merchant lock; one-time card numbers on Visa/MC rails [S29] | Yes (SDK/API) | No on Visa/MC rails | Not published (unverified) | Partly: Shop Pay / UCP, **else falls back to browser automation** [S30] | Live (Jun / Oct 2026) | No (US cards; browser fallback) |
| **Skyfire** (KYAPay) | Individuals and businesses; India not excluded [S31] | ID via Persona [S32] | Card, USDC on Base, ACH/wire [S33] | Payment tokens from a prefunded wallet; **no cards** | Yes | No cards ("direct delivery of card details to merchants isn't currently supported") | No fee on credits for now | **Only at sellers that integrate Skyfire** | Live; narrow seller adoption | Later, API/data purchases only |
| **Payman** | Now targets US banks and credit unions [S34] | — | ACH / USDC | Caps, payee allowlist | Older developer SDK | No cards | Unverified | Pays payees, does not buy at merchants | Pivoted to selling to banks [S35] | No |
| **Visa Intelligent Commerce / Trusted Agent Protocol** | Network program via enablers; Commerce Connect is a **pilot** with named partners (Apr 2026) [S36]; TAP spec is open [S37] | Through the issuer | Cardholder's card, tokenized | Network tokens with controls | Through enablers only | No (tokens) | Not public | Only via an enabled partner. TAP identifies agents; it does not move money | Pilot; India demos only (GFF 2026) [S38] | Not directly; arrives via Stripe/Ramp/Crossmint |
| **Mastercard Agent Pay** | Live for US cardholders; LatAm/Caribbean and Australia first transactions 2026 [S39][S40] | Through the issuer | Agentic tokens on existing cards | Token tied to the agent, with limits | Via approved providers onboarded through Mastercard [S41] | No | Not public | Via enablers | US live; "Agent Pay for Machines" pilot | Not directly |
| **Coinbase x402 + CDP wallets** | Developer platform; CDP country terms unverified | CDP account | USDC on Base/Polygon/Solana… | Per-payment signed authorization; wallet policies | HTTP 402 → sign → retry [S42] | No cards | Facilitator: 1,000 tx/month free then $0.001 [S43]; wallets $0.005/op [S44] | **Yes, fully headless**, only with x402 sellers (mostly AI/data APIs) | Live, but real volume tiny (~$28M) [S45] | Optional later, **US entity only** (see India crypto notes) |
| **Google AP2** | Open protocol, FIDO standardization [S46] | — | Cards first; UPI/stablecoins planned | Signed intent/cart/payment mandates | Spec v0.2 + samples | Implementation-dependent | n/a | No live merchant deployments listed | Spec | Watch |
| **ACP (OpenAI/Stripe) + Stripe SPT** | Merchants onboarded by Stripe; SPTs US/CA/EU only [S47] | — | Card via Stripe | SPT scoped to seller, amount, expiry | Stripe APIs | No | Merchant fee | Only sellers with Stripe programmatic endpoints; can hit `requires_action` (3DS) [S47] | ChatGPT Instant Checkout scaled back Mar 2026 [S48] | M16 "programmatic rail" if sellers appear |
| **Nium** | Enterprise onboarding; in-principle RBI PPI/PA approval Feb 2024 (final unverified) [S49] | KYB | Prefunded | Card API with limits [S50] | Yes | If you fetch it | Contract | Browser | GA (enterprise) | No (enterprise-only) |
| **India: EnKash / Volopay / RazorpayX corporate cards / M2P / Zaggle / Happay** | Indian businesses | Indian KYB | Prepaid (EnKash, Volopay) or bank credit (RazorpayX with YES/RBL) | EnKash API: create prepaid card, usage controls, update limit [S51]. Volopay: per-card limits, single-use, USD cards (API unverified) [S52]. RazorpayX: no public virtual-card API found [S53] | EnKash yes; others not found | Yes if fetched → PCI scope | FX markup on USD cards | **Domestic purchases need an OTP** (human). Foreign merchants usually without OTP, unless the merchant requests it (from 1 Oct 2026) | GA | `manual` mode now; EnKash adapter is a candidate for M16 |

### 2.2 India: the rules that decide the design

1. **Authentication (RBI Authentication Directions, 25 Sep 2025; compliance by 1 Apr 2026)** [S54][S55]:
   - Digital payments need two factors.
   - From **1 Oct 2026** issuers must also authenticate cross-border card-not-present payments when the overseas merchant
     or acquirer asks.
   - A headless agent cannot complete a new domestic online card purchase on an Indian card.
2. **E-mandates for recurring payments ("Digital Payments – E-mandate Framework, 2026", 21 Apr 2026)** [S56][S57]:
   - The first debit needs the extra factor.
   - Later debits up to **₹15,000** each run without it (**₹1 lakh** for insurance, mutual funds and credit-card bills).
   - The bank notifies at least 24 h before each debit, with an opt-out.
   - So SaaS renewals can run unattended after a human registers the first one.
3. **International and online use is off by default on Indian cards** (RBI circular of 15 Jan 2020) [S58]:
   - The cardholder must enable online and international use in the bank app.
   - This is a one-time human step in our funding guide.
4. **Tokenisation / card-on-file** [S59]: merchants may not store PANs. We store none anyway.
5. **Prepaid instruments (PPI Master Direction)** [S60]:
   - Full-KYC PPIs are capped at ₹2 lakh.
   - The **Apr 2026 draft would disallow cross-border use** of PPIs (final status unverified).
   - **Risk:** prepaid cards (EnKash/Volopay) may stop working for foreign SaaS.
   - Prefer a **bank-issued corporate credit card** for an Indian company.
6. **An Indian company paying foreign SaaS** is a current-account import of services, **not LRS**. There is no monetary
   ceiling, and the RBI purpose code is S0807 [S61][S62].
   - Check withholding tax (s.195) and GST reverse charge with the CA.
   - No explicit RBI FAQ on corporate cards was found; confirm with the bank.
7. **An individual (not incorporated)** paying with a personal card is under **LRS: USD 250,000 per financial year** [S61].
   - TCS: 20% above ₹10 lakh/yr for most purposes [S63].
   - International **credit-card** spend is not counted under LRS "until further notice" (2023) [S64]. Debit and forex
     cards are counted.
   - Verify current rules with a CA.
8. **An India resident funding their own US company is Overseas Direct Investment (ODI)** under FEMA:
   - It goes through an AD Category-I bank with ODI reporting.
   - It is not a casual "top-up". This is my inference from the FEMA structure, not verified in this session.
   - The funding guide must say "consult your bank/CA before wiring capital to your US entity".
9. **Crypto / x402 from India:**
   - 30% tax on gains plus 1% TDS (now s.393(1) of the 2025 Act from 1 Apr 2026) [S65].
   - FIU-IND registration is required for offshore platforms; notices went to 15 platforms on 9 Sep 2026 [S66].
   - The RBI leans towards prohibiting stablecoins [S67].
   - Paying foreign sellers in USDC from India is outside authorised forex channels (likely a FEMA problem; inference).
   - **Do not ship x402 as a default. Only offer it for a US entity, later.**

### 2.3 Honest summary

- **Real today:**
  - Stripe Issuing (self-serve Standard program in the US, instant sandbox).
  - Mercury Agent Cards and Cards API.
  - Privacy.com (US persons).
  - Lithic / Brex / Airwallex / Revolut card APIs.
  - x402 (narrow sellers).
  - Indian corporate/prepaid cards (with OTP).
- **Early access / preview / pilot:**
  - Ramp Agent Cards.
  - Stripe Issuing → SPT programmatic checkout.
  - Visa Intelligent Commerce Connect.
  - Mastercard Agent Pay outside the US.
  - Link for agents (US consumer waitlist).
  - The UPI agentic payments pilot (Razorpay/NPCI/OpenAI, Oct 2025) [S68].
- **Protocols with few or no live sellers:** AP2, ACP (scaled back), MPP/UCP.
- **Mostly marketing for our use:** Lithic's "agentic" page, Payman (pivoted), "agent cards" that are ordinary virtual
  cards with a reveal endpoint.

---

## 3. The hard problem: how a purchase actually completes without a browser

Constraints:
- Our agents have no browser (CLIs have no web).
- Automated sign-up and checkout on CAPTCHA, phone or ToS-gated sites is a hard boundary (roadmap "Hard boundaries",
  D16).
- Shopify's default robots.txt also forbids automated checkout [S30].
- We will not put our server in PCI DSS scope by handling PANs.

### Tiered design (the request decides the tier; the agent never chooses a riskier one)

| Tier | When | Who does what | In M15? |
|---|---|---|---|
| **A1 — vendor account balance / connected provider** | The vendor is already a connected account that can spend from its own balance or invoice (e.g. the registrars Porkbun/Namecheap via `domain.purchase`, model-provider credits) | The control plane runs a provider action with a price ceiling (existing M8 pattern: `max_price_micros`, `human_required`). No card involved. | Yes, but only by **recording** these into the purchase ledger (`domain.purchase` already exists) |
| **A2 — token rails (programmatic)** | The seller accepts Stripe SPT via MPP/UCP, x402, or Skyfire | The control plane obtains a scoped token from the issued card or wallet and calls the seller's endpoint. The agent never sees a credential. | **Interface only**: `checkout_path="programmatic"` returns `{available:false, milestone:"M16"}`. No seller we need accepts these today, and Issuing→SPT is preview |
| **B — human checkout with a system-issued card (primary)** | Any web checkout | The agent prepares a **checkout packet** (vendor, URL, plan, quantity, exact amount, which company email to sign up with, what to do with the resulting key). The control plane issues a **single-use, merchant-scoped card with the exact limit** (+ tolerance) and creates a **human task**. The human opens it, reveals the card on the provider's own page, and pays. Reconciliation sees the charge, closes the card and wakes the agent. | **Yes** (Stripe Issuing, Mercury) |
| **C — human checkout with the owner's manual card** | Manual mode (India, or no provider) | Same packet and task. The human pays with their own card (OTP etc.) and clicks "Mark paid" with the charged amount (and optional receipt). | **Yes** |
| **D — report-only** | Purchasing disabled, or the vendor needs a contract or invoice | The agent's request becomes a human task "Buy X" with justification. The human decides off-system and may record it as an expense. | **Yes** (when `purchasing_enabled=false` it degrades to D) |

**Why B is the realistic core:** it turns "the agent wants a tool" into a one-minute human action, while the money
controls stay enforced by the card network:
- exact limit;
- merchant category;
- single use;
- expiry.

Even if the human mistypes the merchant, the card cannot overspend. Stripe Issuing 3DS challenges go to the cardholder's
phone, which is the human anyway.

---

## 4. M15 design

### 4.1 Principles

- Enforcement in code, never prompts (AGENTS rule 9).
- Agents can request, never approve their own, never raise limits, never see card data (rule 2, D18).
- Defaults fail closed:
  - `purchasing_enabled=false`;
  - every AI approval band is 0, so everything goes to the human until the owner sets bands.
- No fake data (rule 3): unbuilt rails answer `{available:false, milestone:"M16"}`. The `fake` provider exists only in
  tests (not registered in production settings).
- Every state change writes an AuditEvent in the same transaction (rule 7). Card data never enters audit payloads.
- Money is micro-USD; settlements are `cost_kind=verified` (rule 5). Approved-but-unsettled holds are shown as
  "committed", never as charged.

### 4.2 Provider adapter interface (`backend/app/integrations/cards/base.py`)

```python
class CardProviderError(ProviderError): ...      # transient flag inherited
class CardProviderNotAvailable(CardProviderError): ...

@dataclass(frozen=True)
class MerchantLock:
    name: str | None = None            # display / matching only
    domain: str | None = None          # vendor_domain we expect (matching only)
    mcc_allow: tuple[str, ...] = ()    # provider category codes, if supported
    country: str | None = None

@dataclass(frozen=True)
class IssuedCard:                       # NOTE: no pan / cvc fields, by design
    provider_card_id: str
    last4: str
    brand: str | None
    exp_month: int | None
    exp_year: int | None
    status: str                         # active | frozen | closed
    limit_micros: int
    limit_interval: str                 # per_authorization | monthly | yearly | all_time
    single_use: bool
    reveal: "RevealHint"                # how a HUMAN can see the number (see 4.6)

@dataclass(frozen=True)
class CardTxn:
    provider_txn_id: str
    provider_card_id: str
    kind: str                           # authorization | capture | refund | decline | reversal
    status: str                         # pending | settled | reversed | declined
    amount_micros: int                  # USD, signed (refund < 0)
    merchant_amount_minor: int | None
    merchant_currency: str | None
    merchant_name: str | None
    merchant_mcc: str | None
    merchant_country: str | None
    occurred_at: datetime
    settled_at: datetime | None

@dataclass(frozen=True)
class Balance:
    available_micros: int | None
    pending_micros: int | None
    as_of: datetime
    currency: str = "usd"

@dataclass(frozen=True)
class Capabilities:
    issue_cards: bool                   # manual: False
    single_use: bool                    # stripe: True, mercury: emulated
    merchant_mcc_lock: bool             # stripe: True, mercury: False (unverified)
    per_auth_limit: bool
    interval_limits: tuple[str, ...]
    list_transactions: bool
    balance: bool
    webhooks: bool
    programmatic_checkout: bool         # M16
    test_mode: bool

class CardProvider(Protocol):
    id: str                             # "stripe_issuing" | "mercury" | "manual" | "fake"
    capabilities: Capabilities
    def check(self) -> dict: ...        # auth ok, issuing enabled, balance readable, mode (test/live)
    def issue_card(self, *, limit_micros: int, limit_interval: str, merchant_lock: MerchantLock | None,
                   single_use: bool, expires_at: datetime | None, idempotency_key: str,
                   metadata: dict[str, str]) -> IssuedCard: ...
    def update_limit(self, provider_card_id: str, *, limit_micros: int, limit_interval: str,
                     idempotency_key: str) -> IssuedCard: ...   # only ever called to LOWER limits by automation
    def freeze(self, provider_card_id: str) -> None: ...
    def unfreeze(self, provider_card_id: str) -> None: ...      # human-only path
    def close(self, provider_card_id: str) -> None: ...
    def list_transactions(self, *, since: datetime, cursor: str | None = None) -> tuple[list[CardTxn], str | None]: ...
    def balance(self) -> Balance: ...
    def funding_instructions(self) -> dict: ...                 # owner-only display, fetched live, never stored
    def reveal_hint(self, provider_card_id: str) -> "RevealHint": ...
    def parse_webhook(self, headers: Mapping[str, str], body: bytes, secret: str) -> list[CardTxn]: ...  # optional
```

Code-enforced rules:
- **No method returns a PAN or CVC.** A test introspects every adapter's dataclasses and HTTP calls.
- The Stripe client refuses any `expand` that contains `number` or `cvc`.
- The Mercury client has no Vault/reveal call. A test greps adapter modules for the forbidden endpoints.

Other rules:
- `metadata` carries only `{"cos_purchase": <id>, "cos_company": <id>}`, never task text (no data leakage, no injection
  echo).
- `idempotency_key = purchase_request_id` (Stripe `Idempotency-Key` header; Mercury: look up an existing card by
  nickname or metadata before creating).

### 4.3 Providers in M15

| id | Capability notes | Human steps (KYC/funding) | Verification claim allowed after M15 |
|---|---|---|---|
| `fake` | In-memory. Scripted authorizations/captures/refunds/declines; balance; failure injection. Registered only when `settings.environment == "test"` | — | Tests only |
| `manual` | `issue_cards=False`. Stores label, brand, last4 (optional, 4 digits only), owner's own limits for display. Every purchase is tier C; the human "Mark paid" creates the transaction | Owner uses their own card | Fully testable offline |
| `stripe_issuing` | Single-use cards, `spending_controls` (per_authorization + interval limits, `allowed_categories`), `GET /v1/issuing/transactions` + authorizations, balance via financial account / Issuing balance, webhook signature verification (`issuing_authorization.created`, `issuing_transaction.created`). **We do not use the 2 s real-time authorization webhook**: a self-hosted box behind an ephemeral quick tunnel (D37) cannot answer reliably. Static card controls do the enforcement. | US entity; Stripe KYB; Issuing application; fund the Issuing balance; restricted key | "Verified against Stripe sandbox" **only if** someone runs it with a sandbox key; else "unit-tested against recorded fixtures" |
| `mercury` | Cards API create/update limit/freeze/cancel [S5]. Single-use **emulated** (limit = amount, closed by reconciliation after the first capture). MCC lock not available (recorded as a capability gap; the merchant mismatch check at reconciliation still freezes). Transactions via account transactions endpoint (field mapping to verify) | US entity; Mercury account; fund via wire/ACH; API token (write tokens may need an IP allowlist, unverified) | Same: fixtures only unless run live |

**Deferred to M16 (and why):**
- Stripe Issuing→SPT programmatic checkout (preview; no target sellers).
- x402 / CDP wallet (US entity only; India regulatory risk).
- EnKash adapter (Indian prepaid API exists, but the PPI cross-border draft and OTP rules limit its value).
- Ramp (early access).
- Revolut / Airwallex.
- Stripe Issuing Elements in-app reveal (CSP change).

### 4.4 Purchase requests

#### 4.4.1 Agent tool `company_request_purchase`

Runtime file `runtime/tools/company_tools.py`. Also exposed to coding CLIs through the existing stdio MCP server.

```jsonc
{
  "item": "string, what exactly (e.g. 'Plausible Analytics Growth plan, 10k pageviews')",   // required, ≤200
  "vendor": "string (e.g. 'Plausible Insights OÜ')",                                         // required, ≤200
  "url": "https://… pricing or checkout page",                                                // required, https only
  "amount_usd": "number > 0, the full charge incl. tax if known",                             // required
  "original_amount": "number, optional (if priced in another currency)",
  "original_currency": "ISO-4217, optional",
  "recurring": "none | monthly | yearly",                                                     // required
  "quantity": "integer ≥1, default 1",
  "justification": "why this is needed for the task and why free options do not work",       // required, ≥40 chars
  "alternatives_considered": "string, optional",
  "task_id": "uuid of the task that needs it"                                                 // required
}
```

Returns `{purchase_request_id, status, decision, next_step}`. Examples of `next_step`:
- "Waiting for the AI Co-founder's approval";
- "The Founder will complete checkout; you will be woken when it is paid";
- "Purchasing is not set up: your request was handed to the Founder as a task".

The service validates the request:
- the URL is https, not private (reuse the M9 SSRF guard), and its host matches `vendor_domain`;
- `amount_usd` is converted to micros with `Decimal` (no floats);
- the task belongs to the agent's company, and the agent is its assignee or the project lead;
- dedupe: the same agent, normalized vendor and item within 24 h returns the existing request;
- rate limit: new key `purchase_requests_per_day` (default 5 per agent, 20 per company).

A second, small tool, `company_review_subscription(subscription_id, decision: keep|cancel, reason)`, is used in renewal
reviews (4.5.3). The agent prompt gains one paragraph in `runtime/company_agent/prompts.py`:
- prefer free tools;
- request purchases with this tool;
- **never ask for, store or type card numbers**;
- you will be woken with the outcome.

The agent-API routes need registry entries, so the existing route-enumeration test (D19) covers them:

| Route | Action | Tier |
|---|---|---|
| `POST /api/agent/v1/purchases` | `purchase.request` | `internal_write` (it commits nothing; rate key `purchase_requests_per_day`) |
| `GET /api/agent/v1/purchases/{id}` | `purchase.read` | `read` |
| `POST /api/agent/v1/subscriptions/{id}/review` | `subscription.review` | `internal_write` |

The money moment is the existing SPEND actions `purchase.make` (one-time) and `subscription.start` (recurring). They are
evaluated inside the purchasing service with `facts.amount_micros`.

New internal actions:
- `card.issue` (SPEND, `kind="internal"`; the platform issues the card after approval, never reachable from the agent API);
- `card.freeze` (`internal_write`, internal).

New human-only actions (`_HUMAN_ONLY_ACTIONS`): `card_account.connect`, `card.unfreeze`, `card.limit_raise`,
`card.reveal`, `purchase_policy.update`.

#### 4.4.2 Policy (company_policies, human-editable only; agents get 403 including via run token and MCP)

| Field | Default | Meaning |
|---|---|---|
| `purchasing_enabled` | false | Off → every request degrades to tier D (a human task) |
| `purchase_auto_approve_micros` | 0 | Per-charge amount the system may approve with no one (0 = band off) |
| `purchase_agent_auto_monthly_micros` | 0 | An agent's auto-approved total per company month |
| `purchase_cofounder_limit_micros` | 0 | Per-charge amount the AI Co-founder may approve (0 = band off) |
| `purchase_cofounder_monthly_micros` | 0 | Company-month total the Co-founder may approve |
| `purchase_monthly_budget_micros` | NULL | Hard company cap on held + settled purchases per month (NULL = no cap; human approvals still required per charge unless bands are set) |
| `purchase_recurring_requires_human` | true | The first start of any subscription goes to the human |
| `purchase_new_vendor_requires_human` | true | The first purchase from a vendor domain goes to the human |
| `purchase_tolerance_bps` / `purchase_tolerance_min_micros` | 1000 / 2_000_000 | Card limit = amount + max(10 %, $2) for tax and FX |
| `purchase_card_ttl_hours` | 72 | An unused card is closed and the request expires |
| `purchase_checkout_member_id` | NULL (owner) | Which human gets checkout tasks |
| `subscription_renewal_alert_days` | 7 | Renewal review lead time |

Agent override: `agents.purchase_monthly_limit_micros` (NULL = inherit). Only humans edit it (same rule as budgets,
D18).

**Code ceilings** (validation in `services/policy.py`, analogous to D18):
- `purchase_auto_approve_micros` ≤ $100 and `purchase_agent_auto_monthly_micros` ≤ $500;
- `purchase_cofounder_limit_micros` ≤ $1,000;
- the owner can still approve anything by hand. These are proposed values; the owner decides.

**Decision (deterministic, in `services/purchasing.py` → `guardrails.rules.default_decision` gets a purchase branch):**

```
deny   if company paused/emergency stop, or held+settled+amount > purchase_monthly_budget (→ request rejected, human alert)
human  if !purchasing_enabled (tier D task) or recurring && purchase_recurring_requires_human
       or new vendor && purchase_new_vendor_requires_human or amount > purchase_cofounder_limit
       or co-founder month total would exceed purchase_cofounder_monthly or requester is the Co-founder itself
cofounder if amount > purchase_auto_approve or agent auto month total would exceed purchase_agent_auto_monthly
allow  otherwise
final = strictest(policy decision, guardrail rule resolution for purchase.make / subscription.start)   # rules can only tighten unless a human wrote an explicit allow
```

The existing `auto_spend_limit_micros` keeps governing other SPEND actions (`model.paid_fallback`). `domain.purchase`
keeps its `human_required` floor.

#### 4.4.3 Approval flow and state machine

```
requested ──policy──► approved(auto) ─────────────┐
    │                                              │
    ├─► pending_approval (Approval kind=external_purchase, scope_hash over
    │     {action, purchase_request_id, vendor_domain, amount_micros, recurrence, limit_micros, merchant_lock})
    │       ├─ approved (Co-founder via company_decide_approval, or human in Wallet/Approvals) ─┤
    │       ├─ rejected / changes_requested → agent woken with the note
    │       └─ expired (7 days) → agent woken
    │                                              ▼
    │                                  issuing (job, idempotent; consumes the approval)
    │                                     ├─ manual provider → awaiting_checkout (no card)
    │                                     └─ card provider → card_issued → awaiting_checkout
    │                                              ▼
    │                         human task "Buy <item> from <vendor> (≤ $X)" with the checkout packet
    │                                              ▼
    │            authorized (txn seen) → settled (capture; ExternalExpense verified) → done; agent woken
    │            └─ ttl passed with no txn → card closed, expired, hold released, agent woken
    └─► human_task (tier D, purchasing disabled)
```

- The approval binds the exact scope (existing M6 `scope_hash`, single use). A changed amount or vendor needs a new
  approval. Issuing consumes the approval in the same transaction that records `card.issue` intent. The provider call
  happens after commit, with `idempotency_key`. On provider failure the request goes to `issue_failed` with backoff
  (transient) or an alert (permanent). **It never falls back to another provider or account.**
- Holds: from approval until settlement, the request's `held_micros` (= amount) counts toward agent and company purchase
  totals. Settlement replaces it with `settled_micros`. Serialization: reuse `ledger.acquire_budget_lock` (D9).
- **The checkout packet** (stored on the request and shown in the human task):
  - vendor and **domain shown explicitly** (the URL is untrusted agent input, rendered as text with an "opens external
    site" warning);
  - plan and quantity;
  - the exact amount and the card limit;
  - which company identity to use (the M8 company email, if any);
  - what to bring back (e.g. "paste the API key into Integrations → Credentials scoped to `analytics.read`");
  - card last4, expiry and a **Reveal on Stripe/Mercury** button;
  - steps: "1 open vendor, 2 choose plan, 3 reveal card, 4 pay, 5 done".
- The human task is the existing M7 human-assignable task. "Done" requires either a seen authorization or a manual "Mark
  paid".

### 4.5 Reconciliation, ledger, subscriptions, balance

#### 4.5.1 Sync

A deterministic scheduler pass, `card_reconcile.run_all`, every 15 min per company with an active card account, plus
`POST …/wallet/sync`:
- calls `list_transactions(since=last_cursor - 3 days)`;
- upserts `card_transactions`, idempotent on `(card_account_id, provider_txn_id)`.

Webhooks are optional:
- `POST /api/webhooks/cards/{company_id}/{provider}`, signature-verified, the same 401 for every failure (pattern of
  `webhooks.py`);
- they only trigger a sync;
- polling stays the source of truth, because the public URL may be ephemeral (D37).

#### 4.5.2 Matching (each issued card belongs to exactly one purchase or subscription)

| Event | Action |
|---|---|
| Authorization on a purchase card | purchase → `authorized`; checkout task auto-completes with a note; the agent is woken |
| Capture/settlement | purchase → `settled`; write **`external_expenses`** row (`source="card"`, `cost_kind="verified"`, `purchase_request_id`, `card_transaction_id` unique); single-use card **closed** (Mercury emulation); hold released |
| Refund | negative expense row (verified), linked |
| Settled amount > approved amount + tolerance | Cannot normally happen (card limit). If it does: critical alert + freeze |
| Merchant mismatch (name/MCC/domain heuristics) | freeze card + alert `card_merchant_mismatch` |
| **Transaction on an agent card with no matching request** | critical alert + **freeze** |
| Decline | record; if the reason is limit/insufficient funds → alert with a funding hint; agent woken with "declined, Founder notified" |
| Manual mode "Mark paid" | human enters the merchant amount (USD) + optional INR billed + optional receipt artifact → `card_transactions(kind=capture, source=human)` + expense `cost_kind="verified"`, `source="human"`; UI labels it "confirmed by you" |

**Finance integration:**
- `finance.summary` already returns `external_expenses_micros`.
- Add `purchases: {settled_verified_micros, held_micros, budget_micros}`.
- The Finance panel gets a "Purchases" card.
- Model-usage budgets (`usage_records`, reservations) stay separate, so a $5/month agent model budget does not block a
  $20 tool purchase. This is a decision for the owner (sections 5 and 7).

#### 4.5.3 Subscriptions

A `subscriptions` row is created when a recurring purchase settles.
- Its card is kind `subscription`:
  - not single-use;
  - `limit_interval` monthly or yearly;
  - limit = amount + tolerance;
  - merchant-locked where supported.
- The daily pass `subscriptions.check`:
  1. **Renewal review:** `subscription_renewal_alert_days` before `next_renewal_on`:
     - wake the owning agent (or its manager if retired) with a task to call `company_review_subscription`;
     - `keep` → noted;
     - `cancel` or no answer within 48 h → **human task "Cancel <vendor> before <date>"** plus an alert. Cancelling on
       a vendor site is a human action.
     - Once marked cancelled, the card is closed after the paid period ends.
  2. **Renewal charge seen within tolerance** → settled expense, `next_renewal_on` advanced.
  3. **Price increase beyond tolerance** → the card declines it (limit). The alert includes the new price, and the agent
     must file a new request (approval).
  4. **Missed renewal** (3 days after the due date with no charge) → info alert (possibly lapsed).
- Freezing a card is not a cancellation (the vendor may dun). The UI says so.

#### 4.5.4 Balance and funding (adapters with `balance`)

- Hourly pass: `available < open holds + renewals due in 30 days + buffer` → alert `wallet_low_balance`, which links to
  the funding guide with the exact live instructions (`funding_instructions()`, owner-only).
- A provider decline for insufficient funds → the same alert, critical.

### 4.6 Secrets, PAN and PCI

- **Never stored, never transmitted by us:** PAN, CVC, full expiry with PAN, PIN, 3DS codes.
- We store `last4`, `brand`, `exp_month`/`exp_year` and the provider card id (non-sensitive without the PAN).
- Because we never store, process or transmit card numbers, the install stays out of PCI DSS cardholder-data scope.
  Stripe states own-use Issuing cards do not require a PCI program (via [S1]/[S2]; confirm in the go-live review).
- **Reveal for humans (`GET …/wallet/cards/{id}/reveal`):**
  - owner/admin only;
  - requires recent authentication (re-enter password/passkey within 5 min; reuse M7 step-up if present, else add it);
  - rate limited;
  - audited as `card.reveal_opened` (no data);
  - returns a `RevealHint`: `{kind: "provider_dashboard", url}` (Stripe: dashboard card page; Mercury: card page in the
    Mercury app).
  - Opt-in M16: `{kind: "issuing_elements", ephemeral_key_secret, nonce}` for Stripe Issuing Elements. That needs a
    **separate route with its own CSP** allowing `https://js.stripe.com`, because the SPA CSP is `script-src 'self'`
    (`backend/app/web.py:31`). Do not weaken the main CSP.
- **API keys:**
  - stored through the existing connected-accounts vault (`CredentialCipher`, `catalog.ProviderSpec` with
    `category="cards"`);
  - restricted Stripe key (Issuing cards/cardholders write, authorizations/transactions/balance read);
  - never sent to agents; the control plane makes every call (D16/D22 pattern).
- **DLP (`services/secrets_scan.py`)** adds:
  1. a **PAN detector**: 13–19 digits with optional spaces/dashes, Luhn-valid, not all one digit, redacted as
     `[card number]`. It applies to agent tool inputs and outputs, artifacts, outbound communications, audit payloads,
     notes and logs.
  2. **Stripe key patterns** `\b[sr]k_(live|test)_[A-Za-z0-9]{16,}` and `whsec_…`. The current `sk-` pattern misses
     Stripe's underscore keys: **an existing gap**.
  3. a **Mercury token** pattern (`secret-token:mercury_…`, format unverified).
  - The manual-card form rejects anything PAN-like (only 4 digits are accepted).
- **Prompt injection:**
  - the vendor URL, item and justification are untrusted agent text. They are rendered as text, never as HTML links that
    auto-open, and the domain is shown separately.
  - Card limits and merchant locks come from the approved scope, not from the agent at checkout time.
- **Emergency stop / pause / retire:**
  - `emergency.stop` and a company pause **freeze every active agent card** (best effort, logged per card) and block
    issuance.
  - Resume does not unfreeze. A human unfreezes per card or with "Unfreeze all".
  - Agent retirement: close its unused purchase cards; transfer its subscriptions to its manager or the Co-founder.

### 4.7 Schema: migration `0014_agent_cards.py`

- Revision "0014". `down_revision` is "0012" for now (0013 had not landed on `main` when step 0 was written);
  whoever merges second re-parents it. The migration depends on no 0013 content.
- Portable SQL only. String enums, JSON, BigInteger, no partial indexes.

**`company_policies` add:**
- `purchasing_enabled` Boolean false;
- `purchase_auto_approve_micros` BigInteger 0;
- `purchase_agent_auto_monthly_micros` BigInteger 0;
- `purchase_cofounder_limit_micros` BigInteger 0;
- `purchase_cofounder_monthly_micros` BigInteger 0;
- `purchase_monthly_budget_micros` BigInteger NULL;
- `purchase_recurring_requires_human` Boolean true;
- `purchase_new_vendor_requires_human` Boolean true;
- `purchase_tolerance_bps` Integer 1000;
- `purchase_tolerance_min_micros` BigInteger 2000000;
- `purchase_card_ttl_hours` Integer 72;
- `purchase_checkout_member_id` String(36) NULL;
- `subscription_renewal_alert_days` Integer 7.

**`agents` add:** `purchase_monthly_limit_micros` BigInteger NULL.

**New table `card_accounts`:**
- id, company_id FK, provider String(32), label String(120);
- status String(16) (`setup|active|attention|disabled`), mode String(8) (`live|test`);
- connected_account_id FK connected_accounts NULL (NULL for manual);
- external_ref String(120) NULL (Stripe cardholder id / Mercury account id);
- manual_brand String(24) NULL, manual_last4 String(4) NULL;
- is_default Boolean;
- last_balance_micros BigInteger NULL, balance_checked_at;
- sync_cursor String(200) NULL, last_synced_at, last_error String(500) NULL;
- created_by_type/id, created_at, updated_at.
- Index (company_id, status). App-enforced: one default per company.

**New table `agent_cards`:**
- id, company_id, card_account_id FK, provider_card_id String(120);
- kind String(16) (`single_use|subscription`);
- status String(16) (`active|frozen|closed|expired`);
- purchase_request_id NULL, subscription_id NULL, agent_id NULL;
- last4 String(4), brand String(24) NULL, exp_month, exp_year;
- limit_micros BigInteger, limit_interval String(24), merchant_lock JSON;
- expires_at, frozen_reason String(64) NULL, closed_at;
- created_at, updated_at.
- Unique (card_account_id, provider_card_id); index (company_id, status).

**New table `purchase_requests`:**
- id, company_id, agent_id FK, run_id String(36), task_id FK, project_id FK NULL;
- item String(200), vendor String(200), vendor_domain String(253), url String(2000);
- quantity Integer;
- amount_micros BigInteger, original_amount_minor BigInteger NULL, original_currency String(3) NULL;
- recurrence String(16) (`one_time|monthly|yearly`);
- justification Text, alternatives Text NULL;
- status String(24), decision String(16) (`auto|cofounder|human|report_only`);
- checkout_path String(24) (`programmatic|human_checkout|human_buys`);
- approval_id FK approvals NULL, card_id NULL, subscription_id NULL, checkout_task_id NULL;
- held_micros BigInteger 0, settled_micros BigInteger NULL, cost_kind String(16) NULL;
- checkout_packet JSON;
- dedupe_key String(200), period_month String(7);
- expires_at, created_at, updated_at.
- Indexes (company_id, status), (company_id, agent_id, period_month), (company_id, vendor_domain); unique
  (company_id, dedupe_key).

**New table `card_transactions`:**
- id, company_id, card_account_id, card_id NULL, purchase_request_id NULL, subscription_id NULL;
- provider_txn_id String(120);
- kind String(16), status String(16);
- amount_micros BigInteger, merchant_amount_minor BigInteger NULL, merchant_currency String(3) NULL;
- merchant_name String(200) NULL, merchant_mcc String(8) NULL, merchant_country String(2) NULL;
- match_status String(16) (`matched|unmatched|mismatch`);
- source String(16) (`provider|human`);
- occurred_at, settled_at, raw JSON (redacted; provider objects carry no PAN), created_at.
- Unique (card_account_id, provider_txn_id); index (company_id, occurred_at).

**New table `subscriptions`:**
- id, company_id, purchase_request_id, agent_id NULL;
- vendor, vendor_domain, item;
- amount_micros, recurrence;
- card_id NULL;
- status String(24) (`active|review_due|cancel_requested|cancelled|lapsed`);
- started_on, next_renewal_on, last_charged_on, last_charge_micros;
- review_task_id NULL, cancel_task_id NULL, reviewed_at;
- created_at, updated_at.
- Index (company_id, status, next_renewal_on).

**`external_expenses` add:**
- `cost_kind` String(16) NOT NULL server_default 'verified' (existing rows are human records of real charges; document
  this);
- `source` String(16) server_default 'human' (`human|card|provider_action`);
- `purchase_request_id` NULL;
- `card_transaction_id` NULL, unique together with company_id.

**Enums:**
- `ApprovalKind` already has `external_purchase`.
- Add `PurchaseStatus`, `CardStatus`, `CardKind`, `SubscriptionStatus`, `CardProviderId`.

### 4.8 API endpoints

All under `/api/companies/{cid}`:
- members can read;
- owner/admin can write;
- non-members get 404;
- lists return `{items, next_cursor}`.

| Method & path | Purpose |
|---|---|
| `GET /wallet` | Overview: `{available, enabled, accounts[], balance{available_micros, as_of}, month{held, settled_verified, budget}, awaiting_checkout[], pending_approvals, renewals_due[]}`. Not set up → `{available:true, enabled:false, setup:{recommended…}}` |
| `GET /wallet/providers` | Catalog: id, label, capabilities, eligibility text per country, KYC/funding guide steps, what is and is not verified |
| `POST /wallet/accounts` | Connect (provider + connected_account_id, or manual: label/brand/last4) |
| `POST /wallet/accounts/{id}/check` | Live check (no card is created in live mode; test mode may create and close a $1 card) |
| `GET /wallet/accounts/{id}/funding` | Live funding instructions, owner/admin only, never cached |
| `POST /wallet/accounts/{id}/default` · `DELETE /wallet/accounts/{id}` | Default / disable (disable freezes its cards) |
| `GET /wallet/cards` · `POST /wallet/cards/{id}/freeze` · `…/unfreeze` (human-only) · `…/close` · `GET /wallet/cards/{id}/reveal` (step-up) | Cards |
| `GET /purchases` · `GET /purchases/{id}` | Requests with the packet, approval, card and transactions |
| `POST /purchases/{id}/approve` · `…/reject` | Thin wrappers over `approvals.decide` (same scope hash) |
| `POST /purchases/{id}/mark-paid` | Manual/tier C: `{merchant_amount_usd, billed_amount_inr?, receipt_artifact_id?}` |
| `POST /purchases/{id}/cancel` | Human cancels (closes the card, releases the hold) |
| `GET /subscriptions` · `POST /subscriptions/{id}/cancelled` · `…/keep` | Subscriptions |
| `GET /wallet/transactions` | Ledger of card transactions |
| `POST /wallet/sync` | Reconcile now |
| `PATCH /policy` | Existing endpoint, extended with the purchase fields (human-only, ceilings validated) |
| `POST /api/webhooks/cards/{company_id}/{provider}` | Optional, signature-only auth |
| Agent API: `POST /purchases`, `GET /purchases/{id}`, `POST /subscriptions/{id}/review` | See 4.4.1 |

**Command Center:** `/wallet` slash command (instant). **CLI:** `foundryos wallet status|sync` (HTTP, D23); no card
secrets over argv.

### 4.9 Web (`web/src/features/wallet/`, `src/api/wallet.ts`)

**`WalletWidget`** (canvas, `xl:col-span-4`, title "Agent cards"). States:
- **loading**;
- **error** (retry);
- **not available** (`available:false` → "Agent cards come with M15");
- **not set up**: "Agents can't buy anything yet." plus a primary button **Set up agent cards** (opens
  `?panel=wallet&tab=setup`) and a line about what agents will do meanwhile (requests become your tasks);
- **set up, active**:
  - balance with "as of" time (provider-reported), or "Manual card ••1234";
  - month: settled (verified) / committed (held) / purchase budget bar;
  - **Awaiting your checkout (n)** list (item, vendor, amount, "Open checkout");
  - pending approvals (n) → Approvals;
  - renewals in the next 7 days;
  - low-balance banner with a "How to add funds" button;
- **attention**: provider check failing, or frozen after an emergency stop.

**`WalletPanel`** tabs:
- **Overview**.
- **Purchases**: filters by status. Detail drawer: packet, timeline, approval, card, transactions, Approve/Reject (if
  pending and human), Cancel.
- **Cards**: freeze, close, and "Reveal on Stripe" (human, step-up dialog).
- **Subscriptions**: next renewal, review status, "Mark cancelled".
- **Transactions**: cost kind and source badges ("verified · provider", "verified · confirmed by you").
- **Setup**: the wizard below.

**Checkout task view** (in `MyWorkPanel` and the task panel), when a task carries `purchase_request_id`, uses a
`CheckoutPacket` component:
- the numbered steps;
- the vendor domain prominently;
- **Reveal card** (provider link) and **Mark paid** (manual) or an automatic "Paid ✓" when the authorization appears.

Other surfaces:
- **ApprovalsWidget / ApprovalCard**: purchase approvals show item, vendor domain, amount, recurring, justification, the
  requesting agent and the task.
- **Settings → PolicySection**: a "Purchasing" group with the fields from 4.4.2 and helper text showing the code ceilings.
- **Finance**: a "Purchases" card.

Every button works (no inert buttons); design tokens only.

**Setup wizard ("Set up agent cards")**:
1. **Where is your company?** India company · US company (LLC/C-corp) · Not incorporated yet · Other. Then: "Do you
   already have Mercury / Stripe?"
2. **Recommendation** with honest text from `/wallet/providers`:
   - India → Manual card (bank corporate credit card), with the OTP/e-mandate explanation;
   - US → Mercury (if you bank there) or Stripe Issuing (best controls; Atlas requires a US representative address);
   - not incorporated → Manual (personal international credit card; LRS/TCS note).
3. **What you must do** (checklist; the human ticks each step, and the system verifies the ones it can).
4. **Connect** (paste the restricted key in a hidden input → connected account → live check), or for manual: label,
   brand, last4.
5. **Set purchase limits** (suggested: auto off; Co-founder ≤ $50/charge and $200/month; recurring and new vendors →
   you).
6. **Done** → `purchasing_enabled=true`.

The wizard also appears as an optional step in the Founder checklist (`services/checklist.py`).

### 4.10 Funding guides: exact steps the user must do (content in `integrations/cards/guides.py`)

#### Stripe Issuing (US entity)

1. Have a US company with an EIN and a Stripe account in its name (Stripe Atlas works).
   - **Atlas condition:** a representative with a US address, and activity and cardholders primarily in the US [S3].
   - If you live in India and have no US address, Stripe may decline: apply anyway or choose Mercury.
2. Dashboard → **Issuing** → apply for the **Standard** program (no sales call).
   - Defaults: 25 cards/week, $5,000/week, $500 per card per day [S2]; ask Stripe to raise them.
3. Fund it: Dashboard → Balances → **Issuing balance → Add funds**, by ACH or wire from your US bank account to the
   routing/account number Stripe shows (≈1 business day), or transfer from your Stripe payments balance [S2].
4. Developers → API keys → **Create restricted key** "Foundry agent cards":
   - Write: Issuing Cards, Issuing Cardholders;
   - Read: Issuing Authorizations, Issuing Transactions, Balance;
   - nothing else.
5. Paste it in Foundry (hidden field). Foundry creates one cardholder "<Company> agents" with your business billing
   address and **your phone number (3DS codes come to you)**.
6. Optional: add a webhook endpoint (only with a stable public URL) and paste the signing secret.
7. Test first with a **sandbox** key (instant) [S2].
8. **From India:** wiring money from India to your own US company is an overseas investment (ODI) under FEMA. Do it
   through your bank's ODI process; talk to your CA first.

#### Mercury (US entity)

1. Open a Mercury business account for your US company (passport, EIN). Mercury serves many non-US founders; check that
   your country is eligible.
2. Fund it by international wire (SWIFT) from India or by ACH/wire from another US account (same ODI note).
3. Settings → API tokens → create a **read/write** token (Mercury may ask for an IP allowlist for write tokens; add your
   server's public IP). Paste it in Foundry.
4. Foundry creates a virtual card per approved purchase with a limit equal to the amount, and closes it after the
   charge. Mercury has no single-use or merchant lock: the limit and our closing do the enforcement.
5. To see a card number, open it in the Mercury app. Foundry never fetches it.

#### Manual card (India company)

1. Get a **bank corporate credit card** (or RazorpayX corporate card).
   - Prepaid cards (EnKash/Volopay) may lose cross-border use under RBI's 2026 PPI draft.
2. In the bank app, **enable online and international transactions** (off by default on Indian cards) and set the card's
   own limit to your monthly purchase budget.
3. Keep the phone that receives the card OTPs with you: Indian cards need an OTP for most online payments.
4. For subscriptions, accept the e-mandate at the first payment (OTP once). Later renewals up to ₹15,000 run
   automatically, with a bank notice 24 h before.
5. In Foundry, enter only a label and the **last 4 digits**. Never the full number.
6. Tax: foreign SaaS is an import of services (not LRS) for a company. Ask your CA about GST reverse charge and
   withholding (s.195).

#### Manual card (not incorporated, India resident)

- Same steps 2–5 with a personal international credit card.
- Payments abroad fall under LRS (USD 250k/yr). Credit-card spend abroad was excluded from LRS/TCS in 2023; confirm
  current rules with your CA.

### 4.11 Autonomy: what runs by itself vs what is always human

| Runs autonomously (code-enforced) | Always human |
|---|---|
| Agents detect the need and file `company_request_purchase` | KYC/KYB with any provider; opening accounts; Stripe/Mercury applications |
| Policy decision; auto-approval within the auto band; AI Co-founder approval within its band | **Funding** the account (wires, top-ups, ODI paperwork) |
| Card issuance per approved purchase (exact limit, merchant lock, single use, TTL) | **Raising** any limit or band, enabling purchasing, unfreezing cards |
| Checkout packet + human task creation, notifications | Clicking **Pay** at checkout (tier B/C); OTP/3DS |
| Reconciliation, expense recording (verified), closing used or expired cards, freezing on anomalies | Accepting vendor terms / creating vendor accounts (D16) |
| Subscription tracking, renewal reviews by the owning agent, cancel reminders, missed-renewal and price-increase alerts | Actually **cancelling** a subscription on the vendor's site |
| Low-balance and decline alerts with live funding instructions | Revealing a card number (on the provider's surface) |
| Emergency stop / pause → freeze all agent cards | Recurring starts and new vendors (default; owner may relax) |

### 4.12 Tests (fake provider only; never a real card API)

`tests/conftest.py` gets an autouse guard that fails any outbound connection to `*.stripe.com`, `*.mercury.com`
(httpx transport or socket guard). Real adapters are tested with `httpx.MockTransport` and handwritten fixtures modelled
on the docs.

**Backend** (`tests/test_m15_*.py`):
1. **Tenancy:** another company's accounts, cards, purchases, transactions and subscriptions → 404, both on the human
   API and the agent API.
2. **Self-modification:** an agent (run token and MCP path) cannot change purchase policy, its own
   `purchase_monthly_limit_micros`, connect accounts, unfreeze, reveal, approve its own request, or have the Co-founder
   approve the Co-founder's own request (→ human).
3. **Policy resolution:** each band; monthly accumulation (agent, Co-founder, company cap); recurring and new-vendor
   floors; all-zero defaults → human; disabled → tier D; ceilings rejected on policy update; guardrail rules can tighten.
4. **Approval scope binding:** a $20 approval cannot issue a $200 card; single use; a change in vendor or amount → new
   approval; expiry.
5. **Issuance:**
   - limit = amount + tolerance; single-use for one-time; interval limit for subscriptions; TTL;
   - idempotent on retry (the same provider card);
   - provider transient error → backoff; permanent → alert; never another provider;
   - pause/emergency → no issuance.
6. **PAN never leaves the provider:**
   - adapter dataclasses have no pan/cvc fields;
   - the Stripe adapter refuses `expand=number|cvc`;
   - adapter source has no Vault/reveal endpoint;
   - the DLP redacts Luhn-valid PANs in tool results, artifacts, audit and logs;
   - Stripe `sk_live_`/`rk_live_`/`whsec_` keys are detected;
   - the manual card rejects a full PAN;
   - the API JSON schemas have no pan/cvc fields (OpenAPI walk).
7. **Reconciliation:**
   - capture → expense `verified`/`card`, once (replay-safe);
   - refund negative;
   - an unmatched transaction on an agent card → freeze + critical alert;
   - merchant mismatch → freeze;
   - single-use (and Mercury emulation) closes after capture;
   - TTL expiry closes the card and releases the hold;
   - a decline for insufficient funds → low-balance alert.
8. **Subscriptions:**
   - review task at N days; keep/cancel paths; no answer → human cancel task;
   - renewal within tolerance advances the date;
   - a price increase is declined → alert + new request needed;
   - missed renewal → alert.
9. **Manual mode:** mark-paid creates a human-sourced verified expense; the INR billed amount is stored but not converted.
10. **Emergency stop** freezes all cards; resume does not unfreeze. Retirement closes cards and transfers subscriptions.
11. **Webhook:** bad signature → 401 (same body as unknown company); a good signature only triggers a sync.
12. **Migration** 0014 up/down on SQLite. Portability lint (no SQLite-only SQL). The existing route-enumeration and
    tier-registration tests pass with the new routes.
13. **Finance** summary includes the purchases block. Model budgets are unaffected by purchases.

**Runtime:**
- tool schema and argument validation;
- the tool is present in the right toolsets and MCP;
- the prompt paragraph is present;
- the tool never echoes PAN-like strings.

**Web (vitest):**
- WalletWidget: loading / error / not-available / not-set-up / active / attention / low-balance;
- the wizard paths for India, US and not incorporated;
- Reveal only for owner/admin with step-up;
- CheckoutPacket mark-paid;
- the approval card for purchases;
- the PolicySection purchasing form validation;
- every button has a handler.

### 4.13 Work packages (Sonnet implementers; strict file ownership)

#### Step 0 — contract (sequential, ~1–2 h, planner or WP1 before the others start)

Files (owned by WP1 afterwards):
- `backend/alembic/versions/0014_agent_cards.py`;
- `backend/app/models/purchasing.py` (new: CardAccount, AgentCard, PurchaseRequest, CardTransaction, Subscription);
- `backend/app/models/enums.py` (new enums);
- `backend/app/models/policy.py`, `backend/app/models/agent.py`, `backend/app/models/guardrails.py` (new columns);
- `backend/app/models/__init__.py`;
- `backend/app/schemas/purchasing.py` (all request/response models);
- `backend/app/integrations/cards/__init__.py`, `base.py` (Protocol + dataclasses above), `fake.py`;
- `backend/app/guardrails/registry.py` (new actions/routes, `purchase_requests_per_day`);
- `backend/app/guardrails/tools.json` (two tools);
- the spec `docs/specs/m15-agent-cards-and-purchasing.md` (from this plan).

Commit path by path.

#### WP1 — Purchasing core + API (backend)

Owns:
- `backend/app/services/purchasing.py` (requests, policy decision, approval, issuance job, mark-paid, cancel, checkout
  packet + human task);
- `backend/app/services/wallet.py` (accounts, cards, reveal hint, overview, providers listing);
- edits to `services/policy.py` (fields + ceilings), `schemas/policy.py`, `guardrails/rules.py` (purchase branch in
  `default_decision`, `facts_from_payload`), `services/emergency.py` (freeze all), `services/agents.py` (retire hook),
  `services/finance.py` (purchases block), `services/checklist.py` (setup step), `services/command_center.py` (`/wallet`);
- `backend/app/api/routes/wallet.py` (new), `api/routes/agent_api.py` (three agent routes), `main.py` (router include);
- `backend/app/schemas/agent_api.py` additions;
- tests `tests/test_m15_purchasing.py`, `test_m15_policy.py`, `test_m15_api.py`, `test_m15_migration.py`;
- docs: `docs/architecture.md` (decision **D38**: agent cards, tiered checkout, no PAN; D39: purchases separate from model
  budgets), `docs/roadmap.md` M15 row.

Depends on step 0 only. It calls reconciliation through `card_reconcile.sync_company()` (WP2's signature, fixed in
step 0 as a stub).

#### WP2 — Card adapters, reconciliation, subscriptions, DLP (backend)

Owns:
- `backend/app/integrations/cards/stripe_issuing.py`, `mercury.py`, `manual.py`, `registry.py` (provider id → adapter
  from a connected account), `guides.py` (funding guides + eligibility text);
- `backend/app/integrations/catalog.py` (`category="cards"` specs for stripe_issuing and mercury, with `check`);
- `backend/app/services/card_reconcile.py` (sync, matching, expenses, holds release, closing, anomalies, balance alerts);
- `backend/app/services/subscriptions.py` (renewal reviews, cancel tasks, missed/price alerts);
- `backend/app/engine/scheduler.py` (two passes: 15-min sync, daily subscriptions, hourly balance);
- `backend/app/api/routes/webhooks.py` (card webhook route);
- `backend/app/services/secrets_scan.py` (PAN Luhn detector, Stripe/Mercury key patterns);
- tests `tests/test_m15_adapters.py` (MockTransport fixtures, forbidden-endpoint test), `test_m15_reconcile.py`,
  `test_m15_subscriptions.py`, `test_m15_dlp.py`, and the conftest network guard (new `tests/m15_helpers.py`; the
  conftest edit is one fixture line, coordinated).

#### WP3 — Agent tool (runtime + toolkit)

Owns:
- `runtime/tools/company_tools.py` (`company_request_purchase`, `company_review_subscription`);
- `runtime/company_agent/spec.py`, `runtime/company_agent/prompts.py` (purchasing paragraph, "never handle card numbers");
- `runtime/toolsets.py`;
- `runtime/FORK.md` (log entry);
- runtime tests `runtime/company_agent/tests/test_company_tools.py` (additions);
- `backend/app/services/agent_toolkit.py` (which role families get the tools: all work roles request; the Co-founder
  reviews);
- `backend/app/roles/builtin/*.yaml` (tool lists);
- the MCP tool exposure in `backend/app/runtimes/cli/` where company tools are listed (verify the exact file; touch only
  the tool list);
- backend test `tests/test_m15_toolkit.py`.

#### WP4 — Web

Owns:
- `web/src/features/wallet/*` (WalletPanel, tabs, SetupWizard, CheckoutPacket, RevealDialog, ProviderGuide, tests);
- `web/src/features/canvas/widgets/WalletWidget.tsx`;
- edits to `web/src/features/canvas/CanvasPage.tsx` (grid), `features/canvas/PanelHost.tsx` (`wallet` case),
  `features/approvals/ApprovalCard.tsx` (purchase details), `features/mywork/MyWorkPanel.tsx` + `features/panels/TaskPanel.tsx`
  (CheckoutPacket), `features/settings/PolicySection.tsx` + `policyForm.ts` (purchasing group),
  `features/finance/*` (Purchases card);
- `web/src/api/wallet.ts`, `web/src/api/index.ts`, `web/src/api/keys.ts`;
- `web/src/api/schema.d.ts` (regenerate with `pnpm gen:api` after WP1 routes exist; until then code against the step-0
  schemas).

**Merge order:**
1. step 0;
2. WP2 and WP3 (independent);
3. WP1 (integrates the reconcile stubs);
4. WP4 (regenerate the schema).

Then run the full `uv run pytest`, `ruff`, `pnpm test`, `typecheck` and `lint`.

### 4.13a Step 0 as built (2026-10-27) and deviations from the signatures above

Created: `backend/alembic/versions/0014_agent_cards.py`; `app/models/purchasing.py`; `app/schemas/purchasing.py`;
`app/integrations/cards/{__init__,base,fake}.py`; stub-signature modules `app/services/{purchasing,wallet,card_reconcile,subscriptions}.py`;
tests `tests/test_m15_{migration,fake_provider,contract}.py`. Changed: `app/models/{enums,policy,agent,guardrails,__init__}.py`,
`app/guardrails/{registry.py,ratelimit.py,tools.json}`, `docs/api.md` (rate-limit table).

Deviations (all deliberate; WP owners should treat these as the contract):

1. **`CardProviderError` has a `transient` flag.** `ProviderError` has none, so the plan's "transient flag
   inherited" is added on `CardProviderError.__init__(..., *, transient=False)`.
2. **`CardTxn.decline_reason`** (optional, default `None`) was added so reconciliation can tell
   `insufficient_funds` from `spending_limit` (section 4.5.2, "Decline"). It never carries PII.
3. **`RevealHint`** is a frozen dataclass `(kind, url)` with kind `provider_dashboard` (M16: `issuing_elements`).
4. **The three agent-API `RouteSpec`s are defined as `registry.M15_ROUTES` but not yet part of `ROUTES`.** A route
   registered without an endpoint fails `test_no_registered_route_is_stale`. WP1 adds `_M15_ROUTES` to `_ROUTES`
   in the same change that adds the three endpoints.
5. **`purchase_requests_per_day`** also needed a `Limit` in `guardrails/ratelimit.py` (5 per agent, 20 per company,
   one day); without it `LIMITS[key]` fails. The API doc table has the new row.
6. **`tools.json` now lists the two new company tools**, but `runtime/tools/company_tools.py` does not define them
   yet. `runtime/company_agent/tests/test_guard_registry.py::test_every_company_tool_the_runtime_knows_is_classified_and_vice_versa`
   compares the two and fails until WP3 adds `company_request_purchase` and `company_review_subscription`. That
   interlock is intentional (a tool without a tier, or a tier without a tool, must not ship).
7. **Circular ids are plain indexed ids, not foreign keys:** `agent_cards.purchase_request_id` / `subscription_id`,
   `purchase_requests.card_id` / `subscription_id`, `subscriptions.purchase_request_id` / `card_id`,
   `card_transactions.purchase_request_id` / `subscription_id`, `external_expenses.purchase_request_id` /
   `card_transaction_id`. The tables point at each other, so a foreign key would force a fragile migration order;
   services keep them consistent and tests assert it.
8. **`external_expenses` unique constraint** is `(company_id, card_transaction_id)`; human rows have NULL there and
   may repeat (SQLite and Postgres both allow multiple NULLs in a unique index).
9. **The policy response/update schemas are not extended in step 0.** `schemas/purchasing.py` carries
   `PurchasePolicy` / `PurchasePolicyUpdate` (with the ceilings) for WP1 to fold into `Policy` / `PolicyUpdate`, so
   the existing `/policy` contract does not change before the service can honour it.
10. **Extra enums** beyond the five named in section 4.7: `CardAccountStatus`, `CardMode`, `PurchaseDecision`,
    `CheckoutPath`, `CardTxnKind/Status/Match/Source`, `ExpenseSource`, plus `PURCHASE_HOLD_STATUSES` and
    `TERMINAL_PURCHASE_STATUSES`. Purchase statuses: requested, pending_approval, approved, issuing, issue_failed,
    card_issued, awaiting_checkout, authorized, settled, human_task, rejected, expired, cancelled.
11. **Stub behaviour:** every read/write function raises `NotAvailable(milestone="M15")`; the scheduler hooks
    (`card_reconcile.sync_company/run_all/check_balances`, `subscriptions.check`, `purchasing.expire_stale`,
    `wallet.freeze_all_cards`) are no-ops returning zero. Nothing calls them, so no existing path changes.
12. **`fake`** lives in `app/integrations/cards/fake.py`. "Registered only when `settings.environment == "test"`" is
    enforced by WP1's `wallet.provider_for`; step 0 only provides the class.


### 4.13b WP1 as built (2026-10-27) and further deviations

`services/purchasing.py`, `services/wallet.py`, `api/routes/wallet.py`, the three agent routes, the policy
fields (`/policy`, with the ceilings), `purchase_policy.updated` audit line, finance `purchases` block,
`/wallet` command, `agent_cards` checklist step, freeze on emergency stop / company pause, retire hook.
Tests: `test_m15_{purchasing,policy,api}.py`, helpers in `tests/m15_wp1_helpers.py`. Deviations:

1. **`rules.purchase_band`** and `Facts.purchasing`: the purchase bands apply only when the purchasing
   service resolves `purchase.make` / `subscription.start`. The same actions arriving as provider actions keep
   the `auto_spend_limit_micros` default (tested by `test_guardrails_engine`). Rules can only tighten.
2. **Approval hook:** `approvals.decide` calls `purchasing.apply_decision` (inside its transaction) and, after the
   commit, `purchasing.issue_after_decision`; `approvals.consume_internal` spends the approval without a
   principal and without a commit, in the transaction that records `card.issue_started`.
3. **No account, or purchasing off, is `report_only`** (a human task, status `human_task`, terminal). A manual
   account (or a vanished account) gives `awaiting_checkout` with no card and `checkout_path=human_buys`.
4. **Mark paid is for purchases without a card only**; a card-issued purchase is settled by reconciliation
   (marking it paid as well would count the charge twice). It writes a `human` capture
   (`provider_txn_id=human-<purchase id>`), a `verified` expense with `source=human`, and calls
   `subscriptions.create_from_settled` for a recurring purchase.
5. **Reveal step-up:** there is no password re-entry endpoint. The route needs the owner token or a browser
   session created within 300 s, else `403 reauth_required` (details `max_age_seconds`); a personal API token
   always gets 403. The UI sends the person through sign-in again.
6. **Extra endpoints:** `GET /wallet/accounts`, `PATCH /wallet/agents/{agent_id}/purchase-limit` (human-only,
   `purchase_policy.update`), `GET /purchases?checkout_task_id=` (task to purchase). `Agent` out gains
   `purchase_monthly_limit_micros`. Approve / reject / mark-paid / cancel return `PurchaseRequestDetail`.
7. **Issuing is synchronous after the commit** (agent request in the auto band, or the approval decision),
   and retried by `purchasing.expire_stale` (the scheduler hook): transient failures back off 1 / 5 / 15 / 60 /
   120 minutes (max 5 attempts), permanent ones raise the critical alert `card_issue_failed`. Retry state is in
   `checkout_packet["_issue"]`, the outcome note in `checkout_packet["_note"]` (ignored by the schema).
8. **Ack for agents:** `POST/GET /api/agent/v1/purchases` return `PurchaseAck` only. `schemas/agent_api.py` did
   not need additions.

### 4.14 Verification and what may be claimed afterwards (rule 8)

**Fully claimable after M15 (offline):** the fake provider, manual mode, and the policy, approval, reconciliation,
subscription, DLP and UI flows.

**Stripe Issuing and Mercury** are "implemented against the documented API, tested with fixtures, **not exercised
against the real system**", unless the owner runs:
- `foundryos wallet check` with a **Stripe sandbox** key (instant), issuing and closing a test card and simulating a
  capture with Stripe's test helpers;
- or a Mercury token with a $1 card.

Record the outcome in the roadmap status notes. No live money moves in M15 verification.

### 4.15 Risks and open questions

- **Stripe Atlas Issuing condition** (US representative address) may exclude an India-resident sole founder. Mercury is
  the fallback. Neither is verified for this owner.
- Mercury: transactions-endpoint field mapping and token IP allowlisting are unverified. Single-use and merchant lock
  are emulated or absent.
- **3DS:** Stripe Issuing sends challenges to the cardholder phone. With one shared cardholder that is the owner's
  phone, which is fine for tier B.
- **Human throughput:** if checkout tasks pile up, autonomy stalls. Mitigations:
  - the M12 watchdog treats `awaiting_checkout` older than 48 h as a human-escalation stall;
  - the weekly digest lists them.
- **RBI PPI draft** (cross-border ban on prepaid) and the 1 Oct 2026 cross-border authentication rule could change how
  manual Indian cards behave. The guide text must be dated and easy to update.
- **The migration chain** depends on whether 0013 has landed.
- **The `external_expenses.cost_kind` backfill** is labelled `verified` for existing human-recorded rows. Confirm the
  owner agrees.

---

## 5. Owner decisions (resolved 2026-10-27)

The owner accepted the plan's recommended defaults. They are the contract for every work package.

| # | Decision | Resolved to |
|---|---|---|
| 1 | Entity | Not fixed: the setup wizard asks (India company, US company, not incorporated, other) and recommends from the answer |
| 2 | Providers | `manual` + `stripe_issuing` + `mercury` + `fake` (tests only). Which real adapter is verified live first is left to the owner |
| 3 | Approval bands | **All bands start at 0**, so every request goes to the human. Code ceilings on what an AI may approve: $100 per charge with no approver, $500 per agent and month auto, $1,000 per charge for the Co-founder. Recommended values once the owner opts in: the Co-founder approves up to **$50 per charge and $200 per month**; **new subscriptions and new vendors always go to the human** |
| 4 | Who does checkout | The owner by default (`purchase_checkout_member_id` NULL); another human member may be chosen |
| 5 | Card reveal | A link to the provider dashboard only. The PAN is never stored, fetched or shown by us. Stripe Issuing Elements is M16 |
| 6 | Budgets | Purchases have a **separate purchase budget** (`purchase_monthly_budget_micros`), not the model budgets |
| 7 | Human-confirmed amounts (manual mode) | Recorded `verified`, source `human`, labelled "confirmed by you" |
| 8 | Crypto / x402 | Out. Revisit only for a US entity |
| 9 | Dashboard layout | A Wallet tile ("Agent cards") **next to Spend** on the canvas (row 3: Approvals, Spend, Wallet) plus the `?panel=wallet` panel |
| 10 | Code ceilings on AI bands | Accepted as proposed ($100 / $500 / $1,000, see 3) |

## 6. Existing code this builds on (read for this plan)

- `backend/app/guardrails/registry.py:164-165`: `purchase.make` and `subscription.start` (SPEND, `kind="provider"`);
  `domain.purchase` with `human_required` (line ~189); `model.paid_fallback` internal SPEND.
- `backend/app/guardrails/rules.py:85-87`: SPEND default = human above `auto_spend_limit_micros`, else Co-founder.
  `engine.py:64` maps SPEND → approval kind `external_purchase`.
- `backend/app/integrations/executors.py:342-385`: the precedent for a price ceiling bound to an approval (registrar
  purchase).
- `backend/app/integrations/catalog.py`: ProviderSpec vault. `services/connected_accounts.py`: `secrets_for`,
  `client_for`, `require_human`.
- `backend/app/models/ledger.py`: reservations and usage records (model spend only).
  `models/guardrails.py:102`: `ExternalExpense` (no cost_kind today).
- `backend/app/services/finance.py:128-220`: budgets are computed from `usage_records` only;
  `external_expenses_micros` is reported separately.
- `backend/app/models/governance.py`: Approval already has `amount_micros`, `vendor`, `recurrence`, `scope_hash`,
  `consumed_at`.
- `backend/app/services/secrets_scan.py`: no PAN detector, and the `sk-` pattern misses Stripe `sk_live_` keys
  (**existing gap**).
- `backend/app/web.py:31`: SPA CSP `script-src 'self'`.
- `web/src/features/canvas/CanvasPage.tsx`: the widget grid. `PanelHost.tsx`: the panel switch.

---

## 7. Note on budgets (why purchases are separate)

- Agent budgets today bound **model** spend (`max_agent_monthly_budget_micros` $25 cap, $5 hire default; D18).
- If purchases counted there, a $20 tool would exhaust a researcher's model budget and block its runs.
- Separate purchase totals (per agent, Co-founder band, company cap) keep both enforceable.
- Finance shows both, labelled.

---

## 8. Sources

- [S1] Stripe, Issuing for agents: https://docs.stripe.com/issuing/agents
- [S2] Stripe, Issuing for your business (Standard program limits, funding, sandbox): https://docs.stripe.com/issuing/for-your-business
- [S3] Stripe support, How to apply for Issuing (Atlas conditions): https://support.stripe.com/questions/how-to-apply-for-issuing
- [S4] Stripe, Pay with an SPT from an Issuing card: https://docs.stripe.com/issuing/agents/programmatic-checkout
- [S5] Mercury changelog, Cards API: https://docs.mercury.com/changelog/cards-api-now-available
- [S6] Mercury changelog, Vault API agent card credentials: https://docs.mercury.com/changelog/vault-api-retrieve-agent-card-credentials
- [S7] Mercury support, Agent cards: https://support.mercury.com/hc/en-us/articles/51299754284948/
- [S8] Fast Company on Mercury agent cards: https://www.fastcompany.com/91574324/mercury-launches-virtual-credit-cards-ai-agents ; https://letsdatascience.com/news/mercury-launches-virtual-cards-for-ai-agents-41cb565f
- [S9] Ramp eligibility: https://support.ramp.com/hc/en-us/articles/360043063133-Applying-and-signing-up-for-Ramp
- [S10] Ramp, Virtual cards for AI agents: https://ramp.com/blog/virtual-cards-for-ai-agents
- [S11] Ramp agent payments docs: https://agents.ramp.com/docs/guides/agentic-payments ; https://agents.ramp.com/skills/ramp-agentic-purchase
- [S12] Brex Platform Agreement: https://www.brex.com/legal/platform-agreement
- [S13] Brex Cards API: https://developer.brex.com/openapi/team_api/cards.md ; changelog https://developer.brex.com/changelog
- [S14] Brex fees: https://www.brex.com/support/cost-of-brex-services
- [S15] Lithic create card: https://docs.lithic.com/reference/postcards
- [S16] Lithic embedded card UI: https://docs.lithic.com/docs/embedded-card-ui
- [S17] Lithic agentic: https://www.lithic.com/solutions/agentic ; https://www.lithic.com/blog/mcp-server-agentic-payments
- [S18] Privacy.com eligibility: https://support.privacy.com/hc/en-us/articles/360012046934-Who-can-use-Privacy
- [S19] Privacy.com pricing: https://privacy.com/pricing
- [S20] Privacy.com agent API post: https://www.privacy.com/blog/privacy-api-agentic-workflow
- [S21] Airwallex Issuing regions: https://www.airwallex.com/docs/issuing/supported-regions-and-currencies
- [S22] Airwallex secure iframes: https://www.airwallex.com/docs/issuing__retrieve-sensitive-card-details__secure-iframes
- [S23] Airwallex pricing: https://www.airwallex.com/us/pricing ; India via Singapore (competitor source): https://www.skydo.com/blog/airwallex-india-availability-fees
- [S24] Wise business card countries: https://wise.com/help/articles/2935775/can-my-business-get-a-wise-card
- [S25] Wise card API: https://docs.wise.com/api-docs/api-reference/card
- [S26] Revolut US non-resident eligibility: https://help.revolut.com/en-US/help/setting-up-an-account/is-my-business-eligible/can-i-open-a-revolut-business-account-in-the-us-if-i-reside-in-a-different-country/business/
- [S27] Revolut create card / sensitive details: https://developer.revolut.com/docs/business/create-card ; https://developer.revolut.com/docs/business/get-sensitive-card-details
- [S28] Crossmint agentic cards launch: https://crossmint.com/announcement/agentic-cards-api-launch-visa-basistheory
- [S29] Crossmint how agents pay: https://docs.crossmint.com/agents/how-agents-pay
- [S30] Crossmint agent commerce toolkit: https://crossmint.com/announcement/agent-commerce-toolkit-launch ; Shopify robots note (secondary): https://stellagent.ai/insights/ec-ai-news-digest-2026-10-09
- [S31] Skyfire terms: https://skyfire.xyz/terms
- [S32] Skyfire KYA terms: https://skyfire.xyz/kya-terms-of-service/
- [S33] Skyfire wallet funding: https://docs.skyfire.xyz/docs/wallet-funding
- [S34] Payman: https://paymanai.com
- [S35] Payman ICBA accelerator: https://venturebeat.com/business/payman-ai-selected-for-icba-thinktech-accelerator
- [S36] Visa, Intelligent Commerce Connect (Apr 2026): https://investor.visa.com/news/news-details/2026/Visa-Opens-the-Door-to-AI-Driven-Shopping-for-Businesses-Worldwide/default.aspx
- [S37] Visa Trusted Agent Protocol: https://developer.visa.com/capabilities/trusted-agent-protocol
- [S38] Visa at GFF 2026: https://www.uniindia.com/business-wire-india/visa-makes-key-announcements-at-global-fintech-fest-2026-101686/175907
- [S39] Mastercard Agent Pay LatAm: https://www.mastercard.com/news/latin-america/en/newsroom/press-releases/pr-en/2026/march/mastercard-advances-agentic-payments-in-latin-america-and-the-caribbean-with-live-transactions-completed-across-the-region/
- [S40] Mastercard Agent Pay Australia: https://www.mastercard.com/news/ap/en/newsroom/press-releases/en/2026/mastercard-accelerates-ai-powered-commerce-with-australia-s-first-authenticated-agentic-transactions-using-agent-pay/
- [S41] Mastercard Agent Pay developer docs: https://developer.mastercard.com/mastercard-agent-pay/documentation/tutorial-and-guides/
- [S42] x402 how it works: https://docs.cdp.coinbase.com/x402/how-it-works
- [S43] x402 facilitator pricing: https://docs.cdp.coinbase.com/x402/seller/facilitator
- [S44] CDP server wallets pricing: https://docs.cdp.coinbase.com/server-wallets/v2/introduction/pricing
- [S45] Glenbrook on x402 volume: https://glenbrook.com/payments_news/the-x402-foundation-activated-a-27-year-old-internet-code-200-million-transactions-later-the-real-volume-is-still-tiny/
- [S46] AP2: https://ap2-protocol.org/
- [S47] Stripe Shared Payment Tokens: https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens
- [S48] OpenAI checkout shift (Mar 2026): https://digitalcommerce360.com/2026/03/06/openai-shifts-checkout-plans-agentic-commerce-strategy/
- [S49] Nium RBI in-principle approval: https://www.nium.com/newsroom/nium-recieved-principle-approval-from-reserve-bank
- [S50] Nium cards: https://docs.nium.com/apis/docs/card-overview
- [S51] EnKash card API: https://docs.enkash.com/doc-863061
- [S52] Volopay India virtual cards: https://volopay.com/in/virtual-cards
- [S53] Razorpay FTX25 corporate cards: https://razorpay.com/newsroom/razorpay-unveils-industry-first-innovations-at-ftx25-corporate-cards-with-yes-bank-agentic-ai-powered-payments-suite-and-indias-first-buyer-protection-program/
- [S54] RBI Authentication Directions press release: https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=61282
- [S55] Analysis of the RBI authentication directions: https://www.mondaq.com/india/new-technology/1728730/understanding-rbi-authentication-mechanisms-for-digital-payment-transactions-directions-2025-obligations-for-banks-nbfcs-and-payment-providers
- [S56] E-mandate framework 2026: https://www.medianama.com/2026/04/223-rbi-additional-factor-authentication-e-mandates/
- [S57] ₹1 lakh e-mandate tier: https://www.business-standard.com/amp/economy/interviews/rbi-raises-limit-of-e-mandates-for-recurring-online-transactions-to-1-lakh-123120801110_1.html
- [S58] RBI 2020 card controls: https://www.medianama.com/2020/01/223-rbi-new-guidelines-card-transactions/
- [S59] RBI tokenisation: https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=52188
- [S60] RBI draft PPI norms (Apr 2026): https://vinodkothari.com/2026/04/rbis-draft-ppi-norms-stricter-cash-rules-simplified-categories-no-cross-border-payments-and-more/
- [S61] FEM (Current Account Transactions) Rules / LRS: https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=5084
- [S62] Purpose code S0807: https://www.xflowpay.com/rbi-purpose-codes/s0807-off-site-software-imports
- [S63] TCS on LRS 2026 (secondary): https://taxguru.in/?p=1040449
- [S64] Credit-card spend not under LRS (2023): https://cleartax.in/s/20-tcs-on-international-credit-card-spends
- [S65] TDS on VDAs s.393(1): https://blog.tdsman.com/2026/05/tds-on-virtual-digital-assets-section-3931-194s/
- [S66] FIU-IND notices (Sep 2026): https://newsonair.gov.in/fiu-ind-cracks-down-on-15-vda-service-providers-over-pmla-non-compliance/
- [S67] RBI on stablecoins: https://paymentexpert.com/2026/07/08/india-rbi-wants-ban-crypto-stablecoin/
- [S68] Razorpay/NPCI/OpenAI UPI agentic pilot: https://www.businesstoday.in/amp/tech-today/news/story/razorpay-npci-openai-launch-pilot-for-agentic-payments-where-ai-powered-shopping-meets-upi-497545-2025-10-09
- Also: ChatGPT Instant Checkout scale-back (secondary) https://www.ringly.io/blog/openai-instant-checkout-agentic-commerce-protocol ; Stripe Sessions 2026 https://stripe.com/blog/everything-we-announced-at-sessions-2026 ; Link for agents https://link.com/agents ; Mastercard + Skyfire trust services https://investor.mastercard.com/investor-news/investor-news-details/2026/Mastercard-Advances-Agentic-Commerce-With-New-Trust-and-Intelligence-Services/default.aspx

**Verification level:**
- The planner read these directly: [S1], [S2], [S5], plus a search summary of [S3] and [S7]/[S8].
- The rest come from two research passes that fetched official pages where possible. Several pages returned 403 (Mercury
  and Revolut eligibility, Visa developer, the Income Tax site). Claims resting on those are marked unverified above.
- Indian tax and FEMA points are not legal advice; the guides tell the owner to confirm with a CA or bank.
