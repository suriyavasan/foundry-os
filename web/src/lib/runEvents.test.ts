import { describe, expect, it } from 'vitest'
import type { RunEvent } from '@/api/types'
import { describeRunEvent, lastSeq, mergeRunEvents } from './runEvents'

const e = (seq: number, type = 'progress', payload: Record<string, unknown> = {}): RunEvent => ({ seq, type, payload, occurred_at: '2026-10-23T12:00:00Z' })

describe('mergeRunEvents', () => {
  it('appends new events in seq order', () => {
    expect(mergeRunEvents([e(1), e(2)], [e(3)]).map((x) => x.seq)).toEqual([1, 2, 3])
  })
  it('is idempotent when events are replayed or arrive out of order', () => {
    const merged = mergeRunEvents([e(1), e(3)], [e(2), e(3), e(1)])
    expect(merged.map((x) => x.seq)).toEqual([1, 2, 3])
  })
  it('returns the same array when nothing is incoming', () => {
    const prev = [e(1)]
    expect(mergeRunEvents(prev, [])).toBe(prev)
  })
  it('finds the last seq', () => {
    expect(lastSeq([])).toBe(0)
    expect(lastSeq([e(4), e(9)])).toBe(9)
  })
})
describe('describeRunEvent', () => {
  it('prefers a message, falls back to the type', () => {
    expect(describeRunEvent(e(1, 'progress', { message: 'Reading' }))).toBe('Reading')
    expect(describeRunEvent(e(1, 'tool_call', {}))).toBe('tool call')
  })
})
