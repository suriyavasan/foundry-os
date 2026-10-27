import { describe, expect, it } from 'vitest'
import { catalog, fakeProvider, manualProvider, mercuryProvider, stripeProvider } from './walletFixtures'
import {
  budgetUse,
  canCancelPurchase,
  canDecidePurchase,
  containsCardNumber,
  isSafeRevealUrl,
  manualAccountBody,
  needsCheckout,
  NO_CARD_NUMBER_MESSAGE,
  recommendProvider,
  transactionProvenance,
  validateManualCard,
} from './walletLogic'

describe('recommendProvider', () => {
  it.each([
    ['india_company', false, 'manual'],
    ['not_incorporated', false, 'manual'],
    ['other', false, 'manual'],
    ['us_company', false, 'stripe_issuing'],
    ['us_company', true, 'mercury'],
  ] as const)('%s (already at Mercury: %s) -> %s', (kind, haveMercury, expected) => {
    expect(recommendProvider(kind, catalog.items, { haveMercury })?.provider.id).toBe(expected)
  })

  it('never recommends the test provider, and offers the others as alternatives', () => {
    const r = recommendProvider('us_company', catalog.items)!
    expect(r.alternatives.map((p) => p.id)).toEqual(['manual', 'mercury'])
    expect(r.alternatives.some((p) => p.id === fakeProvider.id)).toBe(false)
  })

  it('falls back to the own card when a US-only provider is not available, and says why', () => {
    const r = recommendProvider('us_company', [manualProvider, { ...stripeProvider, available: false }, { ...mercuryProvider, available: false }])!
    expect(r.provider.id).toBe('manual')
    expect(r.reason).toMatch(/own card/i)
  })

  it('is honest that an India company has no programmable card', () => {
    expect(recommendProvider('india_company', catalog.items)!.reason).toMatch(/no provider issues a programmable card/i)
  })

  it('returns null when there is nothing to recommend', () => {
    expect(recommendProvider('india_company', [])).toBeNull()
    expect(recommendProvider('india_company', [fakeProvider])).toBeNull()
  })
})

describe('card number guard', () => {
  it.each(['4242424242424242', '4242 4242 4242 4242', '4242-4242-4242-4242', '1234567890123', '1234567890123456789', 'card 5555555555554444 here'])('treats %s as a card number', (t) => {
    expect(containsCardNumber(t)).toBe(true)
  })
  it.each(['1234', '12345678901', '123456789012', 'HDFC corporate card', 'order 2026-10-27', ''])('lets %s through', (t) => {
    expect(containsCardNumber(t)).toBe(false)
  })

  it('refuses a 13 to 19 digit number in any field, and a long number in last 4', () => {
    for (const n of ['1234567890123', '4111111111111111', '1234567890123456789']) {
      expect(validateManualCard({ label: 'Card', brand: 'Visa', last4: n }).last4).toBe(NO_CARD_NUMBER_MESSAGE)
      expect(validateManualCard({ label: n, brand: 'Visa', last4: '1234' }).label).toBe(NO_CARD_NUMBER_MESSAGE)
      expect(validateManualCard({ label: 'Card', brand: n, last4: '1234' }).brand).toBe(NO_CARD_NUMBER_MESSAGE)
    }
  })

  it('accepts exactly four digits and nothing else', () => {
    expect(validateManualCard({ label: 'HDFC', brand: 'Visa', last4: '1234' })).toEqual({})
    expect(validateManualCard({ label: 'HDFC', brand: 'Visa', last4: '123' }).last4).toBe('Exactly 4 digits')
    expect(validateManualCard({ label: 'HDFC', brand: 'Visa', last4: '12345' }).last4).toBe('Exactly 4 digits')
    expect(validateManualCard({ label: 'HDFC', brand: 'Visa', last4: '12a4' }).last4).toBe('Exactly 4 digits')
    expect(validateManualCard({ label: ' ', brand: '', last4: '1234' })).toMatchObject({ label: expect.any(String), brand: expect.any(String) })
  })

  it('builds a body with label, brand and last four only', () => {
    expect(manualAccountBody({ label: ' HDFC ', brand: 'Visa', last4: '1234' })).toEqual({ provider: 'manual', label: 'HDFC', mode: 'live', manual_brand: 'Visa', manual_last4: '1234', make_default: true })
  })
})

describe('purchase lifecycle helpers', () => {
  it('only a pending approval can be decided; only an open purchase can be cancelled', () => {
    expect(canDecidePurchase('pending_approval')).toBe(true)
    expect(canDecidePurchase('awaiting_checkout')).toBe(false)
    expect(canCancelPurchase('awaiting_checkout')).toBe(true)
    expect(canCancelPurchase('settled')).toBe(false)
    expect(canCancelPurchase('cancelled')).toBe(false)
    expect(needsCheckout('awaiting_checkout')).toBe(true)
    expect(needsCheckout('settled')).toBe(false)
  })
})

describe('provenance and budget', () => {
  it('labels who reported a transaction', () => {
    expect(transactionProvenance({ cost_kind: 'verified', source: 'provider' })).toBe('verified · provider')
    expect(transactionProvenance({ cost_kind: 'verified', source: 'human' })).toBe('verified · confirmed by you')
    expect(transactionProvenance({ cost_kind: 'estimated', source: 'provider' })).toBe('estimated · provider')
  })
  it('splits settled from committed against the budget, and has nothing without a budget', () => {
    expect(budgetUse({ settled_verified_micros: 50_000_000, held_micros: 25_000_000, budget_micros: 100_000_000 })).toEqual({ settled: 50, held: 25 })
    expect(budgetUse({ settled_verified_micros: 1, held_micros: 1, budget_micros: null })).toBeNull()
  })
  it('only opens a plain https reveal link', () => {
    expect(isSafeRevealUrl('https://dashboard.stripe.com/issuing/cards/ic_1')).toBe(true)
    expect(isSafeRevealUrl('http://dashboard.stripe.com/x')).toBe(false)
    expect(isSafeRevealUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeRevealUrl('https://u:p@evil.test/')).toBe(false)
  })
})
