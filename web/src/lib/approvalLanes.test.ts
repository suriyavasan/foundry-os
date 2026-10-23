import { describe, expect, it } from 'vitest'
import { makeApproval } from '@/test/factories'
import { approvalLane, groupByTier, readDlpFindings, readOutbound, splitByLane } from './approvalLanes'

describe('approval lanes', () => {
  it('splits by who decides', () => {
    const lanes = splitByLane([
      makeApproval({ id: 'h', approver: 'human' }),
      makeApproval({ id: 'f', approver: 'founder' }),
      makeApproval({ id: 'p', approver: 'peer' }),
    ])
    expect(lanes.human.map((a) => a.id)).toEqual(['h'])
    expect(lanes.cofounder.map((a) => a.id)).toEqual(['f'])
    expect(lanes.peer.map((a) => a.id)).toEqual(['p'])
    expect(approvalLane({ approver: 'founder' })).toBe('cofounder')
  })

  it('groups by risk tier in canonical order, untiered last, newest first', () => {
    const groups = groupByTier([
      makeApproval({ id: 'none' }),
      makeApproval({ id: 'd', tier: 'destructive' }),
      makeApproval({ id: 'c1', tier: 'external_communication', created_at: '2026-10-24T08:00:00Z' }),
      makeApproval({ id: 'c2', tier: 'external_communication', created_at: '2026-10-24T10:00:00Z' }),
      makeApproval({ id: 's', tier: 'spend' }),
    ])
    expect(groups.map((g) => g.tier)).toEqual(['external_communication', 'spend', 'destructive', null])
    expect(groups[0]!.items.map((a) => a.id)).toEqual(['c2', 'c1'])
  })

  it('treats an unknown tier as untiered', () => {
    const groups = groupByTier([makeApproval({ tier: 'mystery' as never })])
    expect(groups.map((g) => g.tier)).toEqual([null])
  })
})

describe('outbound content', () => {
  it('prefers the top-level outbound block', () => {
    const out = readOutbound(makeApproval({ outbound: { channel: 'email', to: 'a@b.co', subject: 'Hi', body: 'Hello there' }, scope: { body: 'other' } }))
    expect(out).toEqual({ body: 'Hello there', channel: 'email', to: 'a@b.co', subject: 'Hi' })
  })
  it('falls back to the scope or evidence', () => {
    expect(readOutbound(makeApproval({ scope: { outbound: { body: 'from scope', channel: 'post' } } }))?.body).toBe('from scope')
    expect(readOutbound(makeApproval({ evidence: { content: 'from evidence' } }))?.body).toBe('from evidence')
  })
  it('is null when there is nothing to show', () => {
    expect(readOutbound(makeApproval({ scope: { amount: 5 } }))).toBeNull()
    expect(readOutbound(makeApproval({}))).toBeNull()
  })
})

describe('DLP findings', () => {
  it('keeps only kind, severity, description and count: a matched value never survives', () => {
    const findings = readDlpFindings(
      makeApproval({
        dlp_findings: [{ kind: 'secret', severity: 'high', description: 'API key pattern', count: 2, match: 'sk-live-123456', excerpt: '...sk-live...' } as never, { kind: '' } as never, null as never],
      }),
    )
    expect(findings).toEqual([{ kind: 'secret', severity: 'high', description: 'API key pattern', count: 2 }])
    expect(JSON.stringify(findings)).not.toContain('sk-live')
  })
  it('reads findings from the evidence and accepts `type` for `kind`', () => {
    expect(readDlpFindings(makeApproval({ evidence: { dlp_findings: [{ type: 'email', severity: 'weird' }] } }))).toEqual([{ kind: 'email', severity: null, description: null, count: null }])
  })
  it('is empty without a scan result', () => {
    expect(readDlpFindings(makeApproval({}))).toEqual([])
  })
})
