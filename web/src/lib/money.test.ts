import { describe, expect, it } from 'vitest'
import { COST_KIND_LABEL, chargedMicros, formatCost, formatUsd, microsToUsdString, parseBudgetInput, usdToMicros } from './money'

describe('usdToMicros', () => {
  it.each([
    ['0', 0],
    ['1', 1_000_000],
    ['12.5', 12_500_000],
    ['12.50', 12_500_000],
    ['19.99', 19_990_000],
    ['0.1', 100_000],
    ['0.000001', 1],
    ['1,234.56', 1_234_560_000],
    ['$25', 25_000_000],
    [' 7 ', 7_000_000],
  ])('parses %s', (input, expected) => {
    expect(usdToMicros(input)).toBe(expected)
  })

  it('has no float drift for typical values', () => {
    // 0.1 + 0.2 style traps and values that misround when multiplied as floats
    for (const s of ['0.07', '1.005', '4.35', '8.2', '19.99', '33.33', '1.1', '2.675']) {
      const [w, f = ''] = s.split('.')
      expect(usdToMicros(s)).toBe(Number(w) * 1_000_000 + Number(f.padEnd(6, '0')))
    }
    expect(usdToMicros(0.1 + 0.2)).toBe(300_000)
    expect(usdToMicros(4.35)).toBe(4_350_000)
  })

  it.each(['', 'abc', '-1', '1.2345678', '1e3', '1.', '.5', '1..2'])('rejects %j', (input) => {
    expect(usdToMicros(input)).toBeNull()
  })
})

describe('microsToUsdString', () => {
  it('round-trips with usdToMicros', () => {
    for (const micros of [0, 1, 123, 10_000, 500_000, 12_500_000, 19_990_000, 1_234_567]) {
      expect(usdToMicros(microsToUsdString(micros))).toBe(micros)
    }
  })
  it('formats with at least 2 decimals and handles null', () => {
    expect(microsToUsdString(12_500_000)).toBe('12.50')
    expect(microsToUsdString(5_000_000)).toBe('5.00')
    expect(microsToUsdString(123)).toBe('0.000123')
    expect(microsToUsdString(null)).toBe('')
  })
})

describe('formatUsd', () => {
  it('formats currency', () => {
    expect(formatUsd(12_500_000)).toBe('$12.50')
    expect(formatUsd(0)).toBe('$0.00')
    expect(formatUsd(1_234_560_000)).toBe('$1,234.56')
  })
  it('keeps sub-cent precision instead of rounding away real cost', () => {
    expect(formatUsd(123)).toBe('$0.000123')
  })
  it('renders a placeholder for null', () => {
    expect(formatUsd(null)).toBe('—')
    expect(formatUsd(undefined, 'no limit')).toBe('no limit')
  })
})

describe('parseBudgetInput', () => {
  it('treats blank as no limit', () => {
    expect(parseBudgetInput('  ')).toEqual({ value: null })
  })
  it('parses valid amounts and flags invalid ones', () => {
    expect(parseBudgetInput('25.5')).toEqual({ value: 25_500_000 })
    expect(parseBudgetInput('abc').error).toBeTruthy()
  })
})

describe('formatCost (cost_kind provenance)', () => {
  it('shows a verified cost as a plain charge', () => {
    expect(formatCost(1_250_000, 'verified')).toBe('$1.25')
  })
  it('never presents an estimate as a charge', () => {
    const s = formatCost(1_250_000, 'estimated')
    expect(s).toBe('≈ $1.25 est.')
    expect(s).not.toBe(formatCost(1_250_000, 'verified'))
  })
  it('shows no number for an unknown cost', () => {
    expect(formatCost(0, 'unknown')).toBe('Unpriced')
    expect(formatCost(5_000_000, 'unknown')).toBe('Unpriced')
  })
  it('handles a missing amount', () => {
    expect(formatCost(null, 'verified')).toBe('—')
  })
  it('labels each kind distinctly', () => {
    expect(new Set(Object.values(COST_KIND_LABEL)).size).toBe(3)
    expect(COST_KIND_LABEL.estimated).toBe('Estimated')
  })
  it('counts only verified spend as charged', () => {
    expect(chargedMicros({ verified: 2_000_000, estimated: 9_000_000, unknown: 1_000_000 })).toBe(2_000_000)
  })
})
