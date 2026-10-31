import type { Agent, Task } from '@/api/types'
import { RING_STYLE, ringStateFor } from './liveState'
import type { OrgPerson } from './orgTree'

export interface OrgListRow {
  id: string
  person: OrgPerson
  /** Reporting levels below the Founder's direct reports: 0 for those, one more per manager above. */
  indent: number
}

/**
 * The organization as dense rows grouped by manager: a person, then their team indented one level, depth first. The order is the
 * tree's own (Co-founder first, people before agents at the same level), so the list and the map read the same way.
 */
export function orgListRows(roots: readonly OrgPerson[]): OrgListRow[] {
  const out: OrgListRow[] = []
  const walk = (p: OrgPerson, indent: number) => {
    out.push({ id: p.id, person: p, indent })
    p.children.forEach((c) => walk(c, indent + 1))
  }
  roots.forEach((r) => walk(r, 0))
  return out
}

/** One line of what an agent is doing: "Working on <task>", "Idle", "Paused", ... Never invents activity. */
export function agentStateText(agent: Pick<Agent, 'status' | 'live'>, taskTitle?: string | null): string {
  const state = ringStateFor(agent)
  if (state === 'working') {
    const title = taskTitle ?? agent.live?.current_task?.title ?? null
    return title ? `Working on ${title}` : (agent.live?.summary ?? 'Working')
  }
  return RING_STYLE[state].label
}

/** The title of the task an agent is on now, from the live block or the task list. */
export function currentTaskTitle(agent: Pick<Agent, 'live'>, tasks: readonly Task[] | undefined): string | null {
  const ref = agent.live?.current_task
  if (!ref) return null
  return ref.title ?? tasks?.find((t) => t.id === ref.id)?.title ?? null
}
