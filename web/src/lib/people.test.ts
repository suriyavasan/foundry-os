import { describe, expect, it } from 'vitest'
import { makeAgent, makeMember, makeTask } from '@/test/factories'
import { agentRef, canManageTeam, initials, isWorkingMember, managerOptions, memberRef, openTasksOfMember, parsePersonRef, roleLabel, taskAssignee } from './people'

describe('person references', () => {
  it('round-trips and rejects malformed refs', () => {
    expect(parsePersonRef(memberRef('m1'))).toEqual({ type: 'member', id: 'm1' })
    expect(parsePersonRef(agentRef('a1'))).toEqual({ type: 'agent', id: 'a1' })
    for (const bad of [null, undefined, '', 'm1', 'user:1', 'member:', 'member:a b', 'member:../x']) expect(parsePersonRef(bad as string)).toBeNull()
  })

  it('reads the assignee from assignee_ref, falling back to the agent id', () => {
    expect(taskAssignee(makeTask({ assignee_ref: 'member:m1', assignee_agent_id: null }))).toEqual({ type: 'member', id: 'm1' })
    expect(taskAssignee(makeTask({ assignee_agent_id: 'a1' }))).toEqual({ type: 'agent', id: 'a1' })
    expect(taskAssignee(makeTask())).toBeNull()
  })
})

describe('members', () => {
  it('lists open tasks of one member only, oldest first', () => {
    const tasks = [
      makeTask({ id: 't2', assignee_ref: 'member:m1', created_at: '2026-10-24T10:00:00Z' }),
      makeTask({ id: 't1', assignee_ref: 'member:m1', created_at: '2026-10-24T09:00:00Z' }),
      makeTask({ id: 'done', assignee_ref: 'member:m1', status: 'done' }),
      makeTask({ id: 'other', assignee_ref: 'member:m2' }),
      makeTask({ id: 'agent', assignee_agent_id: 'm1' }),
    ]
    expect(openTasksOfMember(tasks, 'm1').map((t) => t.id)).toEqual(['t1', 't2'])
  })

  it('only active non-viewers can work or sit in the chart', () => {
    expect(isWorkingMember(makeMember())).toBe(true)
    expect(isWorkingMember(makeMember({ role: 'viewer' }))).toBe(false)
    expect(isWorkingMember(makeMember({ status: 'removed' }))).toBe(false)
  })

  it('makes initials', () => {
    expect(initials('Maya Chen')).toBe('MC')
    expect(initials('maya')).toBe('MA')
    expect(initials('Ana María de la Cruz')).toBe('AC')
    expect(initials('  ')).toBe('?')
  })

  it('manager options include agents and working members, not the person themself', () => {
    const a = makeAgent({ id: 'a1', name: 'Atlas' })
    const retired = makeAgent({ id: 'a2', name: 'Gone', status: 'retired' })
    const me = makeMember({ id: 'm1', display_name: 'Me' })
    const other = makeMember({ id: 'm2', display_name: 'Ben', title: 'Design' })
    const viewer = makeMember({ id: 'm3', role: 'viewer' })
    const opts = managerOptions([a, retired], [me, other, viewer], { memberId: 'm1' })
    expect(opts.map((o) => o.ref)).toEqual(['agent:a1', 'member:m2'])
    expect(opts[1]!.label).toBe('Ben · Design')
  })

  it('role helpers', () => {
    expect(roleLabel('admin')).toBe('Admin')
    expect(roleLabel('custom')).toBe('custom')
    expect(canManageTeam('owner')).toBe(true)
    expect(canManageTeam('admin')).toBe(true)
    expect(canManageTeam('member')).toBe(false)
    expect(canManageTeam('viewer')).toBe(false)
    expect(canManageTeam(undefined)).toBe(false)
  })
})
