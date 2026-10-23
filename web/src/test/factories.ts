import type { Agent, Alert, ClientNote, CofounderSummary, Company, MemoryEntry, NoteReply, Overview, RoleTemplate, Skill, SkillDetail, Task } from '@/api/types'

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

export function makeTask(over: Partial<Task> = {}): Task {
  const id = over.id ?? crypto.randomUUID()
  return {
    id,
    company_id: 'c1',
    project_id: null,
    parent_task_id: null,
    title: `Task ${id.slice(0, 4)}`,
    description: null,
    acceptance_criteria: null,
    kind: 'work',
    status: 'backlog',
    priority: 'normal',
    assignee_agent_id: null,
    reviewer_agent_id: null,
    required_skills: [],
    budget_micros: null,
    attempt_count: 0,
    revision_count: 0,
    delegation_depth: 0,
    due_at: null,
    started_at: null,
    completed_at: null,
    result_summary: null,
    error: null,
    input_refs: [],
    output_refs: [],
    created_by_type: 'human',
    created_by_id: null,
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:00:00Z',
    ...over,
  }
}

/** Route table for a fake backend: unknown paths 404 like a backend that has not shipped that endpoint. */
export function mockApi(routes: Record<string, unknown | ((url: URL, init?: RequestInit) => Response)>) {
  const calls: Array<{ method: string; path: string; body: unknown }> = []
  const impl = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost')
    const path = url.pathname.replace(/^\/api/, '')
    calls.push({ method: init?.method ?? 'GET', path, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = `${init?.method ?? 'GET'} ${path}`
    const hit = routes[key] ?? (init?.method && init.method !== 'GET' ? undefined : routes[path])
    if (hit === undefined) return jsonResponse({ error: { code: 'not_found', message: 'Not found', details: null } }, 404)
    return typeof hit === 'function' ? (hit as (u: URL, i?: RequestInit) => Response)(url, init) : jsonResponse(hit)
  }
  return { impl, calls }
}

export const page = <T,>(items: T[]) => ({ items, next_cursor: null })

export function makeSkill(over: Partial<Skill> = {}): Skill {
  const id = over.id ?? crypto.randomUUID()
  const slug = over.slug ?? `skill-${id.slice(0, 4)}`
  return {
    id,
    company_id: 'c1',
    slug,
    name: slug,
    description: `Does ${slug} things`,
    category: null,
    tags: [],
    version: '1.0.0',
    author: null,
    license: 'MIT',
    source_kind: 'hermes_bundled',
    source_ref: `hermes@abc:skills/${slug}`,
    blob_sha256: 'a'.repeat(64),
    trust: 'builtin',
    scan_verdict: 'safe',
    status: 'active',
    requires_toolsets: [],
    required_env: [],
    created_by_type: 'system',
    created_by_id: null,
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:00:00Z',
    ...over,
  }
}

export function makeSkillDetail(over: Partial<SkillDetail> = {}): SkillDetail {
  return {
    ...makeSkill(over),
    scan_report: { verdict: 'safe', scanner_version: '1', summary: 'ok', finding_count: 0, findings: [] },
    frontmatter: { name: 'x' },
    files: [{ path: 'SKILL.md', size_bytes: 120 }],
    assigned_agents: [],
    ...over,
  }
}

export function makeTemplate(over: Partial<RoleTemplate> = {}): RoleTemplate {
  return {
    slug: 'software-engineer',
    family: 'engineering',
    title: 'Software Engineer',
    description: 'Builds and ships product code.',
    default_kind: 'specialist',
    default_department: 'Engineering',
    instructions: 'Write clean code.',
    default_toolsets: ['file', 'terminal'],
    default_skill_slugs: ['plan', 'debugging'],
    required_skill_tags: [],
    builtin: true,
    id: null,
    ...over,
  }
}

/* ---------------------------------- M5 ---------------------------------- */

export function makeNote(over: Partial<ClientNote> = {}): ClientNote {
  const id = over.id ?? crypto.randomUUID()
  return {
    id,
    company_id: 'c1',
    author_user_id: 'u1',
    body: `Note ${id.slice(0, 4)}`,
    attachments: [],
    status: 'new',
    triage: null,
    triaged_by_run_id: null,
    pinned: false,
    created_at: '2026-10-24T09:00:00Z',
    updated_at: '2026-10-24T09:00:00Z',
    ...over,
  }
}

export function makeReply(over: Partial<NoteReply> = {}): NoteReply {
  return { id: crypto.randomUUID(), note_id: 'n1', author_type: 'agent', author_id: 'f1', body: 'Done.', created_at: '2026-10-24T09:05:00Z', ...over }
}

export function makeAlert(over: Partial<Alert> = {}): Alert {
  return {
    id: crypto.randomUUID(),
    company_id: 'c1',
    severity: 'warning',
    kind: 'budget_threshold',
    title: 'Budget at 80%',
    body: null,
    status: 'open',
    related_type: null,
    related_id: null,
    created_at: '2026-10-24T09:00:00Z',
    ...over,
  }
}

export function makeMemory(over: Partial<MemoryEntry> = {}): MemoryEntry {
  return {
    id: crypto.randomUUID(),
    company_id: 'c1',
    kind: 'fact',
    title: null,
    body: 'We sell to dentists.',
    status: 'verified',
    source: null,
    tags: [],
    proposed_by_type: null,
    proposed_by_id: null,
    verified_by_type: null,
    verified_by_id: null,
    verified_at: null,
    created_at: '2026-10-24T09:00:00Z',
    updated_at: '2026-10-24T09:00:00Z',
    ...over,
  }
}

export function makeSummary(over: Partial<CofounderSummary> = {}): CofounderSummary {
  return {
    agent_id: 'f1',
    goals: [],
    plan: { summary: null, updated_at: null, projects: [] },
    decisions: [],
    blockers: [],
    alerts: [],
    last_run: null,
    wake_pending: false,
    unprocessed_notes: 0,
    ...over,
  }
}
