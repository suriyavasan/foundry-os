import type {
  CardAccount,
  CardAccountCreate,
  CardProviderId,
  CardProviderInfo,
  CardStatus,
  CardTransaction,
  CompanyKind,
  PurchaseStatus,
  Recurrence,
  SubscriptionStatus,
} from '@/api/wallet'
import type { Tone } from '@/components/ui/Badge'
import { titleCase } from '@/lib/format'

/* ------------------------------- company kind ------------------------------- */

export const COMPANY_KINDS: Array<{ value: CompanyKind; label: string; hint: string }> = [
  { value: 'india_company', label: 'An India company', hint: 'Private limited, LLP or similar, registered in India.' },
  { value: 'us_company', label: 'A US company', hint: 'An LLC or C-corp with an EIN, for example through Stripe Atlas.' },
  { value: 'not_incorporated', label: 'Not incorporated yet', hint: 'You are buying as an individual for now.' },
  { value: 'other', label: 'Somewhere else', hint: 'Any other country.' },
]

/** Providers worth showing in the wizard: the test fake is never offered to a person. */
export const selectableProviders = (items: CardProviderInfo[]): CardProviderInfo[] => items.filter((p) => p.id !== 'fake')

export interface Recommendation {
  provider: CardProviderInfo
  /** Honest reason, composed from the answers. The provider's own `eligibility` text is shown next to it. */
  reason: string
  alternatives: CardProviderInfo[]
}

/**
 * Which provider to recommend for where the company is incorporated. India, not incorporated and other countries get the
 * owner's own card (no provider gives them a programmable card); a US company gets Mercury when it already banks there,
 * otherwise Stripe Issuing (best controls), with the other one as the alternative. Falls back to the first usable provider
 * in the catalog the server sent, so a missing provider never produces a dead end.
 */
export function recommendProvider(kind: CompanyKind, catalog: CardProviderInfo[], opts: { haveMercury?: boolean } = {}): Recommendation | null {
  const usable = selectableProviders(catalog).filter((p) => p.available)
  const by = (id: CardProviderId) => usable.find((p) => p.id === id)
  const order: CardProviderId[] =
    kind === 'us_company' ? (opts.haveMercury ? ['mercury', 'stripe_issuing', 'manual'] : ['stripe_issuing', 'mercury', 'manual']) : ['manual']
  const picked = order.map(by).find((p): p is CardProviderInfo => !!p) ?? usable[0]
  if (!picked) return null
  const reason = REASONS[kind]?.[picked.id] ?? 'This is the provider that fits your answers best.'
  return { provider: picked, reason, alternatives: usable.filter((p) => p.id !== picked.id) }
}

const REASONS: Record<CompanyKind, Partial<Record<CardProviderId, string>>> = {
  india_company: {
    manual:
      'No provider issues a programmable card to an India-incorporated company. Use your bank corporate credit card: agents prepare each purchase, you pay at checkout (Indian cards ask for an OTP, which keeps you in the loop anyway).',
  },
  us_company: {
    stripe_issuing: 'Stripe Issuing has the best controls: a single-use card per purchase with an exact limit and a merchant lock.',
    mercury: 'You bank with Mercury, so cards are issued from the account you already fund. Mercury has no single-use cards: the limit and our closing do the enforcement.',
    manual: 'Neither provider is available here, so use your own card.',
  },
  not_incorporated: {
    manual: 'A programmable card needs a company. Until you incorporate, use a personal international credit card: agents prepare the purchase, you pay.',
  },
  other: {
    manual: 'Programmable cards are only available to US entities today. Use your own card for now.',
  },
}

/* ------------------------------ card number guard ------------------------------ */

/**
 * True when the text contains a run of 13 to 19 digits (spaces and dashes allowed between them). Deliberately stricter than
 * the server's Luhn check: the form refuses anything shaped like a full card number, valid or not.
 */
export function containsCardNumber(text: string): boolean {
  const re = /(?:\d[ -]?){13,19}/g
  for (const m of text.matchAll(re)) {
    const digits = m[0].replace(/\D/g, '')
    if (digits.length >= 13 && digits.length <= 19) return true
  }
  return false
}

export const NO_CARD_NUMBER_MESSAGE = 'A full card number is never accepted. Enter only the last 4 digits.'

export interface ManualCardForm {
  label: string
  brand: string
  last4: string
}
export type ManualCardErrors = Partial<Record<keyof ManualCardForm, string>>

export const CARD_BRANDS = ['Visa', 'Mastercard', 'American Express', 'RuPay', 'Other'] as const

export function validateManualCard(f: ManualCardForm): ManualCardErrors {
  const e: ManualCardErrors = {}
  const label = f.label.trim()
  if (!label) e.label = 'Give the card a name you will recognise, e.g. "HDFC corporate card"'
  else if (containsCardNumber(label)) e.label = NO_CARD_NUMBER_MESSAGE
  else if (label.length > 120) e.label = 'At most 120 characters'
  const brand = f.brand.trim()
  if (!brand) e.brand = 'Choose the card network'
  else if (containsCardNumber(brand)) e.brand = NO_CARD_NUMBER_MESSAGE
  const last4 = f.last4.trim()
  if (containsCardNumber(last4)) e.last4 = NO_CARD_NUMBER_MESSAGE
  else if (!/^\d{4}$/.test(last4)) e.last4 = 'Exactly 4 digits'
  return e
}

/** The create body for the owner's own card: label, brand and last four digits, nothing else. */
export function manualAccountBody(f: ManualCardForm): CardAccountCreate {
  return { provider: 'manual', label: f.label.trim(), mode: 'live', manual_brand: f.brand.trim(), manual_last4: f.last4.trim(), make_default: true }
}

/* ---------------------------------- labels ---------------------------------- */

export const PROVIDER_LABEL: Record<CardProviderId, string> = {
  manual: 'Your own card',
  stripe_issuing: 'Stripe Issuing',
  mercury: 'Mercury',
  fake: 'Test provider',
}

export const PURCHASE_STATUS: Record<PurchaseStatus, { label: string; tone: Tone }> = {
  requested: { label: 'Requested', tone: 'neutral' },
  pending_approval: { label: 'Waiting for approval', tone: 'warning' },
  approved: { label: 'Approved', tone: 'info' },
  issuing: { label: 'Issuing card', tone: 'info' },
  issue_failed: { label: 'Card failed', tone: 'danger' },
  card_issued: { label: 'Card ready', tone: 'info' },
  awaiting_checkout: { label: 'Waiting for you to pay', tone: 'warning' },
  authorized: { label: 'Paid, settling', tone: 'success' },
  settled: { label: 'Paid', tone: 'success' },
  human_task: { label: 'Handed to you', tone: 'warning' },
  rejected: { label: 'Rejected', tone: 'danger' },
  expired: { label: 'Expired', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}

export const PURCHASE_FILTERS: Array<{ value: PurchaseStatus | 'all'; label: string }> = [
  { value: 'all', label: 'All purchases' },
  { value: 'pending_approval', label: 'Waiting for approval' },
  { value: 'awaiting_checkout', label: 'Waiting for you to pay' },
  { value: 'human_task', label: 'Handed to you' },
  { value: 'authorized', label: 'Paid, settling' },
  { value: 'settled', label: 'Paid' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'expired', label: 'Expired' },
  { value: 'cancelled', label: 'Cancelled' },
]

/** A purchase a person can still approve or reject. */
export const canDecidePurchase = (s: PurchaseStatus): boolean => s === 'pending_approval'
/** A purchase a person can still cancel (a card, if any, is closed and the hold is released). */
export const canCancelPurchase = (s: PurchaseStatus): boolean => ['requested', 'pending_approval', 'approved', 'issuing', 'issue_failed', 'card_issued', 'awaiting_checkout', 'human_task'].includes(s)
/** A purchase whose payment a person still has to make or confirm. */
export const needsCheckout = (s: PurchaseStatus): boolean => ['card_issued', 'awaiting_checkout', 'human_task'].includes(s)
export const isPaid = (s: PurchaseStatus): boolean => s === 'authorized' || s === 'settled'

export const CARD_STATUS: Record<CardStatus, { label: string; tone: Tone }> = {
  active: { label: 'Active', tone: 'success' },
  frozen: { label: 'Frozen', tone: 'warning' },
  closed: { label: 'Closed', tone: 'neutral' },
  expired: { label: 'Expired', tone: 'neutral' },
}

export const SUBSCRIPTION_STATUS: Record<SubscriptionStatus, { label: string; tone: Tone }> = {
  active: { label: 'Active', tone: 'success' },
  review_due: { label: 'Review due', tone: 'warning' },
  cancel_requested: { label: 'Cancel requested', tone: 'warning' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  lapsed: { label: 'Lapsed', tone: 'danger' },
}

export const ACCOUNT_STATUS: Record<CardAccount['status'], { label: string; tone: Tone }> = {
  setup: { label: 'Setting up', tone: 'info' },
  active: { label: 'Active', tone: 'success' },
  attention: { label: 'Needs attention', tone: 'danger' },
  disabled: { label: 'Disabled', tone: 'neutral' },
}

export function recurrenceSuffix(r: Recurrence | null | undefined): string {
  return r === 'monthly' ? ' / month' : r === 'yearly' ? ' / year' : ''
}

/** "visa ••1234" or "••1234". */
export function cardDescription(brand: string | null | undefined, last4: string | null | undefined): string {
  if (!last4) return 'No card yet'
  return `${brand ? `${titleCase(brand)} ` : ''}••${last4}`
}

export function expiry(month: number | null | undefined, year: number | null | undefined): string | null {
  if (!month || !year) return null
  return `${String(month).padStart(2, '0')}/${String(year).slice(-2)}`
}

/** "verified · provider" or "verified · confirmed by you". */
export function transactionProvenance(t: Pick<CardTransaction, 'cost_kind' | 'source'>): string {
  const kind = t.cost_kind === 'verified' ? 'verified' : t.cost_kind === 'estimated' ? 'estimated' : 'unpriced'
  return `${kind} · ${t.source === 'human' ? 'confirmed by you' : 'provider'}`
}

/** Fraction of the monthly purchase budget that is spent plus committed, or null when there is no budget. */
export function budgetUse(month: { settled_verified_micros: number; held_micros: number; budget_micros: number | null }): { settled: number; held: number } | null {
  if (!month.budget_micros || month.budget_micros <= 0) return null
  return {
    settled: Math.min(100, (month.settled_verified_micros / month.budget_micros) * 100),
    held: Math.min(100, (month.held_micros / month.budget_micros) * 100),
  }
}

/** The purchase's vendor URL, only ever as text. Never rendered as a link by this module. */
export const SAFE_REVEAL_PROTOCOL = 'https:'
export function isSafeRevealUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === SAFE_REVEAL_PROTOCOL && !u.username && !u.password
  } catch {
    return false
  }
}
