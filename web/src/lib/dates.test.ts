import { describe, expect, it } from 'vitest'
import { formatDuration, formatInterval, isSameMonth, isoDateInZone, monthStartIso } from './dates'

describe('isoDateInZone', () => {
  it('uses the company timezone, not UTC', () => {
    const d = new Date('2026-10-24T02:30:00Z')
    expect(isoDateInZone(d, 'UTC')).toBe('2026-10-24')
    expect(isoDateInZone(d, 'America/Los_Angeles')).toBe('2026-10-23')
  })
  it('falls back to UTC for an invalid zone', () => {
    expect(isoDateInZone(new Date('2026-10-24T02:30:00Z'), 'Not/AZone')).toBe('2026-10-24')
  })
})
describe('month helpers', () => {
  it('finds the month start and same-month days', () => {
    expect(monthStartIso('2026-10-23')).toBe('2026-10-01')
    expect(isSameMonth('2026-10-01', '2026-10-23')).toBe(true)
    expect(isSameMonth('2026-09-30', '2026-10-23')).toBe(false)
  })
})
describe('formatInterval / formatDuration', () => {
  it('formats intervals', () => {
    expect(formatInterval(900)).toBe('15 min')
    expect(formatInterval(3600)).toBe('1 h')
    expect(formatInterval(172800)).toBe('2 d')
    expect(formatInterval(90)).toBe('90 s')
  })
  it('formats durations', () => {
    expect(formatDuration(4500)).toBe('5s')
    expect(formatDuration(125_000)).toBe('2m 5s')
    expect(formatDuration(3_725_000)).toBe('1h 2m')
  })
})
