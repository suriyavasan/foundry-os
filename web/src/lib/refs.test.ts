import { describe, expect, it } from 'vitest'
import { panelForRef, refTypeLabel } from './refs'

describe('panelForRef', () => {
  it('maps record types to the panel that shows them', () => {
    expect(panelForRef('task', 't1')).toEqual({ kind: 'task', id: 't1' })
    expect(panelForRef('artifact', 'a1')).toEqual({ kind: 'artifact', id: 'a1' })
    expect(panelForRef('agent', 'a1')).toEqual({ kind: 'agent', id: 'a1' })
    expect(panelForRef('note', 'n1')).toEqual({ kind: 'note', id: 'n1' })
    expect(panelForRef('goal', 'g1')).toEqual({ kind: 'goals' })
    expect(panelForRef('project', 'p1')).toEqual({ kind: 'goals' })
    expect(panelForRef('schedule', 's1')).toEqual({ kind: 'routines' })
    expect(panelForRef('approval', 'x')).toEqual({ kind: 'approvals' })
  })

  it('refuses unknown types, missing ids and ids that could not round-trip through the URL', () => {
    expect(panelForRef(null, 't1')).toBeNull()
    expect(panelForRef('task', null)).toBeNull()
    expect(panelForRef('task', 'a/b')).toBeNull()
    expect(panelForRef('spaceship', 's1')).toBeNull()
  })

  it('has readable labels', () => {
    expect(refTypeLabel('goal')).toBe('Goal')
    expect(refTypeLabel('budget_threshold')).toBe('Budget threshold')
  })
})
