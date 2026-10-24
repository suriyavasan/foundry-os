import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent, feedItemForEvent } from './streamEvents'

const ev = (type: string, payload: Record<string, unknown> = {}) => ({ id: 3, type, payload })
const keys = (type: string, payload?: Record<string, unknown>) => effectsForEvent('c1', ev(type, payload)).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))

describe('M10 stream events', () => {
  it('registers a listener for every event name of the spec', () => {
    for (const t of ['reflection.created', 'reflection.updated', 'reflection.responded', 'standard.adopted', 'standard.updated', 'command.updated']) expect(KNOWN_EVENT_TYPES).toContain(t)
  })

  it('reflection events refresh the reflections (and the open thread when the event names it)', () => {
    expect(keys('reflection.created')).toContain(JSON.stringify(qk.reflectionsAll('c1')))
    expect(keys('reflection.updated')).toContain(JSON.stringify(qk.reflectionsAll('c1')))
    const responded = keys('reflection.responded', { reflection_id: 'rf1' })
    expect(responded).toContain(JSON.stringify(qk.reflectionsAll('c1')))
    expect(responded).toContain(JSON.stringify(qk.reflection('c1', 'rf1')))
    expect(keys('reflection.responded')).not.toContain(JSON.stringify(qk.reflection('c1', 'rf1')))
  })

  it('standard events refresh the standards and the reflections that were adopted', () => {
    for (const t of ['standard.adopted', 'standard.updated']) {
      const k = keys(t)
      expect(k).toContain(JSON.stringify(qk.standardsAll('c1')))
      expect(k).toContain(JSON.stringify(qk.reflectionsAll('c1')))
    }
  })

  it('command.updated refreshes the command feed (and the notes free text is stored in)', () => {
    const k = keys('command.updated', { id: 'e1' })
    expect(k).toContain(JSON.stringify(qk.commandFeed('c1')))
    expect(k).toContain(JSON.stringify(qk.notesAll('c1')))
  })

  it('a policy change re-reads the AI setup (the gate)', () => {
    expect(keys('policy.updated')).toContain(JSON.stringify(qk.aiSetup('c1')))
  })

  it('all of them live under the company key, so one prefix clears them', () => {
    for (const key of [qk.aiSetup('c1'), qk.reflections('c1', 'open'), qk.reflection('c1', 'x'), qk.standards('c1'), qk.commandFeed('c1')]) expect(key.slice(0, 2)).toEqual(['company', 'c1'])
  })

  it('a new reflection shows up in the live feed and links to its thread', () => {
    const item = feedItemForEvent(ev('reflection.created', { reflection_id: 'rf1', title: 'Sources' }))
    expect(item).toMatchObject({ text: 'The AI Co-founder shared a reflection: Sources', panel: { kind: 'reflection', id: 'rf1' } })
    expect(feedItemForEvent(ev('reflection.created'))).toMatchObject({ panel: { kind: 'reflections' } })
    expect(feedItemForEvent(ev('standard.adopted', { title: 'Cite' }))).toMatchObject({ text: 'Standard adopted: Cite', tone: 'success', panel: { kind: 'standards' } })
  })
})
