import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import type { CompanyEvent } from '@/api/types'
import { KNOWN_EVENT_TYPES, dedupeEffects, effectsForEvent, feedItemForEvent, parseCompanyEvent } from './streamEvents'

const cid = 'c1'
const ev = (type: string, payload: Record<string, unknown> = {}, id = 1): CompanyEvent => ({ id, type, payload, created_at: '2026-10-24T09:00:00Z' })
const keys = (e: CompanyEvent) => effectsForEvent(cid, e).flatMap((x) => (x.kind === 'invalidate' ? [x.key] : []))

describe('stream reducer: Founder’s note events', () => {
  it('listens for every note event', () => {
    for (const t of ['note.created', 'note.triaged', 'note.replied']) expect(KNOWN_EVENT_TYPES).toContain(t)
  })

  it('note.created refreshes the notes list and the co-founder summary', () => {
    const k = keys(ev('note.created', { note_id: 'n1' }))
    expect(k).toContainEqual(qk.notesAll(cid))
    expect(k).toContainEqual(qk.cofounderSummary(cid))
  })

  it('note.triaged also refreshes what triage creates: goals, projects, tasks, schedules and memory', () => {
    const k = keys(ev('note.triaged', { note_id: 'n1', category: 'goal' }))
    for (const key of [qk.notesAll(cid), qk.goals(cid), qk.projects(cid), qk.tasksAll(cid), qk.schedules(cid), qk.memoryAll(cid)]) expect(k).toContainEqual(key)
  })

  it('note.replied refreshes the list and that note’s thread when the id is known', () => {
    expect(keys(ev('note.replied', { note_id: 'n7' }))).toContainEqual(qk.note(cid, 'n7'))
    const noId = keys(ev('note.replied'))
    expect(noId).toContainEqual(qk.notesAll(cid))
    expect(noId.some((k) => k.includes('detail'))).toBe(false)
  })

  it('refreshes alerts on alert events', () => {
    expect(keys(ev('alert.raised'))).toContainEqual(qk.alertsAll(cid))
    expect(keys(ev('alert.resolved'))).toContainEqual(qk.alertsAll(cid))
  })

  it('a burst of note events causes one invalidation per key', () => {
    const burst = [ev('note.created', {}, 1), ev('note.created', {}, 2), ev('note.triaged', {}, 3)].flatMap((e) => effectsForEvent(cid, e))
    const deduped = dedupeEffects(burst)
    const notes = deduped.filter((e) => e.kind === 'invalidate' && JSON.stringify(e.key) === JSON.stringify(qk.notesAll(cid)))
    expect(notes).toHaveLength(1)
  })

  it('shows readable live-feed lines that open the note', () => {
    const created = feedItemForEvent(ev('note.created', { note_id: 'n1' }))!
    expect(created.text).toBe('The Founder wrote a note')
    expect(created.agentId).toBeNull()
    expect(created.panel).toEqual({ kind: 'note', id: 'n1' })

    const triaged = feedItemForEvent(ev('note.triaged', { note_id: 'n1', category: 'directive' }, 2))!
    expect(triaged.text).toBe('The AI Co-founder triaged a note as directive')
    expect(triaged.tone).toBe('success')

    const reply = feedItemForEvent(ev('note.replied', { note_id: 'n1', author_type: 'agent', agent_id: 'f1' }, 3))!
    expect(reply.text).toBe('The AI Co-founder replied to a note')
    expect(reply.agentId).toBe('f1')

    const humanReply = feedItemForEvent(ev('note.replied', { note_id: 'n1', author_type: 'human', agent_id: 'f1' }, 4))!
    expect(humanReply.text).toBe('The Founder replied on a note')
    expect(humanReply.agentId).toBeNull()
  })

  it('falls back to the notes panel without a usable note id (never a dead link)', () => {
    expect(feedItemForEvent(ev('note.created', {}))!.panel).toEqual({ kind: 'notes' })
    expect(feedItemForEvent(ev('note.created', { note_id: '../x' }))!.panel).toEqual({ kind: 'notes' })
  })

  it('parses a named SSE note event', () => {
    const e = parseCompanyEvent('note.triaged', JSON.stringify({ note_id: 'n1' }), '12')
    expect(e).toMatchObject({ id: 12, type: 'note.triaged', payload: { note_id: 'n1' } })
  })
})
