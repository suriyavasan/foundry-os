import { describe, expect, it } from 'vitest'
import { normalizeLive } from './liveState'
import { agentStateText, currentTaskTitle, orgListRows } from './orgList'
import { buildOrgTree } from './orgTree'
import { makeAgent, makeTask } from '@/test/factories'

describe('org list', () => {
  const co = makeAgent({ id: 'co', name: 'Atlas', kind: 'founder', reports_to_human: true })
  const m1 = makeAgent({ id: 'm1', name: 'Iris', kind: 'lead', manager_agent_id: 'co' })
  const m2 = makeAgent({ id: 'm2', name: 'Boris', kind: 'lead', manager_agent_id: 'co' })
  const r1 = makeAgent({ id: 'r1', name: 'Dev', manager_agent_id: 'm1' })
  const r2 = makeAgent({ id: 'r2', name: 'Eve', manager_agent_id: 'm1' })
  const r3 = makeAgent({ id: 'r3', name: 'Finn', manager_agent_id: 'r1' })

  it('groups people under their manager, one indent per level, depth first', () => {
    const rows = orgListRows(buildOrgTree([r3, r2, r1, m2, m1, co]))
    expect(rows.map((r) => [r.person.agent!.name, r.indent])).toEqual([
      ['Atlas', 0],
      ['Boris', 1],
      ['Iris', 1],
      ['Dev', 2],
      ['Finn', 3],
      ['Eve', 2],
    ])
  })

  it('says what an agent is doing without inventing anything', () => {
    const working = makeAgent({ live: normalizeLive({ state: 'working', current_task: { id: 't1', title: 'Write the pitch' } }) })
    expect(agentStateText(working)).toBe('Working on Write the pitch')
    expect(agentStateText(makeAgent({ live: normalizeLive({ state: 'working', summary: 'Researching' }) }))).toBe('Researching')
    expect(agentStateText(makeAgent())).toBe('Idle')
    expect(agentStateText(makeAgent({ status: 'paused' }))).toBe('Paused')
    expect(agentStateText(working, 'Other title')).toBe('Working on Other title')
  })

  it('finds the task title from the live block, or the task list', () => {
    const withRef = makeAgent({ live: normalizeLive({ state: 'working', current_task: { id: 't9' } }) })
    expect(currentTaskTitle(withRef, [makeTask({ id: 't9', title: 'From the list' })])).toBe('From the list')
    expect(currentTaskTitle(withRef, undefined)).toBeNull()
    expect(currentTaskTitle(makeAgent(), [])).toBeNull()
  })
})
