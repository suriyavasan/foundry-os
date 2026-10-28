import type { BudgetStatus, CostSplit, FinanceBreakdownRow, FinancePeriod, FinanceSummary, FinanceTrendPoint, RunStatus } from '@/api/types'
import { isoDateInZone } from './dates'

/* ============================ periods in the company timezone ============================ */

const DAY_MS = 86_400_000
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/

/** A real calendar date written YYYY-MM-DD (rejects 2026-02-30). */
export function isIsoDate(s: string): boolean {
  const m = ISO_RE.exec(s)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const t = new Date(Date.UTC(y, mo - 1, d))
  return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d
}

const toMs = (iso: string): number => {
  const m = ISO_RE.exec(iso)!
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}
const fromMs = (ms: number): string => new Date(ms).toISOString().slice(0, 10)

/** Calendar arithmetic on date-only strings: no timezone, no DST, so no off-by-one days. */
export function addDays(iso: string, days: number): string {
  return fromMs(toMs(iso) + days * DAY_MS)
}

export interface DateRange {
  /** First day, inclusive. */
  from: string
  /** Last day, inclusive. */
  to: string
}

/** The company-local calendar day an instant falls on ("today" for a company in Los Angeles is not the UTC date). */
export function todayInZone(now: Date, timeZone: string): string {
  return isoDateInZone(now, timeZone)
}

/**
 * The calendar range of a period around an anchor day. Weeks run Monday to Sunday. Days are company-local
 * calendar days, which is what the finance endpoints take as `from` / `to` (inclusive).
 */
export function periodRange(period: FinancePeriod, anchor: string): DateRange {
  if (period === 'day') return { from: anchor, to: anchor }
  if (period === 'week') {
    const dow = (new Date(toMs(anchor)).getUTCDay() + 6) % 7 // 0 = Monday
    const from = addDays(anchor, -dow)
    return { from, to: addDays(from, 6) }
  }
  const [y, m] = [Number(anchor.slice(0, 4)), Number(anchor.slice(5, 7))]
  return { from: `${anchor.slice(0, 7)}-01`, to: fromMs(Date.UTC(y, m, 0)) }
}

/** Step the anchor to the previous or next period. A month steps to the first of the neighbouring month. */
export function shiftAnchor(period: FinancePeriod, anchor: string, direction: -1 | 1): string {
  if (period === 'day') return addDays(anchor, direction)
  if (period === 'week') return addDays(anchor, 7 * direction)
  const y = Number(anchor.slice(0, 4))
  const m = Number(anchor.slice(5, 7)) - 1 + direction
  return fromMs(Date.UTC(y, m, 1))
}

/** Is `today` inside the range (so the period is still running)? */
export const isCurrentPeriod = (range: DateRange, today: string): boolean => range.from <= today && today <= range.to
/** The "next" button is disabled once the range already contains today (or lies in the future). */
export const isFuturePeriod = (range: DateRange, today: string): boolean => range.from > today

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

// Formatted by hand from the calendar date, not through Intl and a timezone, so a date can never shift by a day.
const parts = (iso: string) => {
  const d = new Date(toMs(iso))
  return { wd: WEEKDAYS[d.getUTCDay()]!, day: d.getUTCDate(), mon: MONTHS[d.getUTCMonth()]!, monLong: MONTHS_LONG[d.getUTCMonth()]!, year: d.getUTCFullYear() }
}

/** "Fri 23 Oct" */
export function formatDay(iso: string): string {
  if (!isIsoDate(iso)) return iso
  const p = parts(iso)
  return `${p.wd} ${p.day} ${p.mon}`
}

/**
 * "Fri 23 Oct 2026", "Mon 19 Oct \u2013 Sun 25 Oct 2026", "October 2026". It describes the range itself (a single day, a whole
 * calendar month, or anything else), so it stays right when the server echoes a range other than the one asked for.
 */
export function formatRangeLabel(range: DateRange): string {
  if (!isIsoDate(range.from) || !isIsoDate(range.to)) return `${range.from} \u2013 ${range.to}`
  const f = parts(range.from)
  const t = parts(range.to)
  if (range.from === range.to) return `${f.wd} ${f.day} ${f.mon} ${f.year}`
  const month = periodRange('month', range.from)
  if (range.to === month.to && range.from === month.from) return `${f.monLong} ${f.year}`
  return `${f.wd} ${f.day} ${f.mon}${f.year === t.year ? '' : ` ${f.year}`} \u2013 ${t.wd} ${t.day} ${t.mon} ${t.year}`
}

/* ============================ budgets, thresholds, utilization ============================ */

export const DEFAULT_THRESHOLDS = [50, 80, 100]

/** Valid, sorted, unique percentages in (0, 1000]; null when absent or unusable (the UI then shows no markers). */
export function normalizeThresholds(raw: unknown): number[] | null {
  if (!Array.isArray(raw)) return null
  const vals = raw.filter((v): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 1000)
  if (vals.length === 0) return null
  return [...new Set(vals)].sort((a, b) => a - b)
}

export type BudgetLevel = 'ok' | 'warning' | 'exceeded'

export interface Utilization {
  limit: number | null
  verified: number
  estimated: number
  /** (verified + estimated) / limit in percent, uncapped. null without a limit. */
  usedPct: number | null
  /** Bar segment widths in percent; together never more than 100. */
  verifiedBarPct: number
  estimatedBarPct: number
  /** Thresholds that spend (verified plus estimated) has reached. */
  reached: number[]
  level: BudgetLevel
}

/**
 * How much of a budget is used. Verified spend is the solid segment; an estimate is a separate hatched one and is
 * never folded into "charged". The threshold state counts both, because budget enforcement cannot wait for a bill.
 */
export function utilization(verified: number, estimated: number, limit: number | null | undefined, thresholds: number[] = DEFAULT_THRESHOLDS): Utilization {
  const lim = typeof limit === 'number' && limit > 0 ? limit : null
  if (lim === null) return { limit: null, verified, estimated, usedPct: null, verifiedBarPct: 0, estimatedBarPct: 0, reached: [], level: 'ok' }
  const usedPct = ((verified + estimated) / lim) * 100
  const verifiedBarPct = Math.min(100, (verified / lim) * 100)
  const estimatedBarPct = Math.max(0, Math.min(100 - verifiedBarPct, (estimated / lim) * 100))
  return {
    limit: lim,
    verified,
    estimated,
    usedPct,
    verifiedBarPct,
    estimatedBarPct,
    reached: thresholds.filter((t) => usedPct >= t),
    level: usedPct >= 100 ? 'exceeded' : usedPct >= 80 ? 'warning' : 'ok',
  }
}

/** "62% used" / "over budget by 12%"; whole percents. */
export function utilizationText(u: Utilization): string {
  if (u.usedPct === null) return 'No limit'
  const pct = Math.round(u.usedPct)
  return u.usedPct > 100 ? `${pct}% used, over budget` : `${pct}% used`
}

export interface BudgetRow {
  scope: BudgetStatus['scope']
  agentId: string | null
  period: BudgetStatus['period']
  util: Utilization
}

/** Budgets that have a limit, company rows first, then agents by how full they are. */
export function budgetRows(budgets: BudgetStatus[], thresholds: number[] = DEFAULT_THRESHOLDS): BudgetRow[] {
  const rows = budgets
    .filter((b) => typeof b.limit_micros === 'number' && b.limit_micros > 0)
    .map((b) => ({ scope: b.scope, agentId: b.agent_id, period: b.period, util: utilization(b.verified_micros, b.estimated_micros, b.limit_micros, thresholds) }))
  return rows.sort((a, b) => {
    if (a.scope !== b.scope) return a.scope === 'company' ? -1 : 1
    if (a.scope === 'company') return a.period === b.period ? 0 : a.period === 'day' ? -1 : 1
    return (b.util.usedPct ?? 0) - (a.util.usedPct ?? 0)
  })
}

export interface AgentBudget {
  agentId: string
  /** `util.limit` is null when the agent has no limit for that period. */
  day: Utilization
  month: Utilization
}

/**
 * One entry per agent with its daily and monthly budget side by side, limit or not (an unbudgeted agent still shows
 * what it spends). Fullest budget first; agents without any limit last, by spend.
 */
export function agentBudgets(budgets: BudgetStatus[], thresholds: number[] = DEFAULT_THRESHOLDS): AgentBudget[] {
  const byAgent = new Map<string, AgentBudget>()
  for (const b of budgets) {
    if (b.scope !== 'agent' || !b.agent_id) continue
    const entry = byAgent.get(b.agent_id) ?? { agentId: b.agent_id, day: utilization(0, 0, null, thresholds), month: utilization(0, 0, null, thresholds) }
    entry[b.period] = utilization(b.verified_micros, b.estimated_micros, b.limit_micros, thresholds)
    byAgent.set(b.agent_id, entry)
  }
  const fullest = (a: AgentBudget) => Math.max(a.day.usedPct ?? -1, a.month.usedPct ?? -1)
  const spent = (a: AgentBudget) => a.month.verified + a.month.estimated
  return [...byAgent.values()].sort((a, b) => fullest(b) - fullest(a) || spent(b) - spent(a))
}

/* ================================== cost provenance ================================== */

export const EMPTY_SPLIT: CostSplit = { verified_micros: 0, estimated_micros: 0, unknown_micros: 0 }

export const isWastedStatus = (s: RunStatus): boolean => s === 'failed' || s === 'cancelled' || s === 'timed_out'

/** Sum of the amounts that can honestly be called money out: verified only. */
export const chargedOf = (s: CostSplit): number => s.verified_micros

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0)
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const splitOf = (o: Record<string, unknown> | null): CostSplit => ({ verified_micros: num(o?.verified_micros), estimated_micros: num(o?.estimated_micros), unknown_micros: num(o?.unknown_micros) })

/** Defensive read of `GET /finance/summary`. Missing numbers are 0 (a missing figure is never invented). */
export function normalizeSummary(raw: unknown): FinanceSummary | null {
  const o = obj(raw)
  if (!o) return null
  const totals = obj(o.totals)
  const wasted = obj(o.wasted)
  const budgets: BudgetStatus[] = []
  for (const b of Array.isArray(o.budgets) ? o.budgets : []) {
    const r = obj(b)
    if (!r) continue
    const period = r.period === 'day' || r.period === 'month' ? r.period : null
    const scope = r.scope === 'company' || r.scope === 'agent' ? r.scope : null
    if (!period || !scope) continue
    budgets.push({
      scope,
      agent_id: typeof r.agent_id === 'string' ? r.agent_id : null,
      period,
      limit_micros: typeof r.limit_micros === 'number' ? r.limit_micros : null,
      verified_micros: num(r.verified_micros),
      estimated_micros: num(r.estimated_micros),
    })
  }
  return {
    from: typeof o.from === 'string' ? o.from : '',
    to: typeof o.to === 'string' ? o.to : '',
    timezone: typeof o.timezone === 'string' ? o.timezone : '',
    totals: { ...splitOf(totals), input_tokens: num(totals?.input_tokens), output_tokens: num(totals?.output_tokens), runs: num(totals?.runs) },
    wasted: { ...splitOf(wasted), runs: num(wasted?.runs) },
    external_expenses_micros: num(o.external_expenses_micros),
    budgets,
  }
}

export function normalizeBreakdown(raw: unknown): FinanceBreakdownRow[] {
  const o = obj(raw)
  const list = Array.isArray(o?.items) ? o.items : Array.isArray(raw) ? raw : []
  const rows: FinanceBreakdownRow[] = []
  for (const item of list) {
    const r = obj(item)
    if (!r || typeof r.key !== 'string') continue
    rows.push({
      key: r.key,
      label: typeof r.label === 'string' && r.label !== '' ? r.label : null,
      ...splitOf(r),
      input_tokens: num(r.input_tokens),
      output_tokens: num(r.output_tokens),
      runs: num(r.runs),
    })
  }
  return rows.sort((a, b) => b.verified_micros + b.estimated_micros - (a.verified_micros + a.estimated_micros))
}

/* ====================================== trend ====================================== */

export interface TrendDay {
  date: string
  verified: number
  estimated: number
  unknown: number
}

const MAX_TREND_DAYS = 400

/** Every day from `from` to `to`, inclusive (bounded). */
export function enumerateDays(from: string, to: string): string[] {
  if (!isIsoDate(from) || !isIsoDate(to) || from > to) return []
  const out: string[] = []
  for (let d = from; d <= to && out.length < MAX_TREND_DAYS; d = addDays(d, 1)) out.push(d)
  return out
}

/**
 * One entry per day of the range, in order, with days the server did not report filled with zeros (so a quiet day is a
 * visible gap and the axis stays honest). Points outside the range are dropped, duplicates for a day are summed, and
 * negative or non-numeric amounts count as 0.
 */
export function normalizeTrend(raw: unknown, from: string, to: string): TrendDay[] {
  const o = obj(raw)
  const list = Array.isArray(o?.points) ? o.points : Array.isArray(o?.items) ? o.items : Array.isArray(raw) ? raw : []
  const days = new Map<string, TrendDay>(enumerateDays(from, to).map((d) => [d, { date: d, verified: 0, estimated: 0, unknown: 0 }]))
  for (const p of list as FinanceTrendPoint[]) {
    const r = obj(p)
    const day = typeof r?.date === 'string' ? r.date.slice(0, 10) : ''
    const slot = days.get(day)
    if (!slot || !r) continue
    slot.verified += num(r.verified_micros)
    slot.estimated += num(r.estimated_micros)
    slot.unknown += num(r.unknown_micros)
  }
  return [...days.values()]
}

/** Round a maximum up to 1, 2, 2.5, 5 or 10 times a power of ten, so axis ticks read cleanly. 0 gives a 1-unit axis. */
export function niceMax(max: number): number {
  if (!(max > 0)) return 1
  const pow = 10 ** Math.floor(Math.log10(max))
  for (const step of [1, 2, 2.5, 5, 10]) if (max <= step * pow) return step * pow
  return 10 * pow
}

export const trendDayTotal = (d: TrendDay): number => d.verified + d.estimated + d.unknown
