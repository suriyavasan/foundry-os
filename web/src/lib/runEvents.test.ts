import { describe, expect, it } from 'vitest'
import type { RunEvent } from '@/api/types'
import { describeGraphStep, describeRunEvent, isGraphStep, lastSeq, mergeRunEvents } from './runEvents'

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

describe('describeGraphStep (M18 work graph)', () => {
  const g = (node: string, outcome: string, extra: Record<string, unknown> = {}) => describeGraphStep(e(1, 'graph.node', { node, outcome, summary: `${node}: ${outcome}`, ...extra }))
  it('turns transitions into plain step lines', () => {
    expect(isGraphStep(e(1, 'graph.node'))).toBe(true)
    expect(isGraphStep(e(1, 'progress'))).toBe(false)
    expect(g('check', 'passed')).toBe('Self-check passed')
    expect(g('check', 'failed/revise', { revisions: 1 })).toBe('Revising (1/2)')
    expect(g('check', 'failed')).toBe('Self-check found problems; sent for review')
    expect(g('wait_founder', 'waiting')).toBe('Waiting for your answer')
    expect(g('wait_approval', 'waiting')).toBe('Waiting for an approval')
    expect(g('wait_founder', 'resumed')).toBe('Got your answer, continuing')
    expect(g('finish', 'checked/applied')).toBe('Finished')
    expect(g('graph', 'continued_from_checkpoint')).toBe('Continued from where the earlier run stopped')
  })
  it('falls back to the server summary for an outcome it does not know', () => {
    expect(g('act', 'brand_new_outcome')).toBe('act: brand_new_outcome')
  })
})
