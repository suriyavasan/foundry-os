import { describe, expect, it } from 'vitest'
import { makeAgent } from '@/test/factories'
import { RING_STYLE, normalizeLive, ringStateFor, ringStyleFor } from './liveState'

const live = (over: Record<string, unknown> = {}) =>
  normalizeLive({ state: 'idle', current_task: null, current_run: null, summary: null, since: null, ...over })

describe('ringStateFor', () => {
  it('maps live states to rings for an active agent', () => {
    expect(ringStateFor(makeAgent({ live: live({ state: 'working' }) }))).toBe('working')
    expect(ringStateFor(makeAgent({ live: live({ state: 'idle' }) }))).toBe('idle')
    expect(ringStateFor(makeAgent({ live: live({ state: 'blocked' }) }))).toBe('blocked')
    expect(ringStateFor(makeAgent({ live: live({ state: 'offline' }) }))).toBe('offline')
  })

  it('treats a missing live block as idle rather than inventing activity', () => {
    expect(ringStateFor(makeAgent({ status: 'active' }))).toBe('idle')
    expect(ringStateFor(makeAgent({ status: 'active', live: null }))).toBe('idle')
  })

  it('lets lifecycle status override a stale working live state', () => {
    expect(ringStateFor(makeAgent({ status: 'paused', live: live({ state: 'working' }) }))).toBe('paused')
    expect(ringStateFor(makeAgent({ status: 'proposed', live: live({ state: 'working' }) }))).toBe('proposed')
    expect(ringStateFor(makeAgent({ status: 'retired' }))).toBe('retired')
    expect(ringStateFor(makeAgent({ status: 'rejected' }))).toBe('retired')
    expect(ringStateFor(makeAgent({ status: 'retiring' }))).toBe('paused')
  })

  it('shows failed when an idle agent last run failed or timed out', () => {
    const failed = live({ state: 'idle', current_run: { id: 'r1', status: 'failed' } })
    const timed = live({ state: 'idle', current_run: { id: 'r1', status: 'timed_out' } })
    expect(ringStateFor(makeAgent({ live: failed }))).toBe('failed')
    expect(ringStateFor(makeAgent({ live: timed }))).toBe('failed')
    // but a working agent with a retried run stays working
    expect(ringStateFor(makeAgent({ live: live({ state: 'working', current_run: { id: 'r2', status: 'running' } }) }))).toBe('working')
  })
})

describe('RING_STYLE', () => {
  it('pulses only while working', () => {
    const pulsing = Object.entries(RING_STYLE).filter(([, s]) => s.pulse).map(([k]) => k)
    expect(pulsing).toEqual(['working'])
  })
  it('uses a dashed ring for proposed and dims paused agents', () => {
    expect(RING_STYLE.proposed.dashed).toBe(true)
    expect(RING_STYLE.paused.dimmed).toBe(true)
    expect(RING_STYLE.idle.dimmed).toBe(false)
    expect(RING_STYLE.failed.token).toBe('--live-failed')
    expect(RING_STYLE.blocked.token).toBe('--live-blocked')
  })
  it('ringStyleFor returns the state with its style', () => {
    const r = ringStyleFor(makeAgent({ live: live({ state: 'working' }) }))
    expect(r.state).toBe('working')
    expect(r.pulse).toBe(true)
  })
})

describe('normalizeLive', () => {
  it('parses object and bare-id task/run refs', () => {
    const a = normalizeLive({ state: 'working', current_task: { id: 't1', title: 'Write spec' }, current_run: 'r1', summary: 'Drafting', since: '2026-10-23T12:00:00Z' })
    expect(a?.current_task).toEqual({ id: 't1', title: 'Write spec', status: null })
    expect(a?.current_run).toEqual({ id: 'r1' })
    expect(a?.summary).toBe('Drafting')
  })
  it('rejects payloads without a valid state', () => {
    expect(normalizeLive(null)).toBeNull()
    expect(normalizeLive({ state: 'sleeping' })).toBeNull()
    expect(normalizeLive('working')).toBeNull()
  })
})
