/** Money helpers. Canonical unit is integer micro-USD (1 USD = 1,000,000 micros). Never use float math on money. */

export const MICROS_PER_USD = 1_000_000

const USD_RE = /^(\d+)(?:\.(\d{1,6}))?$/

/**
 * Parse a user-entered USD string (e.g. "12.5", "$1,000.25") to integer micros using string math,
 * so there is no floating point drift. Returns null when the input is not a valid non-negative
 * amount with at most 6 decimal places.
 */
export function usdToMicros(input: string | number): number | null {
  const raw = typeof input === 'number' ? (Number.isFinite(input) && input >= 0 && input < 1e9 ? input.toFixed(6) : '') : input
  const cleaned = raw.trim().replace(/^\$/, '').replace(/,/g, '')
  if (/e/i.test(cleaned)) return null
  const m = USD_RE.exec(cleaned)
  if (!m) return null
  const whole = Number(m[1])
  const frac = Number((m[2] ?? '').padEnd(6, '0'))
  const micros = whole * MICROS_PER_USD + frac
  return Number.isSafeInteger(micros) ? micros : null
}

/** Exact decimal string for micros, trimmed to at least 2 decimals ("12.50", "0.000123"). For inputs. */
export function microsToUsdString(micros: number | null | undefined): string {
  if (micros === null || micros === undefined) return ''
  const neg = micros < 0
  const abs = Math.abs(Math.trunc(micros))
  const whole = Math.floor(abs / MICROS_PER_USD)
  let frac = String(abs % MICROS_PER_USD).padStart(6, '0').replace(/0+$/, '')
  if (frac.length < 2) frac = frac.padEnd(2, '0')
  return `${neg ? '-' : ''}${whole}.${frac}`
}

const usdFormatters = new Map<number, Intl.NumberFormat>()

/** Display format: "$12.50"; sub-cent amounts keep precision up to 6 decimals. */
export function formatUsd(micros: number | null | undefined, empty = '—'): string {
  if (micros === null || micros === undefined) return empty
  const needsPrecision = micros % 10_000 !== 0
  const maxDigits = needsPrecision ? 6 : 2
  let fmt = usdFormatters.get(maxDigits)
  if (!fmt) {
    fmt = new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDigits,
    })
    usdFormatters.set(maxDigits, fmt)
  }
  return fmt.format(micros / MICROS_PER_USD)
}

export interface BudgetParse {
  /** micros, or null when the field is blank (no limit). */
  value: number | null
  error?: string
}

/** Parse an optional budget form field. Blank = no limit. */
export function parseBudgetInput(input: string): BudgetParse {
  if (input.trim() === '') return { value: null }
  const micros = usdToMicros(input)
  if (micros === null) return { value: null, error: 'Enter a USD amount like 25 or 25.50 (up to 6 decimals)' }
  return { value: micros }
}

/* ------------------------------ cost provenance ------------------------------ */

export type CostKindLike = 'verified' | 'estimated' | 'unknown'

export const COST_KIND_LABEL: Record<CostKindLike, string> = {
  verified: 'Verified',
  estimated: 'Estimated',
  unknown: 'Unpriced',
}

export const COST_KIND_HELP: Record<CostKindLike, string> = {
  verified: 'Reported by the provider or runtime. This is an actual charge.',
  estimated: 'Computed from token counts and list prices. Not a charge; the real bill may differ.',
  unknown: 'The runtime did not report a cost. Nothing is claimed.',
}

/**
 * Display a cost with its provenance. Only `verified` reads as a charge; an estimate always carries
 * "≈" and "est."; an unknown cost never shows a number (it would read as $0).
 */
export function formatCost(micros: number | null | undefined, kind: CostKindLike): string {
  if (kind === 'unknown') return 'Unpriced'
  if (micros === null || micros === undefined) return '—'
  return kind === 'estimated' ? `≈ ${formatUsd(micros)} est.` : formatUsd(micros)
}

/** Total that may be called "spent": verified costs only. */
export function chargedMicros(byKind: Record<CostKindLike, number>): number {
  return byKind.verified
}
