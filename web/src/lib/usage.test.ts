import { describe, expect, it } from 'vitest'
import { budgetFraction, normalizeUsage, sumRows } from './usage'

describe('normalizeUsage', () => {
  it('reads a by_cost_kind map and keeps provenance separate', () => {
    const r = normalizeUsage({
      items: [{ key: 'a1', input_tokens: 10, output_tokens: 5, by_cost_kind: { verified: 1_000_000, estimated: 400_000 } }],
    })
    expect(r.rows).toHaveLength(1)
    expect(r.rows[0]!.by_kind).toEqual({ verified: 1_000_000, estimated: 400_000, unknown: 0 })
    expect(r.totals.total_micros).toBe(1_400_000)
    expect(r.totals.input_tokens).toBe(10)
  })

  it('reads *_micros columns', () => {
    const r = normalizeUsage({ items: [{ day: '2026-10-23', verified_micros: 5, estimated_micros: 7, unknown_micros: 0 }] })
    expect(r.rows[0]!.key).toBe('2026-10-23')
    expect(r.rows[0]!.by_kind.estimated).toBe(7)
  })

  it('merges one-row-per-kind responses by key', () => {
    const r = normalizeUsage({
      items: [
        { agent_id: 'a', cost_kind: 'verified', cost_micros: 100 },
        { agent_id: 'a', cost_kind: 'estimated', cost_micros: 40 },
        { agent_id: 'b', cost_kind: 'estimated', cost_micros: 9 },
      ],
    })
    expect(r.rows.find((x) => x.key === 'a')!.by_kind).toEqual({ verified: 100, estimated: 40, unknown: 0 })
    expect(r.rows).toHaveLength(2)
    expect(r.totals.by_kind.estimated).toBe(49)
  })

  it('never upgrades a cost with no stated kind to verified', () => {
    const r = normalizeUsage({ items: [{ key: 'm', cost_micros: 500 }] })
    expect(r.rows[0]!.by_kind).toEqual({ verified: 0, estimated: 0, unknown: 500 })
  })

  it('tolerates empty, malformed and array payloads', () => {
    expect(normalizeUsage(null).rows).toEqual([])
    expect(normalizeUsage({}).totals.total_micros).toBe(0)
    expect(normalizeUsage({ items: [null, 5, 'x'] }).rows).toEqual([])
    expect(normalizeUsage([{ key: 'k', by_cost_kind: { verified: 3 } }]).totals.total_micros).toBe(3)
  })
})

describe('sumRows / budgetFraction', () => {
  it('sums rows', () => {
    const r = normalizeUsage({ items: [{ key: 'a', verified_micros: 2 }, { key: 'b', estimated_micros: 3 }] })
    const s = sumRows(r.rows)
    expect(s.by_kind.verified).toBe(2)
    expect(s.by_kind.estimated).toBe(3)
  })
  it('computes the budget fraction, null without a limit', () => {
    expect(budgetFraction(50, 200)).toBe(0.25)
    expect(budgetFraction(50, null)).toBeNull()
    expect(budgetFraction(50, 0)).toBeNull()
  })
})
