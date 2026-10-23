import type { Agent, AgentKind, Member } from '@/api/types'
import type { OrgIssue, OrgPerson } from './orgTree'
import { isWorkingMember, memberRef, parsePersonRef } from './people'

/**
 * The reporting tree with humans in it (docs/specs/m7 §3). Agents and members both have a manager reference
 * (`agent:<id>` / `member:<id>`); an agent can report to a human and a human to an agent. The signed-in Founder is
 * the virtual "You" root of the chart, so a reference to their own member record resolves to that root.
 *
 *  - Agent: `reports_to_human` -> You; else `manager_ref`; else `manager_agent_id`.
 *  - Member: `manager_ref`; with none, a direct report of You.
 *  - A reference to someone who is not in the chart (retired, viewer, removed, unknown) is a flagged root, never lost.
 *  - A cycle is broken at its first member (by sort order), which becomes a flagged root.
 * Every agent and every shown member appears exactly once.
 */

const KIND_ORDER: Record<AgentKind, number> = { founder: 0, lead: 1, specialist: 2, reviewer: 3 }

/** Members sort right after the AI Co-founder: they are colleagues who usually run a team. */
const rank = (p: OrgPerson): number => (p.member ? 0.5 : (KIND_ORDER[p.agent!.kind] ?? 9))
const nameOf = (p: OrgPerson): string => (p.member ? p.member.display_name : p.agent!.name)

function compare(a: OrgPerson, b: OrgPerson): number {
  return rank(a) - rank(b) || nameOf(a).localeCompare(nameOf(b)) || a.id.localeCompare(b.id)
}

export interface PeopleOptions {
  /** The signed-in user: their own member record is the "You" root, not a separate node. */
  selfUserId?: string | null
}

type Parent = { to: 'root' } | { to: 'none' } | { to: 'missing' } | { to: 'node'; id: string }

export function buildPeopleTree(agents: Agent[], members: Member[], opts: PeopleOptions = {}): OrgPerson[] {
  const self = opts.selfUserId ? members.find((m) => m.user_id === opts.selfUserId) : undefined
  const shown = members.filter((m) => isWorkingMember(m) && m.id !== self?.id)

  const people = new Map<string, OrgPerson>()
  for (const a of agents) people.set(a.id, { id: a.id, agent: a, member: null, children: [], depth: 0 })
  for (const m of shown) people.set(memberRef(m.id), { id: memberRef(m.id), agent: null, member: m, children: [], depth: 0 })

  const resolve = (ref: string | null | undefined): Parent => {
    const r = parsePersonRef(ref)
    if (!r) return { to: 'none' }
    if (r.type === 'member') {
      if (self && r.id === self.id) return { to: 'root' }
      return people.has(memberRef(r.id)) ? { to: 'node', id: memberRef(r.id) } : { to: 'missing' }
    }
    return people.has(r.id) ? { to: 'node', id: r.id } : { to: 'missing' }
  }

  const parentOf = (p: OrgPerson): Parent => {
    if (p.member) {
      const ref = p.member.manager_ref
      return ref ? resolve(ref) : { to: 'root' }
    }
    const a = p.agent!
    if (a.reports_to_human) return { to: 'root' }
    const ref = a.manager_ref ?? (a.manager_agent_id ? `agent:${a.manager_agent_id}` : null)
    return resolve(ref)
  }

  const childrenOf = new Map<string, OrgPerson[]>()
  const roots: Array<{ p: OrgPerson; issue?: OrgIssue }> = []
  for (const p of people.values()) {
    const par = parentOf(p)
    if (par.to === 'root') roots.push({ p })
    else if (par.to === 'none') roots.push({ p, issue: 'no_manager' })
    else if (par.to === 'missing') roots.push({ p, issue: 'missing_manager' })
    else if (par.id === p.id) {
      // self-manager: handled by the cycle pass below
    } else {
      const list = childrenOf.get(par.id) ?? []
      list.push(p)
      childrenOf.set(par.id, list)
    }
  }

  const visited = new Set<string>()
  const build = (p: OrgPerson, depth: number, issue?: OrgIssue): OrgPerson => {
    visited.add(p.id)
    const node: OrgPerson = { id: p.id, agent: p.agent, member: p.member, depth, children: [], issue }
    for (const kid of (childrenOf.get(p.id) ?? []).filter((c) => !visited.has(c.id)).sort(compare)) {
      if (visited.has(kid.id)) continue
      node.children.push(build(kid, depth + 1))
    }
    return node
  }

  const issueRank = (r: { issue?: OrgIssue }) => (r.issue === undefined ? 0 : r.issue === 'no_manager' ? 1 : 2)
  roots.sort((a, b) => issueRank(a) - issueRank(b) || compare(a.p, b.p))
  const result = roots.map((r) => build(r.p, 0, r.issue))

  for (const p of [...people.values()].filter((x) => !visited.has(x.id)).sort(compare)) {
    if (visited.has(p.id)) continue
    result.push(build(p, 0, 'cycle'))
  }
  return result
}
