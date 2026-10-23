import type { Agent, AgentKind, CreateAgentRequest, UpdateAgentRequest } from '@/api/types'
import { microsToUsdString, parseBudgetInput } from '@/lib/money'

export const AGENT_LIMITS = { name: 120, roleTitle: 120 } as const

export const UNASSIGNED_RUNTIME = 'unassigned'

export interface AgentFormValues {
  name: string
  role_title: string
  kind: AgentKind
  department_id: string // '' = none
  manager_agent_id: string // '' = none
  responsibilities: string
  instructions: string
  model_provider: string
  model_name: string
  runtime: string
  monthly_budget: string // USD string, '' = no limit
  daily_budget: string
}

export type AgentFormErrors = Partial<Record<keyof AgentFormValues, string>>

export function emptyAgentForm(partial: Partial<AgentFormValues> = {}): AgentFormValues {
  return {
    name: '',
    role_title: '',
    kind: 'specialist',
    department_id: '',
    manager_agent_id: '',
    responsibilities: '',
    instructions: '',
    model_provider: '',
    model_name: '',
    runtime: UNASSIGNED_RUNTIME,
    monthly_budget: '',
    daily_budget: '',
    ...partial,
  }
}

export function agentToForm(a: Agent): AgentFormValues {
  return {
    name: a.name,
    role_title: a.role_title,
    kind: a.kind,
    department_id: a.department_id ?? '',
    manager_agent_id: a.manager_agent_id ?? '',
    responsibilities: a.responsibilities ?? '',
    instructions: a.instructions ?? '',
    model_provider: a.model_provider ?? '',
    model_name: a.model_name ?? '',
    runtime: a.runtime || UNASSIGNED_RUNTIME,
    monthly_budget: microsToUsdString(a.monthly_budget_micros),
    daily_budget: microsToUsdString(a.daily_budget_micros),
  }
}

export function validateAgentForm(v: AgentFormValues, opts: { budgets: boolean }): AgentFormErrors {
  const e: AgentFormErrors = {}
  const name = v.name.trim()
  if (!name) e.name = 'Name is required'
  else if (name.length > AGENT_LIMITS.name) e.name = `At most ${AGENT_LIMITS.name} characters`
  const role = v.role_title.trim()
  if (!role) e.role_title = 'Role title is required'
  else if (role.length > AGENT_LIMITS.roleTitle) e.role_title = `At most ${AGENT_LIMITS.roleTitle} characters`
  if (v.model_name.trim() && !v.model_provider.trim()) e.model_provider = 'Provider is required when a model is set'
  if (opts.budgets) {
    const m = parseBudgetInput(v.monthly_budget)
    if (m.error) e.monthly_budget = m.error
    const d = parseBudgetInput(v.daily_budget)
    if (d.error) e.daily_budget = d.error
  }
  return e
}

const opt = (s: string): string | null => {
  const t = s.trim()
  return t === '' ? null : t
}

/** Fields shared by create and the Configuration tab (no budgets / kind / status). */
function commonFields(v: AgentFormValues) {
  return {
    name: v.name.trim(),
    role_title: v.role_title.trim(),
    department_id: v.department_id || null,
    manager_agent_id: v.manager_agent_id || null,
    responsibilities: opt(v.responsibilities),
    instructions: opt(v.instructions),
    model_provider: opt(v.model_provider),
    model_name: opt(v.model_name),
    runtime: v.runtime || UNASSIGNED_RUNTIME,
  }
}

export function toCreateAgentBody(v: AgentFormValues): CreateAgentRequest {
  return {
    ...commonFields(v),
    kind: v.kind,
    monthly_budget_micros: parseBudgetInput(v.monthly_budget).value,
    daily_budget_micros: parseBudgetInput(v.daily_budget).value,
  }
}

export function toUpdateAgentBody(v: AgentFormValues): UpdateAgentRequest {
  return commonFields(v)
}

/** Map server field paths to form fields. */
export function mapAgentServerErrors(fields: Record<string, string>): AgentFormErrors {
  const alias: Record<string, keyof AgentFormValues> = {
    monthly_budget_micros: 'monthly_budget',
    daily_budget_micros: 'daily_budget',
  }
  const known = new Set<string>(Object.keys(emptyAgentForm()))
  const out: AgentFormErrors = {}
  for (const [k, msg] of Object.entries(fields)) {
    const key = alias[k] ?? (known.has(k) ? (k as keyof AgentFormValues) : undefined)
    if (key) out[key] = msg
  }
  return out
}

/** Agents that can be chosen as a manager: not the agent itself, not retired/rejected. */
export function managerCandidates(agents: Agent[], selfId?: string): Agent[] {
  return agents
    .filter((a) => a.id !== selfId && a.status !== 'retired' && a.status !== 'rejected')
    .sort((a, b) => a.name.localeCompare(b.name))
}
