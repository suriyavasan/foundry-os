import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import type { AuditEvent, CompanyEvent } from '@/api/types'
import { makeAgent } from '@/test/factories'
import { normalizeLive } from './liveState'
import {
  dedupeEffects,
  effectsForEvent,
  feedItemForAudit,
  feedItemForEvent,
  mergeFeed,
  parseCompanyEvent,
  patchAgentLive,
  patchOneAgentLive,
} from './streamEvents'

const ev = (type: string, payload: Record<string, unknown> = {}, id = 1): CompanyEvent => ({
  id,
  type,
  payload,
  created_at: '2026-10-23T12:00:00Z',
})

describe('parseCompanyEvent', () => {
  it('parses a full envelope', () => {
    const e = parseCompanyEvent(null, JSON.stringify({ id: 7, type: 'task.updated', payload: { status: 'done' }, created_at: 'x' }), '7')
    expect(e).toEqual({ id: 7, type: 'task.updated', payload: { status: 'done' }, created_at: 'x' })
  })
  it('parses a named event whose data is the bare payload, taking the id from Last-Event-ID', () => {
    const e = parseCompanyEvent('run.started', JSON.stringify({ run_id: 'r1' }), '42')
    expect(e).toMatchObject({ id: 42, type: 'run.started', payload: { run_id: 'r1' } })
  })
  it('rejects garbage, arrays and unnamed bare payloads', () => {
    expect(parseCompanyEvent('task.updated', 'not json', '1')).toBeNull()
    expect(parseCompanyEvent('task.updated', '[1]', '1')).toBeNull()
    expect(parseCompanyEvent('message', JSON.stringify({ a: 1 }), '1')).toBeNull()
    expect(parseCompanyEvent(null, JSON.stringify({ a: 1 }), '1')).toBeNull()
  })
})

describe('effectsForEvent', () => {
  const cid = 'c1'
  const keys = (e: CompanyEvent) => effectsForEvent(cid, e).map((x) => (x.kind === 'invalidate' ? x.key : x.kind))

  it('patches the agent cache in place on agent.activity with a recognised payload', () => {
    const effects = effectsForEvent(cid, ev('agent.activity', { agent_id: 'a1', state: 'working', summary: 'Drafting spec', current_task: { id: 't1', title: 'Spec' } }))
    expect(effects).toHaveLength(1)
    expect(effects[0]).toMatchObject({ kind: 'patchAgentLive', agentId: 'a1', live: { state: 'working', summary: 'Drafting spec' } })
  })
  it('accepts the live block nested under `live`', () => {
    const effects = effectsForEvent(cid, ev('agent.activity', { agent_id: 'a1', live: { state: 'blocked', summary: 'Needs input' } }))
    expect(effects[0]).toMatchObject({ kind: 'patchAgentLive', live: { state: 'blocked' } })
  })
  it('falls back to refetching agents when agent.activity is unrecognised', () => {
    expect(keys(ev('agent.activity', { foo: 1 }))).toEqual([qk.agents(cid)])
    expect(keys(ev('agent.activity', { agent_id: 'a1', state: 'sleeping' }))).toEqual([qk.agents(cid)])
  })
  it('invalidates tasks on task.* and the run log on run.event', () => {
    expect(keys(ev('task.updated'))).toContainEqual(qk.tasksAll(cid))
    expect(keys(ev('task.created'))).toContainEqual(qk.tasksAll(cid))
    expect(keys(ev('run.event', { run_id: 'r9' }))).toEqual([qk.runEvents(cid, 'r9')])
  })
  it('refreshes spend and artifacts when a run finishes', () => {
    const k = keys(ev('run.finished', { run_id: 'r1' }))
    expect(k).toContainEqual(qk.usageAll(cid))
    expect(k).toContainEqual(qk.runsAll(cid))
    expect(k).toContainEqual(qk.artifactsAll(cid))
  })
  it('refreshes approvals and artifacts on their events', () => {
    expect(keys(ev('approval.created'))).toContainEqual(qk.approvalsAll(cid))
    expect(keys(ev('approval.decided'))).toContainEqual(qk.approvalsAll(cid))
    expect(keys(ev('artifact.created'))).toEqual([qk.artifactsAll(cid)])
  })
  it('ignores unknown event types', () => {
    expect(effectsForEvent(cid, ev('something.else'))).toEqual([])
  })
})

describe('dedupeEffects', () => {
  it('collapses repeated invalidations and keeps only the latest live patch per agent', () => {
    const live1 = normalizeLive({ state: 'working', summary: 'one' })!
    const live2 = normalizeLive({ state: 'idle', summary: 'two' })!
    const out = dedupeEffects([
      { kind: 'invalidate', key: qk.tasksAll('c') },
      { kind: 'patchAgentLive', agentId: 'a', live: live1 },
      { kind: 'invalidate', key: qk.tasksAll('c') },
      { kind: 'patchAgentLive', agentId: 'a', live: live2 },
      { kind: 'patchAgentLive', agentId: 'b', live: live1 },
    ])
    expect(out.filter((e) => e.kind === 'invalidate')).toHaveLength(1)
    const patches = out.filter((e) => e.kind === 'patchAgentLive')
    expect(patches).toHaveLength(2)
    expect(patches.find((p) => p.kind === 'patchAgentLive' && p.agentId === 'a')).toMatchObject({ live: { summary: 'two' } })
  })
})

describe('patchAgentLive', () => {
  const live = normalizeLive({ state: 'working', summary: 'Hi' })!
  it('replaces only the matching agent, immutably', () => {
    const a = makeAgent({ id: 'a' })
    const b = makeAgent({ id: 'b' })
    const list = [a, b]
    const next = patchAgentLive(list, 'b', live)
    expect(next).not.toBe(list)
    expect(next[0]).toBe(a)
    expect(next[1]).toMatchObject({ id: 'b', live })
    expect(list[1]!.live).toBeUndefined()
  })
  it('returns the same reference when the agent is not cached, and passes undefined through', () => {
    const list = [makeAgent({ id: 'a' })]
    expect(patchAgentLive(list, 'zzz', live)).toBe(list)
    expect(patchAgentLive(undefined, 'a', live)).toBeUndefined()
  })
  it('patches a single cached agent', () => {
    const a = makeAgent({ id: 'a' })
    expect(patchOneAgentLive(a, 'a', live)?.live).toBe(live)
    expect(patchOneAgentLive(a, 'x', live)).toBe(a)
    expect(patchOneAgentLive(undefined, 'a', live)).toBeUndefined()
  })
})

describe('feedItemForEvent', () => {
  it('describes a started run and links to the task', () => {
    const item = feedItemForEvent(ev('run.started', { agent_id: 'a1', task_id: 't1', title: 'Write brief' }, 5))
    expect(item).toMatchObject({ kind: 'run', agentId: 'a1', text: 'Started working on Write brief', panel: { kind: 'task', id: 't1' }, key: 'ev:5:run.started' })
  })
  it('marks failures as danger and completions as success', () => {
    expect(feedItemForEvent(ev('run.finished', { status: 'failed', title: 'X' }))?.tone).toBe('danger')
    expect(feedItemForEvent(ev('run.finished', { status: 'succeeded', title: 'X' }))?.tone).toBe('success')
    expect(feedItemForEvent(ev('task.updated', { status: 'blocked', title: 'X' }))?.tone).toBe('warning')
  })
  it('shows progress and decisions from run events but not noise', () => {
    expect(feedItemForEvent(ev('run.event', { run_id: 'r', type: 'progress', payload: { message: 'Reading sources' } }))?.text).toBe('Reading sources')
    expect(feedItemForEvent(ev('run.event', { run_id: 'r', type: 'decision', payload: { title: 'Use Postgres' } }))?.kind).toBe('decision')
    expect(feedItemForEvent(ev('run.event', { run_id: 'r', type: 'token_delta', payload: { message: 'x' } }))).toBeNull()
    expect(feedItemForEvent(ev('run.event', { run_id: 'r', type: 'progress', payload: {} }))).toBeNull()
  })
  it('never invents text for an empty agent.activity', () => {
    expect(feedItemForEvent(ev('agent.activity', { agent_id: 'a', state: 'idle' }))).toBeNull()
  })
  it('mirrors audit activity events and ignores unknown types', () => {
    expect(feedItemForEvent(ev('activity', { summary: 'Atlas paused', actor_type: 'human' }))?.text).toBe('Atlas paused')
    expect(feedItemForEvent(ev('weird'))).toBeNull()
  })
})

describe('mergeFeed', () => {
  const audit: AuditEvent = {
    id: 'au1',
    company_id: 'c',
    actor_type: 'agent',
    actor_id: 'a1',
    actor_label: 'Atlas',
    action: 'agent.paused',
    target_type: 'agent',
    target_id: 'a1',
    summary: 'Atlas paused',
    data: {},
    created_at: '2026-10-23T12:00:00Z',
  }
  it('orders newest first and de-duplicates a stream event that mirrors a seeded audit event', () => {
    const seeded = [feedItemForAudit(audit), feedItemForAudit({ ...audit, id: 'au0', summary: 'Older', created_at: '2026-10-23T11:00:00Z' })]
    const live = [feedItemForEvent(ev('activity', { summary: 'Atlas paused', actor_type: 'agent', actor_id: 'a1' }, 9))!, feedItemForEvent(ev('task.created', { title: 'New' }, 10), '2026-10-23T12:05:00Z')!]
    live[1]!.at = '2026-10-23T12:05:00Z'
    const merged = mergeFeed([live, seeded])
    expect(merged.map((m) => m.text)).toEqual(['New task: New', 'Atlas paused', 'Older'])
  })
  it('limits the feed', () => {
    const many = Array.from({ length: 10 }, (_, i) => feedItemForAudit({ ...audit, id: `x${i}`, summary: `s${i}`, created_at: `2026-10-23T12:00:0${i}Z` }))
    expect(mergeFeed([many], 4)).toHaveLength(4)
  })
})
