import { describe, expect, it } from 'vitest'
import { makeAlert } from '@/test/factories'
import { ALERT_STRIP_LIMIT, alertCountText, alertPanel, sortAlerts, stripModel } from './alerts'

const open = (n: number, over = {}) => Array.from({ length: n }, (_, i) => makeAlert({ id: `a${i}`, created_at: `2026-10-24T09:0${i}:00Z`, ...over }))

describe('stripModel', () => {
  it('is hidden without open alerts (resolved ones do not count)', () => {
    expect(stripModel(undefined, false).mode).toBe('hidden')
    expect(stripModel([], false).mode).toBe('hidden')
    expect(stripModel([makeAlert({ status: 'resolved' })], false).mode).toBe('hidden')
  })

  it('lists alerts up to the limit', () => {
    const m = stripModel(open(ALERT_STRIP_LIMIT), false)
    expect(m.mode).toBe('list')
    expect(m.items).toHaveLength(ALERT_STRIP_LIMIT)
  })

  it('collapses to a count beyond the limit, and lists everything once expanded', () => {
    const alerts = open(ALERT_STRIP_LIMIT + 2)
    const collapsed = stripModel(alerts, false)
    expect(collapsed.mode).toBe('collapsed')
    expect(collapsed.items).toHaveLength(0)
    expect(collapsed.count).toBe(ALERT_STRIP_LIMIT + 2)
    const expanded = stripModel(alerts, true)
    expect(expanded.mode).toBe('expanded')
    expect(expanded.items).toHaveLength(ALERT_STRIP_LIMIT + 2)
  })

  it('orders critical first and reports the worst severity', () => {
    const alerts = [makeAlert({ id: 'i', severity: 'info' }), makeAlert({ id: 'c', severity: 'critical' }), makeAlert({ id: 'w', severity: 'warning' })]
    expect(sortAlerts(alerts).map((a) => a.id)).toEqual(['c', 'w', 'i'])
    const m = stripModel(alerts, false)
    expect(m.mode === 'list' && m.worst).toBe('critical')
    expect(stripModel([makeAlert({ severity: 'info' })], false)).toMatchObject({ worst: 'info' })
  })

  it('counts in words', () => {
    expect(alertCountText([makeAlert()])).toBe('1 open alert')
    expect(alertCountText([makeAlert(), makeAlert({ severity: 'critical' })])).toBe('2 open alerts, 1 critical')
  })
})

describe('alertPanel', () => {
  it('links to the related record when there is a view for it', () => {
    expect(alertPanel({ related_type: 'task', related_id: 't1' })).toEqual({ kind: 'task', id: 't1' })
    expect(alertPanel({ related_type: 'approval', related_id: 'x' })).toEqual({ kind: 'approvals' })
    expect(alertPanel({ related_type: null, related_id: null })).toBeNull()
    expect(alertPanel({ related_type: 'mystery', related_id: 'x' })).toBeNull()
  })
})
