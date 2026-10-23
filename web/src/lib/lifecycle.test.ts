import { describe, expect, it } from 'vitest'
import type { AgentStatus } from '@/api/types'
import { availableLifecycleActions, isReadOnlyStatus } from './lifecycle'

const actions = (s: AgentStatus) => availableLifecycleActions(s).map((a) => a.action)

describe('availableLifecycleActions', () => {
  it('matches the transition table in docs/api.md', () => {
    expect(actions('proposed')).toEqual(['activate', 'reject'])
    expect(actions('active')).toEqual(['pause', 'retire'])
    expect(actions('paused')).toEqual(['resume', 'retire'])
  })

  it('never offers retire for the founder', () => {
    const founder = (s: AgentStatus) => availableLifecycleActions(s, 'founder').map((a) => a.action)
    expect(founder('active')).toEqual(['pause'])
    expect(founder('paused')).toEqual(['resume'])
  })

  it('offers nothing for terminal or transitional statuses', () => {
    expect(actions('retired')).toEqual([])
    expect(actions('rejected')).toEqual([])
    expect(actions('retiring')).toEqual([])
  })

  it('requires confirmation exactly for destructive actions (reject, retire)', () => {
    const all = (['proposed', 'active', 'paused'] as AgentStatus[]).flatMap((s) => availableLifecycleActions(s))
    for (const a of all) {
      expect(a.confirm).toBe(a.action === 'reject' || a.action === 'retire')
      expect(a.destructive).toBe(a.confirm)
    }
  })

  it('only retired agents are read-only', () => {
    expect(isReadOnlyStatus('retired')).toBe(true)
    expect(isReadOnlyStatus('paused')).toBe(false)
  })
})
