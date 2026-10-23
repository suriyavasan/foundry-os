import { describe, expect, it } from 'vitest'
import type { CofounderPlanProject } from '@/api/types'
import { makeSummary } from '@/test/factories'
import { buildPlanTree, planTotals, wakeResultText } from './cofounder'

const goal = (id: string, over = {}) => ({ id, title: `Goal ${id}`, status: 'active' as const, priority: 'normal' as const, ...over })
const project = (id: string, goal_id: string | null, over: Partial<CofounderPlanProject> = {}): CofounderPlanProject => ({
  id,
  goal_id,
  name: `Project ${id}`,
  status: 'active',
  lead_agent_id: 'l1',
  tasks_done: 1,
  tasks_total: 3,
  open_tasks: [
    { id: `${id}-t1`, title: 'Open task', status: 'in_progress', assignee_agent_id: 'a1' },
    { id: `${id}-t2`, title: 'Another', status: 'backlog', assignee_agent_id: null },
  ],
  ...over,
})

describe('buildPlanTree', () => {
  it('nests projects under their goal and tasks under their project, using the server counts', () => {
    const tree = buildPlanTree(makeSummary({ goals: [goal('g1')], plan: { summary: null, updated_at: null, projects: [project('p1', 'g1'), project('p2', 'g1')] } }))
    expect(tree).toHaveLength(1)
    expect(tree[0]!.goal!.id).toBe('g1')
    expect(tree[0]!.projects.map((p) => p.project.id)).toEqual(['p1', 'p2'])
    expect(tree[0]!.projects[0]).toMatchObject({ done: 1, total: 3, hiddenTasks: 0 })
    expect(tree[0]!.projects[0]!.tasks.map((t) => t.id)).toEqual(['p1-t1', 'p1-t2'])
  })

  it('orders active goals first, then by priority, and keeps goals that have no projects', () => {
    const tree = buildPlanTree(
      makeSummary({
        goals: [goal('done', { status: 'achieved' }), goal('low', { priority: 'low' }), goal('urgent', { priority: 'urgent' }), goal('prop', { status: 'proposed' })],
      }),
    )
    expect(tree.map((n) => n.goal!.id)).toEqual(['urgent', 'low', 'prop', 'done'])
    expect(tree.every((n) => n.projects.length === 0)).toBe(true)
  })

  it('groups projects whose goal is unknown or missing at the end instead of dropping them', () => {
    const tree = buildPlanTree(makeSummary({ goals: [goal('g1')], plan: { summary: null, updated_at: null, projects: [project('p1', 'g1'), project('p2', null), project('p3', 'gone')] } }))
    expect(tree).toHaveLength(2)
    expect(tree[1]!.goal).toBeNull()
    expect(tree[1]!.projects.map((p) => p.project.id)).toEqual(['p2', 'p3'])
  })

  it('shows at most N open tasks per project and counts the rest', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ id: `t${i}`, title: `T${i}`, status: 'backlog' as const, assignee_agent_id: null }))
    const tree = buildPlanTree(makeSummary({ goals: [goal('g1')], plan: { summary: null, updated_at: null, projects: [project('p1', 'g1', { open_tasks: many })] } }), 5)
    expect(tree[0]!.projects[0]!.tasks).toHaveLength(5)
    expect(tree[0]!.projects[0]!.hiddenTasks).toBe(4)
    expect(planTotals(tree)).toEqual({ goals: 1, projects: 1, openTasks: 9 })
  })

  it('is empty for an empty summary', () => {
    expect(buildPlanTree(makeSummary())).toEqual([])
  })
})

describe('wakeResultText', () => {
  it('reports exactly what the server did', () => {
    expect(wakeResultText({ status: 'queued', task_id: 't' })).toMatch(/queued/i)
    expect(wakeResultText({ status: 'coalesced', task_id: 't' })).toMatch(/already waiting/i)
    expect(wakeResultText({ status: 'weird', task_id: null })).toContain('weird')
  })
})
