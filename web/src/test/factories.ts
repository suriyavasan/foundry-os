import type { Agent, Company, Overview } from '@/api/types'

export function makeAgent(over: Partial<Agent> = {}): Agent {
  const id = over.id ?? crypto.randomUUID()
  return {
    id,
    company_id: 'c1',
    name: `Agent ${id.slice(0, 4)}`,
    role_title: 'Specialist',
    kind: 'specialist',
    status: 'active',
    department_id: null,
    manager_agent_id: null,
    reports_to_human: false,
    responsibilities: null,
    instructions: null,
    model_provider: null,
    model_name: null,
    runtime: 'unassigned',
    runtime_config: {},
    tool_permissions: [],
    monthly_budget_micros: null,
    daily_budget_micros: null,
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:00:00Z',
    status_changed_at: '2026-10-23T12:00:00Z',
    ...over,
  }
}

export function makeCompany(over: Partial<Company> = {}): Company {
  return {
    id: 'c1',
    name: 'Acme Labs',
    slug: 'acme-labs',
    mission: null,
    vision: null,
    strategic_goal: 'Ship the thing',
    constraints: [],
    timezone: 'UTC',
    status: 'active',
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:00:00Z',
    ...over,
  }
}

export function makeOverview(over: Partial<Overview> = {}): Overview {
  return {
    company: makeCompany(),
    founder: makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', reports_to_human: true, role_title: 'Founder & CEO' }),
    agents: { total: 3, by_status: { proposed: 0, active: 2, paused: 1, retiring: 0, retired: 0, rejected: 0 } },
    departments: { total: 2 },
    tasks: { available: false, milestone: 'M3' },
    spending: { available: false, milestone: 'M6' },
    approvals: { available: false, milestone: 'M6' },
    artifacts: { available: false, milestone: 'M7' },
    routines: { available: false, milestone: 'M5' },
    recent_activity: [],
    ...over,
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
