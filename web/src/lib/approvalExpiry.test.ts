import { describe, expect, it } from 'vitest'
import { EXPIRED_LABEL, expiryText, formatSpan } from './approvalExpiry'

const NOW = Date.parse('2026-10-30T12:00:00Z')
const at = (ms: number) => new Date(NOW + ms).toISOString()
const H = 3_600_000
const M = 60_000

describe('expiryText', () => {
  it('tells a human approval it declines automatically', () => {
    expect(expiryText({ status: 'pending', approver: 'human', expires_at: at(5 * H) }, NOW)).toBe('Declines automatically in 5 h')
    expect(expiryText({ status: 'pending', approver: 'human', expires_at: at(40 * M) }, NOW)).toBe('Declines automatically in 40 min')
  })
  it('tells a Co-founder approval it goes to the person', () => {
    expect(expiryText({ status: 'pending', approver: 'founder', expires_at: at(20 * M) }, NOW)).toBe("Goes to you in 20 min if the Co-founder doesn't decide")
  })
  it('says so when the deadline has passed but the sweep has not run yet', () => {
    expect(expiryText({ status: 'pending', approver: 'human', expires_at: at(-M) }, NOW)).toMatch(/any moment/)
    expect(expiryText({ status: 'pending', approver: 'founder', expires_at: at(-M) }, NOW)).toMatch(/Goes to you now/)
  })
  it('is silent without a deadline, for decided approvals and for peer reviews', () => {
    expect(expiryText({ status: 'pending', approver: 'human', expires_at: null }, NOW)).toBeNull()
    expect(expiryText({ status: 'approved', approver: 'human', expires_at: at(H) }, NOW)).toBeNull()
    expect(expiryText({ status: 'pending', approver: 'peer', expires_at: at(H) }, NOW)).toBeNull()
  })
  it('names expiry as a decline, never as an approval', () => {
    expect(EXPIRED_LABEL).toBe('Expired · declined by timeout')
  })
})

describe('formatSpan', () => {
  it('uses minutes, hours then days', () => {
    expect(formatSpan(10_000)).toBe('under a minute')
    expect(formatSpan(90 * M)).toBe('2 h')
    expect(formatSpan(3 * 24 * H)).toBe('3 days')
  })
})
