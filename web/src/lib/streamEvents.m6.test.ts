import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent, feedItemForEvent, parseCompanyEvent } from './streamEvents'

const ev = (type: string, payload: Record<string, unknown> = {}) => ({ id: 7, type, payload })
const keys = (type: string, payload?: Record<string, unknown>) => effectsForEvent('c1', ev(type, payload)).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))

describe('guardrail stream events', () => {
  it('listens for guardrail.event', () => {
    expect(KNOWN_EVENT_TYPES).toContain('guardrail.event')
  })

  it('a guardrail.event refreshes the log, rate limits and approvals', () => {
    const k = keys('guardrail.event', { decision: 'deny' })
    expect(k).toContain(JSON.stringify(qk.guardrailsAll('c1')))
    expect(k).toContain(JSON.stringify(qk.approvalsAll('c1')))
  })

  it('emergency stop events refresh the system status and work state', () => {
    const k = keys('system.emergency_stop')
    expect(k).toContain(JSON.stringify(qk.systemStatus))
    expect(k).toContain(JSON.stringify(qk.runsAll('c1')))
    expect(keys('system.resumed')).toContain(JSON.stringify(qk.systemStatus))
  })

  it('a finished run and a policy change refresh finance', () => {
    expect(keys('run.finished')).toContain(JSON.stringify(qk.financeAll('c1')))
    expect(keys('policy.updated')).toContain(JSON.stringify(qk.financeAll('c1')))
    expect(keys('expense.created')).toContain(JSON.stringify(qk.financeAll('c1')))
  })

  it('parses a named guardrail.event frame', () => {
    const parsed = parseCompanyEvent('guardrail.event', JSON.stringify({ id: 3, type: 'guardrail.event', payload: { action: 'email.send', decision: 'peer_review' } }), '3')
    expect(parsed).toMatchObject({ id: 3, type: 'guardrail.event', payload: { action: 'email.send' } })
  })

  it('shows decisions that held something back in the feed, but not the routine allows', () => {
    expect(feedItemForEvent(ev('guardrail.event', { decision: 'allow', action: 'task.create' }))).toBeNull()
    const denied = feedItemForEvent(ev('guardrail.event', { decision: 'deny', action: 'domain.register', agent_id: 'a1' }))
    expect(denied).toMatchObject({ kind: 'guardrail', tone: 'danger', text: 'Blocked: domain.register', agentId: 'a1', panel: { kind: 'guardrails' } })
    expect(feedItemForEvent(ev('guardrail.event', { decision: 'peer_review', action: 'email.send' }))).toMatchObject({ tone: 'warning', text: 'Sent to peer review: email.send' })
    expect(feedItemForEvent(ev('guardrail.event', { decision: 'human_approval' }))).toMatchObject({ text: 'Needs approval' })
    expect(feedItemForEvent(ev('guardrail.event', {}))).toBeNull()
  })
})

describe('M8 stream events', () => {
  it('refreshes reachability, accounts, domains and mailboxes', () => {
    for (const t of ['reachability.updated', 'tunnel.updated', 'connected_account.updated', 'domain.updated', 'mailbox.updated', 'hosted_ui.updated']) expect(KNOWN_EVENT_TYPES).toContain(t)
    expect(keys('tunnel.updated')).toContain(JSON.stringify(qk.reachability))
    expect(keys('connected_account.updated')).toContain(JSON.stringify(qk.connectedAccounts('c1')))
    expect(keys('domain.updated')).toContain(JSON.stringify(qk.domainAll('c1')))
    expect(keys('mailbox.updated')).toContain(JSON.stringify(qk.emailAll('c1')))
  })
})
