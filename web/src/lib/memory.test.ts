import { describe, expect, it } from 'vitest'
import { makeMemory } from '@/test/factories'
import { DEFAULT_MEMORY_FILTERS, countByKind, filterMemory, isDecidable, provenanceOf } from './memory'

const entries = [
  makeMemory({ id: 'a', kind: 'fact', status: 'verified', body: 'We sell to dentists', created_at: '2026-10-24T08:00:00Z' }),
  makeMemory({ id: 'b', kind: 'lesson', status: 'proposed', title: 'Retry less', body: 'Retrying 5 times wastes budget', created_at: '2026-10-24T07:00:00Z', tags: ['budget'] }),
  makeMemory({ id: 'c', kind: 'requirement', status: 'rejected', body: 'No cold calls', created_at: '2026-10-24T09:00:00Z' }),
]

describe('memory filters', () => {
  it('lists proposed entries first (they need a decision), then newest first', () => {
    expect(filterMemory(entries, DEFAULT_MEMORY_FILTERS).map((e) => e.id)).toEqual(['b', 'c', 'a'])
  })
  it('filters by kind, status and text (title, body, tags)', () => {
    expect(filterMemory(entries, { ...DEFAULT_MEMORY_FILTERS, kind: 'fact' }).map((e) => e.id)).toEqual(['a'])
    expect(filterMemory(entries, { ...DEFAULT_MEMORY_FILTERS, status: 'proposed' }).map((e) => e.id)).toEqual(['b'])
    expect(filterMemory(entries, { ...DEFAULT_MEMORY_FILTERS, q: 'DENTISTS' }).map((e) => e.id)).toEqual(['a'])
    expect(filterMemory(entries, { ...DEFAULT_MEMORY_FILTERS, q: 'budget' }).map((e) => e.id)).toEqual(['b'])
    expect(filterMemory(entries, { ...DEFAULT_MEMORY_FILTERS, kind: 'research' })).toEqual([])
  })
  it('counts per kind and only proposed entries are decidable', () => {
    expect(countByKind(entries)).toEqual({ all: 3, fact: 1, decision: 0, requirement: 1, lesson: 1, research: 0 })
    expect(entries.filter(isDecidable).map((e) => e.id)).toEqual(['b'])
  })
})

describe('provenanceOf', () => {
  it('links note, task and artifact sources to their panels', () => {
    expect(provenanceOf({ type: 'note', id: 'n1' })).toEqual({ text: 'Founder’s note', panel: { kind: 'note', id: 'n1' } })
    expect(provenanceOf({ type: 'task', id: 't1', label: 'Write copy' })).toEqual({ text: 'Write copy', panel: { kind: 'task', id: 't1' } })
    expect(provenanceOf({ type: 'artifact', id: 'a1' })?.panel).toEqual({ kind: 'artifact', id: 'a1' })
  })
  it('links a run through its task, and shows plain text when that is unknown', () => {
    expect(provenanceOf({ type: 'run', id: 'r1', task_id: 't1' })?.panel).toEqual({ kind: 'task', id: 't1', run: 'r1' })
    expect(provenanceOf({ type: 'run', id: 'r1' })).toEqual({ text: 'Run', panel: null })
  })
  it('is null without a source', () => {
    expect(provenanceOf(null)).toBeNull()
    expect(provenanceOf(undefined)).toBeNull()
  })
})
