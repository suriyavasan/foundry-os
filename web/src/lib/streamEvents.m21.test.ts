import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent } from './streamEvents'

describe('M21 stream events: meetings', () => {
  it('listens for meeting.updated', () => {
    expect(KNOWN_EVENT_TYPES).toContain('meeting.updated')
  })
  it('refreshes meetings, the live agent state (who is in a meeting) and the tasks a conclusion creates', () => {
    const keys = effectsForEvent('c1', { id: 3, type: 'meeting.updated', payload: { meeting_id: 'm1', status: 'running', round: 1, max_rounds: 2 } }).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))
    expect(keys).toContain(JSON.stringify(qk.meetingsAll('c1')))
    expect(keys).toContain(JSON.stringify(qk.agents('c1')))
    expect(keys).toContain(JSON.stringify(qk.tasksAll('c1')))
  })
})
