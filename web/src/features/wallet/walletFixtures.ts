import type { CardAccount, CardProviderInfo, CardProviderList, CheckoutPacketData, PurchaseRequest, WalletOverview } from '@/api/wallet'

const caps = {
  issue_cards: true,
  single_use: true,
  merchant_mcc_lock: true,
  per_auth_limit: true,
  interval_limits: ['per_authorization'],
  list_transactions: true,
  balance: true,
  webhooks: true,
  programmatic_checkout: false,
  test_mode: true,
}

export const manualProvider: CardProviderInfo = {
  id: 'manual',
  label: 'Your own card',
  capabilities: { ...caps, issue_cards: false, single_use: false, merchant_mcc_lock: false, per_auth_limit: false, balance: false, webhooks: false, test_mode: false, list_transactions: false },
  eligibility: 'Works anywhere. You pay; agents only prepare the purchase.',
  recommended_for: ['india_company', 'not_incorporated', 'other'],
  steps: [
    { title: 'Turn on online and international payments', body: 'Indian cards have this off by default.', owner_only: true },
    { title: 'Keep your OTP phone with you', body: 'Most online payments ask for an OTP.', owner_only: true },
    { title: 'Enter only the last 4 digits in Foundry', body: 'Never the full number.', owner_only: false },
  ],
  verification: 'Not exercised against a real bank: no card is ever contacted.',
  needs_connected_account: false,
  available: true,
}
export const stripeProvider: CardProviderInfo = {
  id: 'stripe_issuing',
  label: 'Stripe Issuing',
  capabilities: caps,
  eligibility: 'Needs a US company. Stripe Atlas works.',
  recommended_for: ['us_company'],
  steps: [
    { title: 'Apply for the Issuing Standard program', body: 'Dashboard, Issuing.', owner_only: true },
    { title: 'Fund the Issuing balance', body: 'ACH or wire.', owner_only: true },
  ],
  verification: 'Not verified against the real Stripe API.',
  needs_connected_account: true,
  available: true,
}
export const mercuryProvider: CardProviderInfo = {
  id: 'mercury',
  label: 'Mercury',
  capabilities: { ...caps, single_use: false, merchant_mcc_lock: false },
  eligibility: 'Needs a Mercury business account for a US entity.',
  recommended_for: ['us_company'],
  steps: [{ title: 'Create an API token', body: 'Settings, API tokens.', owner_only: true }],
  verification: 'Not verified against the real Mercury API.',
  needs_connected_account: true,
  available: true,
}
export const fakeProvider: CardProviderInfo = { ...manualProvider, id: 'fake', label: 'Test provider', recommended_for: [] }

export const catalog: CardProviderList = { items: [manualProvider, stripeProvider, mercuryProvider, fakeProvider], guide_updated_on: '2026-10-27' }

export const manualAccount: CardAccount = {
  id: 'ca1',
  company_id: 'c1',
  provider: 'manual',
  label: 'HDFC corporate card',
  status: 'active',
  mode: 'live',
  connected_account_id: null,
  external_ref: null,
  manual_brand: 'Visa',
  manual_last4: '1234',
  is_default: true,
  last_balance_micros: null,
  balance_checked_at: null,
  last_synced_at: null,
  last_error: null,
  created_at: '2026-10-27T09:00:00Z',
  updated_at: '2026-10-27T09:00:00Z',
}

export const emptyWallet: WalletOverview = {
  available: true,
  enabled: false,
  accounts: [],
  balance: null,
  month: { period: '2026-10', settled_verified_micros: 0, held_micros: 0, budget_micros: null },
  awaiting_checkout: [],
  pending_approvals: 0,
  renewals_due: [],
}

export const activeWallet: WalletOverview = {
  ...emptyWallet,
  enabled: true,
  accounts: [manualAccount],
  balance: { available_micros: null, as_of: null, source: 'manual' },
  month: { period: '2026-10', settled_verified_micros: 24_500_000, held_micros: 60_000_000, budget_micros: 200_000_000 },
  awaiting_checkout: [{ purchase_request_id: 'p1', item: 'Analytics Pro plan', vendor: 'Plausible', vendor_domain: 'plausible.io', amount_micros: 9_000_000, checkout_task_id: 't9', expires_at: null }],
  pending_approvals: 2,
  renewals_due: [{ subscription_id: 's1', vendor: 'Plausible', amount_micros: 9_000_000, next_renewal_on: '2026-11-02' }],
}

export const packet: CheckoutPacketData = {
  vendor: 'Plausible',
  vendor_domain: 'plausible.io',
  url: 'https://plausible.io/pricing',
  item: 'Analytics Pro plan',
  quantity: 1,
  amount_micros: 9_000_000,
  card_limit_micros: 10_000_000,
  recurrence: 'monthly',
  company_email: 'ops@acme.example',
  bring_back: 'Paste the API key into Integrations, scoped to analytics.read',
  card_last4: '4242',
  card_brand: 'visa',
  card_exp_month: 12,
  card_exp_year: 2027,
  steps: [],
}

export function makePurchase(over: Partial<PurchaseRequest> = {}): PurchaseRequest {
  return {
    id: 'p1',
    company_id: 'c1',
    agent_id: 'a2',
    agent_name: 'Iris',
    run_id: null,
    task_id: 't1',
    project_id: null,
    item: 'Analytics Pro plan',
    vendor: 'Plausible',
    vendor_domain: 'plausible.io',
    url: 'https://plausible.io/pricing',
    quantity: 1,
    amount_micros: 9_000_000,
    original_amount_minor: null,
    original_currency: null,
    recurrence: 'monthly',
    justification: 'We need privacy-friendly analytics to measure the launch; the free tier has no API.',
    alternatives: null,
    status: 'awaiting_checkout',
    decision: 'human',
    checkout_path: 'human_checkout',
    approval_id: 'ap1',
    card_id: 'card1',
    subscription_id: null,
    checkout_task_id: 't9',
    held_micros: 9_000_000,
    settled_micros: null,
    cost_kind: null,
    checkout_packet: packet,
    expires_at: null,
    created_at: '2026-10-27T09:00:00Z',
    updated_at: '2026-10-27T09:30:00Z',
    ...over,
  }
}
