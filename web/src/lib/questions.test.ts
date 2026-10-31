import { describe, expect, it } from 'vitest'
import { oldestOpenQuestion } from './questions'

const q = (id: string, status: 'open' | 'answered' | 'defaulted' | 'cancelled', created_at: string) => ({ id, status, created_at })

describe('oldestOpenQuestion', () => {
  it('picks the open question that has waited longest, ignoring resolved ones', () => {
    const list = [q('new', 'open', '2026-10-30T10:00:00Z'), q('old-answered', 'answered', '2026-10-29T10:00:00Z'), q('old', 'open', '2026-10-30T08:00:00Z'), q('d', 'defaulted', '2026-10-28T10:00:00Z')]
    expect(oldestOpenQuestion(list)?.id).toBe('old')
  })
  it('is null when nothing is open', () => {
    expect(oldestOpenQuestion([q('a', 'answered', '2026-10-30T08:00:00Z')])).toBeNull()
    expect(oldestOpenQuestion([])).toBeNull()
  })
})
