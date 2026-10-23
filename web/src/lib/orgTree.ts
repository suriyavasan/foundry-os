import type { Agent, AgentKind, Member } from '@/api/types'

export type OrgIssue = 'no_manager' | 'missing_manager' | 'cycle'

/**
 * One person in the chart: an AI agent, or (M7) a human member. `id` is the agent's id, or `member:<id>` for a
 * human so the two namespaces never collide. Exactly one of `agent` / `member` is set.
 */
export interface OrgPerson {
  id: string
  agent: Agent | null
  member?: Member | null
  children: OrgPerson[]
  depth: number
  /** Set when the structure around this node is inconsistent; the node is still shown. */
  issue?: OrgIssue
}

/** A node of the agents-only chart (the shape before M7, still used when there are no human members). */
export interface OrgNode extends OrgPerson {
  agent: Agent
  children: OrgNode[]
}

const KIND_ORDER: Record<AgentKind, number> = { founder: 0, lead: 1, specialist: 2, reviewer: 3 }

function compareAgents(a: Agent, b: Agent): number {
  const k = (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9)
  if (k !== 0) return k
  return a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
}

/**
 * Build the reporting tree from a flat agent list.
 *
 * Roots are agents that report to the human, then (flagged) agents without a manager, then
 * (flagged) agents whose manager is not in the list. Agents caught in a manager cycle (including
 * self-management) are never lost or looped: the cycle is broken at its first member (by sort order),
 * which becomes a flagged root. Every input agent appears exactly once in the output.
 */
export function buildOrgTree(agents: Agent[]): OrgNode[] {
  const byId = new Map(agents.map((a) => [a.id, a]))
  const childrenOf = new Map<string, Agent[]>()
  const roots: Array<{ agent: Agent; issue?: OrgIssue }> = []

  for (const a of agents) {
    if (a.reports_to_human) {
      roots.push({ agent: a })
    } else if (!a.manager_agent_id) {
      roots.push({ agent: a, issue: 'no_manager' })
    } else if (!byId.has(a.manager_agent_id)) {
      roots.push({ agent: a, issue: 'missing_manager' })
    } else if (a.manager_agent_id === a.id) {
      // self-manager: handled by the cycle pass below
    } else {
      const list = childrenOf.get(a.manager_agent_id) ?? []
      list.push(a)
      childrenOf.set(a.manager_agent_id, list)
    }
  }

  const visited = new Set<string>()

  const build = (agent: Agent, depth: number, issue?: OrgIssue): OrgNode => {
    visited.add(agent.id)
    const kids = (childrenOf.get(agent.id) ?? []).filter((c) => !visited.has(c.id)).sort(compareAgents)
    const node: OrgNode = { id: agent.id, agent, depth, children: [], issue }
    for (const kid of kids) {
      if (visited.has(kid.id)) continue
      node.children.push(build(kid, depth + 1))
    }
    return node
  }

  const rank = (r: { agent: Agent; issue?: OrgIssue }) => (r.issue === undefined ? 0 : r.issue === 'no_manager' ? 1 : 2)
  roots.sort((a, b) => rank(a) - rank(b) || compareAgents(a.agent, b.agent))

  const result = roots.map((r) => build(r.agent, 0, r.issue))

  // Anything still unvisited sits in a manager cycle (or hangs off one).
  const leftover = agents.filter((a) => !visited.has(a.id)).sort(compareAgents)
  for (const a of leftover) {
    if (visited.has(a.id)) continue
    result.push(build(a, 0, 'cycle'))
  }
  return result
}

export function flattenOrgTree<N extends OrgPerson>(nodes: N[]): N[] {
  const out: N[] = []
  const walk = (n: N) => {
    out.push(n)
    ;(n.children as N[]).forEach(walk)
  }
  nodes.forEach(walk)
  return out
}

const INACTIVE = new Set(['retired', 'rejected'])

/**
 * Drop retired/rejected agents from a built tree for display. Their reports are lifted to the nearest
 * remaining ancestor (so a retired manager never orphans a live team), and depths are recomputed.
 */
export function pruneInactive<N extends OrgPerson>(roots: N[]): N[] {
  const walk = (n: N, depth: number): N[] => {
    const inactive = !!n.agent && INACTIVE.has(n.agent.status)
    const kids = (n.children as N[]).flatMap((c) => walk(c, inactive ? depth : depth + 1))
    if (inactive) return kids
    return [{ ...n, depth, children: kids }]
  }
  return roots.flatMap((r) => walk(r, 0))
}
