import { describe, expect, it } from 'vitest'
import {
  DEFAULT_THRESHOLDS,
  addDays,
  budgetRows,
  enumerateDays,
  formatDay,
  formatRangeLabel,
  isCurrentPeriod,
  isFuturePeriod,
  isIsoDate,
  isWastedStatus,
  niceMax,
  normalizeBreakdown,
  normalizeSummary,
  normalizeThresholds,
  normalizeTrend,
  periodRange,
  shiftAnchor,
  todayInZone,
  trendDayTotal,
  utilization,
  utilizationText,
} from './finance'
import { formatCost } from './money'

describe('period ranges in the company timezone', () => {
  it('"today" follows the company clock, not UTC', () => {
    // 02:30 UTC on the 24th is still the evening of the 23rd in Los Angeles (UTC-7 in October)...
    const instant = new Date('2026-10-24T02:30:00Z')
    expect(todayInZone(instant, 'UTC')).toBe('2026-10-24')
    expect(todayInZone(instant, 'America/Los_Angeles')).toBe('2026-10-23')
    // ...and already the 24th in Auckland (UTC+13 with daylight saving) at 20:00 UTC on the 23rd.
    const late = new Date('2026-10-23T20:00:00Z')
    expect(todayInZone(late, 'UTC')).toBe('2026-10-23')
    expect(todayInZone(late, 'Pacific/Auckland')).toBe('2026-10-24')
  })

  it('builds day, week (Monday to Sunday) and month ranges from the company-local anchor', () => {
    const la = todayInZone(new Date('2026-10-24T02:30:00Z'), 'America/Los_Angeles') // Friday 23 Oct
    expect(periodRange('day', la)).toEqual({ from: '2026-10-23', to: '2026-10-23' })
    expect(periodRange('week', la)).toEqual({ from: '2026-10-19', to: '2026-10-25' })
    expect(periodRange('month', la)).toEqual({ from: '2026-10-01', to: '2026-10-31' })
    // The same instant in UTC lands on Saturday 24 Oct: same week, but a different day.
    expect(periodRange('day', todayInZone(new Date('2026-10-24T02:30:00Z'), 'UTC'))).toEqual({ from: '2026-10-24', to: '2026-10-24' })
  })

  it('handles week edges: a Monday starts its own week, a Sunday ends it', () => {
    expect(periodRange('week', '2026-10-19')).toEqual({ from: '2026-10-19', to: '2026-10-25' })
    expect(periodRange('week', '2026-10-25')).toEqual({ from: '2026-10-19', to: '2026-10-25' })
    expect(periodRange('week', '2026-10-26')).toEqual({ from: '2026-10-26', to: '2026-11-01' })
  })

  it('handles month lengths, leap years and year ends', () => {
    expect(periodRange('month', '2026-02-10')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(periodRange('month', '2028-02-10')).toEqual({ from: '2028-02-01', to: '2028-02-29' })
    expect(periodRange('month', '2026-12-31')).toEqual({ from: '2026-12-01', to: '2026-12-31' })
    expect(periodRange('week', '2026-12-31')).toEqual({ from: '2026-12-28', to: '2027-01-03' })
  })

  it('steps to the neighbouring period', () => {
    expect(shiftAnchor('day', '2026-03-01', -1)).toBe('2026-02-28')
    expect(shiftAnchor('week', '2026-10-23', -1)).toBe('2026-10-16')
    expect(shiftAnchor('week', '2026-10-23', 1)).toBe('2026-10-30')
    expect(shiftAnchor('month', '2026-01-15', -1)).toBe('2025-12-01')
    expect(shiftAnchor('month', '2026-12-15', 1)).toBe('2027-01-01')
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
  })

  it('knows whether a period contains today or lies in the future', () => {
    const range = periodRange('week', '2026-10-23')
    expect(isCurrentPeriod(range, '2026-10-23')).toBe(true)
    expect(isCurrentPeriod(range, '2026-10-26')).toBe(false)
    expect(isFuturePeriod(periodRange('week', '2026-10-30'), '2026-10-23')).toBe(true)
    expect(isFuturePeriod(range, '2026-10-23')).toBe(false)
  })

  it('echoes the range in words without shifting days', () => {
    expect(formatRangeLabel({ from: '2026-10-23', to: '2026-10-23' })).toBe('Fri 23 Oct 2026')
    expect(formatRangeLabel({ from: '2026-10-19', to: '2026-10-25' })).toBe('Mon 19 Oct – Sun 25 Oct 2026')
    expect(formatRangeLabel({ from: '2026-12-28', to: '2027-01-03' })).toBe('Mon 28 Dec 2026 – Sun 3 Jan 2027')
    expect(formatDay('2026-10-23')).toBe('Fri 23 Oct')
    expect(formatRangeLabel({ from: '2026-10-01', to: '2026-10-31' })).toBe('October 2026')
  })

  it('validates real calendar dates', () => {
    expect(isIsoDate('2026-02-28')).toBe(true)
    expect(isIsoDate('2026-02-30')).toBe(false)
    expect(isIsoDate('2026-13-01')).toBe(false)
    expect(isIsoDate('26-02-28')).toBe(false)
    expect(isIsoDate('')).toBe(false)
  })
})

describe('utilization and thresholds', () => {
  it('splits the bar into verified and estimated and never exceeds 100%', () => {
    const u = utilization(30_000_000, 20_000_000, 100_000_000)
    expect(u.usedPct).toBe(50)
    expect(u.verifiedBarPct).toBe(30)
    expect(u.estimatedBarPct).toBe(20)
    expect(u.reached).toEqual([50])
    expect(u.level).toBe('ok')
    const over = utilization(90_000_000, 40_000_000, 100_000_000)
    expect(over.usedPct).toBe(130)
    expect(over.verifiedBarPct).toBe(90)
    expect(over.estimatedBarPct).toBe(10)
    expect(over.verifiedBarPct + over.estimatedBarPct).toBeLessThanOrEqual(100)
    expect(over.level).toBe('exceeded')
    expect(over.reached).toEqual([50, 80, 100])
  })

  it('crosses a threshold exactly at its value', () => {
    expect(utilization(49_999_999, 0, 100_000_000).reached).toEqual([])
    expect(utilization(50_000_000, 0, 100_000_000).reached).toEqual([50])
    expect(utilization(80_000_000, 0, 100_000_000)).toMatchObject({ reached: [50, 80], level: 'warning' })
    expect(utilization(100_000_000, 0, 100_000_000)).toMatchObject({ reached: [50, 80, 100], level: 'exceeded' })
  })

  it('counts estimates toward the threshold state but keeps them out of the solid segment', () => {
    const u = utilization(10_000_000, 45_000_000, 100_000_000)
    expect(u.verifiedBarPct).toBe(10)
    expect(u.estimatedBarPct).toBe(45)
    expect(u.reached).toEqual([50]) // 10% verified alone would reach nothing
    expect(utilization(10_000_000, 40_000_000, 100_000_000).reached).toEqual([50])
    expect(utilization(10_000_000, 39_000_000, 100_000_000).reached).toEqual([])
  })

  it('has no state without a limit', () => {
    for (const lim of [null, undefined, 0, -5]) {
      expect(utilization(5, 5, lim)).toMatchObject({ limit: null, usedPct: null, reached: [], level: 'ok' })
    }
    expect(utilizationText(utilization(5, 0, null))).toBe('No limit')
  })

  it('uses custom thresholds and says when over budget', () => {
    expect(utilization(60_000_000, 0, 100_000_000, [25, 75, 120]).reached).toEqual([25])
    expect(utilizationText(utilization(62_000_000, 0, 100_000_000))).toBe('62% used')
    expect(utilizationText(utilization(112_000_000, 0, 100_000_000))).toBe('112% used, over budget')
  })

  it('normalises thresholds from the policy', () => {
    expect(DEFAULT_THRESHOLDS).toEqual([50, 80, 100])
    expect(normalizeThresholds([100, 50, 80, 80])).toEqual([50, 80, 100])
    expect(normalizeThresholds([0, -1, 'x', NaN, 5000])).toBeNull()
    expect(normalizeThresholds(undefined)).toBeNull()
    expect(normalizeThresholds([])).toBeNull()
  })

  it('lists only budgets with a limit: company first, agents fullest first', () => {
    const rows = budgetRows([
      { scope: 'agent', agent_id: 'a', period: 'month', limit_micros: 100, verified_micros: 10, estimated_micros: 0 },
      { scope: 'agent', agent_id: 'b', period: 'month', limit_micros: 100, verified_micros: 90, estimated_micros: 0 },
      { scope: 'company', agent_id: null, period: 'month', limit_micros: 1000, verified_micros: 1, estimated_micros: 0 },
      { scope: 'company', agent_id: null, period: 'day', limit_micros: 100, verified_micros: 1, estimated_micros: 0 },
      { scope: 'agent', agent_id: 'c', period: 'day', limit_micros: null, verified_micros: 5, estimated_micros: 0 },
    ])
    expect(rows.map((r) => `${r.scope}:${r.agentId ?? '-'}:${r.period}`)).toEqual(['company:-:day', 'company:-:month', 'agent:b:month', 'agent:a:month'])
  })
})

describe('cost kind rendering', () => {
  it('only a verified amount reads as a charge; an estimate always says est.', () => {
    expect(formatCost(1_250_000, 'verified')).toBe('$1.25')
    expect(formatCost(1_250_000, 'estimated')).toBe('≈ $1.25 est.')
    expect(formatCost(1_250_000, 'unknown')).toBe('Unpriced')
    expect(formatCost(0, 'unknown')).toBe('Unpriced')
  })
  it('classifies wasted runs', () => {
    for (const s of ['failed', 'cancelled', 'timed_out'] as const) expect(isWastedStatus(s)).toBe(true)
    for (const s of ['succeeded', 'running', 'queued', 'starting'] as const) expect(isWastedStatus(s)).toBe(false)
  })
})

describe('trend normalisation', () => {
  it('fills days the server did not report with zeros, in order', () => {
    const days = normalizeTrend({ points: [{ date: '2026-10-03', verified_micros: 5, estimated_micros: 2, unknown_micros: 0 }] }, '2026-10-01', '2026-10-05')
    expect(days.map((d) => d.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05'])
    expect(days.map(trendDayTotal)).toEqual([0, 0, 7, 0, 0])
    expect(days[2]).toEqual({ date: '2026-10-03', verified: 5, estimated: 2, unknown: 0 })
  })
  it('sums duplicate days, drops points outside the range and clamps junk to zero', () => {
    const days = normalizeTrend(
      [
        { date: '2026-10-01', verified_micros: 3, estimated_micros: 0, unknown_micros: 0 },
        { date: '2026-10-01', verified_micros: 4, estimated_micros: 1, unknown_micros: 0 },
        { date: '2026-09-30', verified_micros: 99, estimated_micros: 0, unknown_micros: 0 },
        { date: '2026-10-02', verified_micros: -5, estimated_micros: 'x', unknown_micros: null },
        { date: '2026-10-02T00:00:00Z', verified_micros: 2, estimated_micros: 0, unknown_micros: 0 },
        null,
      ],
      '2026-10-01',
      '2026-10-02',
    )
    expect(days).toEqual([
      { date: '2026-10-01', verified: 7, estimated: 1, unknown: 0 },
      { date: '2026-10-02', verified: 2, estimated: 0, unknown: 0 },
    ])
  })
  it('returns nothing for a bad range and bounds a huge one', () => {
    expect(normalizeTrend({}, '2026-10-05', '2026-10-01')).toEqual([])
    expect(normalizeTrend({}, 'x', 'y')).toEqual([])
    expect(enumerateDays('2020-01-01', '2026-12-31').length).toBeLessThanOrEqual(400)
  })
  it('rounds the axis maximum up to a clean value', () => {
    expect(niceMax(0)).toBe(1)
    expect(niceMax(1_200_000)).toBe(2_000_000)
    expect(niceMax(2_000_000)).toBe(2_000_000)
    expect(niceMax(2_300_000)).toBe(2_500_000)
    expect(niceMax(4_100_000)).toBe(5_000_000)
    expect(niceMax(7_000_000)).toBe(10_000_000)
  })
})

describe('response normalisers', () => {
  it('reads a finance summary and never invents a missing number', () => {
    const s = normalizeSummary({ from: '2026-10-01', to: '2026-10-31', timezone: 'UTC', totals: { verified_micros: 5 }, budgets: [{ scope: 'company', period: 'month', limit_micros: 10, verified_micros: 2, estimated_micros: 1 }, { scope: 'bogus' }] })!
    expect(s.totals).toMatchObject({ verified_micros: 5, estimated_micros: 0, unknown_micros: 0, runs: 0 })
    expect(s.wasted).toMatchObject({ verified_micros: 0, runs: 0 })
    expect(s.external_expenses_micros).toBe(0)
    expect(s.budgets).toHaveLength(1)
    expect(normalizeSummary('x')).toBeNull()
  })
  it('reads breakdown rows, biggest spender first', () => {
    const rows = normalizeBreakdown({
      items: [
        { key: 'a', verified_micros: 1, estimated_micros: 0 },
        { key: 'b', verified_micros: 1, estimated_micros: 5, label: 'Bee' },
        { verified_micros: 100 },
      ],
    })
    expect(rows.map((r) => r.key)).toEqual(['b', 'a'])
    expect(rows[0]!.label).toBe('Bee')
  })
})
