import type { CofounderGoal, CofounderPlanProject, CofounderPlanTask, CofounderSummary, CofounderWakeResult, Priority } from '@/api/types'

const PRIORITY_RANK: Record<Priority, number> = { urgent: 0, high: 1, normal: 2, low: 3 }
const GOAL_STATUS_RANK: Record<CofounderGoal['status'], number> = { active: 0, proposed: 1, achieved: 2, abandoned: 3 }

export interface PlanProjectNode {
  project: CofounderPlanProject
  /** done / total, always from the server's counts. */
  done: number
  total: number
  tasks: CofounderPlanTask[]
  hiddenTasks: number
}

export interface PlanGoalNode {
  /** null for the group of projects that belong to no (known) goal. */
  goal: CofounderGoal | null
  projects: PlanProjectNode[]
}

/** Tasks listed under a project before "and N more". */
export const PLAN_TASKS_SHOWN = 5

/**
 * Goals -> projects -> open tasks from the flat summary. Active goals first (then by priority), every
 * current goal appears even without projects, and projects whose goal is not in the summary are grouped
 * at the end so nothing the AI Co-founder planned is silently dropped.
 */
export function buildPlanTree(summary: Pick<CofounderSummary, 'goals' | 'plan'>, taskLimit: number = PLAN_TASKS_SHOWN): PlanGoalNode[] {
  const projects = summary.plan?.projects ?? []
  const toNode = (p: CofounderPlanProject): PlanProjectNode => {
    const tasks = p.open_tasks ?? []
    return { project: p, done: p.tasks_done ?? 0, total: p.tasks_total ?? 0, tasks: tasks.slice(0, taskLimit), hiddenTasks: Math.max(0, tasks.length - taskLimit) }
  }
  const goals = [...(summary.goals ?? [])].sort((a, b) => (GOAL_STATUS_RANK[a.status] ?? 9) - (GOAL_STATUS_RANK[b.status] ?? 9) || (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9))
  const known = new Set(goals.map((g) => g.id))
  const out: PlanGoalNode[] = goals.map((g) => ({ goal: g, projects: projects.filter((p) => p.goal_id === g.id).map(toNode) }))
  const loose = projects.filter((p) => !p.goal_id || !known.has(p.goal_id))
  if (loose.length > 0) out.push({ goal: null, projects: loose.map(toNode) })
  return out
}

/** What the "Wake now" button reports back. Never claims more than the server said. */
export function wakeResultText(r: CofounderWakeResult): string {
  switch (r.status) {
    case 'queued':
      return 'Wake queued. The AI Co-founder will start a planning run shortly.'
    case 'coalesced':
      return 'A planning run was already waiting, so nothing new was queued.'
    default:
      return `The server answered: ${r.status}.`
  }
}

export function planTotals(nodes: PlanGoalNode[]): { goals: number; projects: number; openTasks: number } {
  let projects = 0
  let openTasks = 0
  for (const n of nodes) {
    projects += n.projects.length
    for (const p of n.projects) openTasks += p.tasks.length + p.hiddenTasks
  }
  return { goals: nodes.filter((n) => n.goal).length, projects, openTasks }
}
