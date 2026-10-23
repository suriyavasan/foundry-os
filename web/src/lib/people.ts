import type { Agent, Member, Task } from '@/api/types'
import { isTerminalTask } from './taskStatus'

/**
 * References to people: an AI agent or a human member (docs/specs/m7 §3: `manager_ref` is `agent:<id>` or
 * `member:<id>`; tasks carry `assignee_ref` the same way). Agents keep their old `*_agent_id` fields too.
 */
export type PersonKind = 'agent' | 'member'
export interface PersonRef {
  type: PersonKind
  id: string
}

export const agentRef = (id: string): string => `agent:${id}`
export const memberRef = (id: string): string => `member:${id}`

const REF_RE = /^(agent|member):([A-Za-z0-9_-]{1,64})$/

export function parsePersonRef(ref: string | null | undefined): PersonRef | null {
  const m = ref ? REF_RE.exec(ref) : null
  return m ? { type: m[1] as PersonKind, id: m[2]! } : null
}

/** Who a task is assigned to: `assignee_ref` when the backend sends it, else the agent id. */
export function taskAssignee(task: Pick<Task, 'assignee_ref' | 'assignee_agent_id'>): PersonRef | null {
  return parsePersonRef(task.assignee_ref) ?? (task.assignee_agent_id ? { type: 'agent', id: task.assignee_agent_id } : null)
}

/** Open (not finished) tasks assigned to one member, oldest first. */
export function openTasksOfMember(tasks: readonly Task[], memberId: string): Task[] {
  return tasks
    .filter((t) => !isTerminalTask(t.status) && taskAssignee(t)?.type === 'member' && taskAssignee(t)?.id === memberId)
    .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0))
}

/** Members who can be given work or placed in the chart: active people with more than read-only access. */
export const isWorkingMember = (m: Member): boolean => m.status === 'active' && m.role !== 'viewer'

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const first = [...parts[0]!][0] ?? ''
  const last = parts.length > 1 ? ([...parts[parts.length - 1]!][0] ?? '') : ([...parts[0]!][1] ?? '')
  return (first + last).toUpperCase()
}

export const MEMBER_ROLES = ['owner', 'admin', 'member', 'viewer'] as const
export const ROLE_LABEL: Record<(typeof MEMBER_ROLES)[number], string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer',
}
export const ROLE_HELP: Record<(typeof MEMBER_ROLES)[number], string> = {
  owner: 'Full control, including billing-level settings and other owners.',
  admin: 'Manages the team, agents, policy and approvals.',
  member: 'Works inside the company: takes tasks, reviews and approvals assigned to them.',
  viewer: 'Read-only.',
}
export const roleLabel = (role: string): string => (ROLE_LABEL as Record<string, string>)[role] ?? role

/** Owners and admins manage people; the server enforces it, the UI just does not offer it to others. */
export const canManageTeam = (role: string | undefined): boolean => role === 'owner' || role === 'admin'

/** Every candidate manager for a person: agents and working members, as `{ref, label}` for a picker. */
export function managerOptions(agents: readonly Agent[], members: readonly Member[], exclude?: { memberId?: string }): Array<{ ref: string; label: string; group: 'agents' | 'members' }> {
  const out: Array<{ ref: string; label: string; group: 'agents' | 'members' }> = []
  for (const a of [...agents].filter((x) => x.status !== 'retired' && x.status !== 'rejected').sort((x, y) => x.name.localeCompare(y.name))) {
    out.push({ ref: agentRef(a.id), label: `${a.name} · ${a.role_title}`, group: 'agents' })
  }
  for (const m of [...members].filter(isWorkingMember).sort((x, y) => x.display_name.localeCompare(y.display_name))) {
    if (m.id === exclude?.memberId) continue
    out.push({ ref: memberRef(m.id), label: `${m.display_name}${m.title ? ` · ${m.title}` : ''}`, group: 'members' })
  }
  return out
}
