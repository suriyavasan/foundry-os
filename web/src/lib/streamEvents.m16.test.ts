import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent } from './streamEvents'

const ev = (type: string, payload: Record<string, unknown> = {}) => ({ id: 4, type, payload })
const keys = (type: string, payload?: Record<string, unknown>) => effectsForEvent('c1', ev(type, payload)).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))
const radarAll = JSON.stringify(qk.radarAll('c1'))

describe('M16 stream events', () => {
  it('listens for the radar events the backend emits', () => {
    for (const t of ['radar.brief.created', 'radar.item.updated', 'radar.idea.created', 'radar.idea.accepted', 'radar.idea.rejected', 'radar.idea.reopened', 'radar.idea.merged', 'radar.idea.done', 'radar.idea.expired']) {
      expect(KNOWN_EVENT_TYPES).toContain(t)
    }
  })
  it('an item change refreshes the radar views only', () => {
    expect(keys('radar.item.updated', { item_id: 'i1' })).toEqual([radarAll])
  })
  it('a new brief also refreshes artifacts and the activity feed', () => {
    const k = keys('radar.brief.created', { brief_id: 'b1' })
    expect(k).toContain(radarAll)
    expect(k).toContain(JSON.stringify(qk.artifactsAll('c1')))
    expect(k).toContain(JSON.stringify(qk.activityAll('c1')))
  })
  it('an idea decision also refreshes the work it became', () => {
    for (const t of ['radar.idea.created', 'radar.idea.accepted', 'radar.idea.rejected', 'radar.idea.reopened', 'radar.idea.merged', 'radar.idea.done', 'radar.idea.expired']) {
      const k = keys(t, { idea_id: 'i1' })
      expect(k).toContain(radarAll)
      expect(k).toContain(JSON.stringify(qk.tasksAll('c1')))
      expect(k).toContain(JSON.stringify(qk.goals('c1')))
      expect(k).toContain(JSON.stringify(qk.projects('c1')))
    }
  })
  it('a source, keyword or fetch change arrives as an activity event named radar.*', () => {
    expect(keys('activity', { action: 'radar.source.updated' })).toContain(radarAll)
    expect(keys('activity', { action: 'radar.fetched' })).toContain(radarAll)
    expect(keys('activity', { action: 'radar.keyword.created' })).toContain(radarAll)
  })
  it('other activity events leave the radar alone', () => {
    expect(keys('activity', { action: 'task.updated' })).toEqual([JSON.stringify(qk.activityAll('c1'))])
    expect(keys('activity', {})).toEqual([JSON.stringify(qk.activityAll('c1'))])
  })
})
