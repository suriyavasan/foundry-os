import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent } from './streamEvents'

const ev = (type: string, payload: Record<string, unknown> = {}) => ({ id: 3, type, payload })
const keys = (type: string, payload?: Record<string, unknown>) => effectsForEvent('c1', ev(type, payload)).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))

describe('M14 stream events', () => {
  it('listens for repository.updated and refreshes every repository view and the activity feed', () => {
    expect(KNOWN_EVENT_TYPES).toContain('repository.updated')
    const k = keys('repository.updated', { repository_id: 'r1' })
    expect(k).toContain(JSON.stringify(qk.reposAll('c1')))
    expect(k).toContain(JSON.stringify(qk.activityAll('c1')))
  })
})
