import type { Agent, AgentKind } from '@/api/types'

export type OrgIssue = 'no_manager' | 'missing_manager' | 'cycle'

export interface OrgNode {
  agent: Agent
  children: OrgNode[]
  depth: number
  /** Set when the structure around this node is inconsistent; the node is still shown. */
  issue?: OrgIssue
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
    const node: OrgNode = { agent, depth, children: [], issue }
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

export function flattenOrgTree(nodes: OrgNode[]): OrgNode[] {
  const out: OrgNode[] = []
  const walk = (n: OrgNode) => {
    out.push(n)
    n.children.forEach(walk)
  }
  nodes.forEach(walk)
  return out
}
