import type { ApprovalKind, AutonomyMode, CompanyPolicy, ModelRouting, RoleFamily, UpdatePolicyRequest } from '@/api/types'
import { microsToUsdString, parseBudgetInput } from '@/lib/money'

export const APPROVAL_KINDS: Array<{ kind: ApprovalKind; label: string; hint: string }> = [
  { kind: 'external_purchase', label: 'External purchases', hint: 'Spending real money with a third party.' },
  { kind: 'budget_increase', label: 'Budget increases', hint: 'Raising a budget above its current limit.' },
  { kind: 'external_communication', label: 'External communication', hint: 'Email, posts or messages sent outside the company.' },
  { kind: 'production_deploy', label: 'Production deploys', hint: 'Releasing to real users.' },
  { kind: 'sensitive_action', label: 'Sensitive actions', hint: 'Anything an agent flags as high-risk.' },
  { kind: 'hire', label: 'Hires', hint: 'Adding employees to the company.' },
  { kind: 'other', label: 'Other', hint: 'Approvals that fit no other category.' },
]

export type LimitKey =
  | 'max_concurrent_runs'
  | 'max_delegation_depth'
  | 'max_tasks_per_planning_cycle'
  | 'max_attempts_per_task'
  | 'max_revisions'
  | 'max_run_seconds'
  | 'max_headcount'
  | 'heartbeat_interval_seconds'

export const LIMIT_FIELDS: Array<{ key: LimitKey; label: string; hint: string; min: number }> = [
  { key: 'max_concurrent_runs', label: 'Max concurrent runs', hint: 'Agent runs executing at the same time.', min: 1 },
  { key: 'max_delegation_depth', label: 'Max delegation depth', hint: 'How many levels of sub-tasks agents may create.', min: 0 },
  { key: 'max_tasks_per_planning_cycle', label: 'Max tasks per planning cycle', hint: 'Tasks one run may create.', min: 1 },
  { key: 'max_attempts_per_task', label: 'Max attempts per task', hint: 'Retries before a task fails.', min: 1 },
  { key: 'max_revisions', label: 'Max revisions', hint: 'Review rounds before a task fails.', min: 0 },
  { key: 'max_run_seconds', label: 'Max run time (seconds)', hint: 'A run is stopped after this long.', min: 1 },
  { key: 'max_headcount', label: 'Max headcount', hint: 'Employees the company may have.', min: 1 },
  { key: 'heartbeat_interval_seconds', label: 'AI Co-founder heartbeat (seconds)', hint: 'How often the AI Co-founder wakes to plan.', min: 1 },
]

/* ------------------------------ Milestone 6 fields ------------------------------ */

export type M6Key =
  | 'max_agent_monthly_budget_micros'
  | 'hire_default_monthly_budget_micros'
  | 'budget_alert_thresholds'
  | 'model_routing'
  | 'allow_paid_fallback'
  | 'auto_spend_limit_micros'
  | 'stall_seconds'

export const M6_KEYS: M6Key[] = [
  'max_agent_monthly_budget_micros',
  'hire_default_monthly_budget_micros',
  'auto_spend_limit_micros',
  'budget_alert_thresholds',
  'model_routing',
  'allow_paid_fallback',
  'stall_seconds',
]

export const MODEL_ROUTING_OPTIONS: Array<{ value: ModelRouting; label: string; hint: string }> = [
  { value: 'free_first', label: 'Free first', hint: 'Local and free models first; a paid model only when "paid fallback" below allows it.' },
  { value: 'pinned', label: 'Pinned', hint: 'Always the model set on each agent. No routing.' },
  { value: 'quality_first', label: 'Quality first', hint: 'The best model for the task, even when it costs money.' },
]

/**
 * M6 values as typed, only for the fields the backend actually reported. A field the policy response does not carry
 * is absent here, so the form hides it and the diff never sends it.
 */
export interface M6FormValues {
  max_agent_monthly_budget_micros?: string
  hire_default_monthly_budget_micros?: string
  auto_spend_limit_micros?: string
  /** Percentages, comma separated: "50, 80, 100". */
  budget_alert_thresholds?: string
  model_routing?: ModelRouting
  allow_paid_fallback?: boolean
  stall_seconds?: string
}

export const hasM6Fields = (m6: M6FormValues): boolean => Object.keys(m6).length > 0

export interface ThresholdParse {
  value: number[] | null
  error?: string
}

/** "50, 80 100" -> [50, 80, 100]. Percentages above 0 and at most 1000; sorted, unique. */
export function parseThresholds(input: string): ThresholdParse {
  const parts = input.split(/[\s,]+/).filter(Boolean)
  if (parts.length === 0) return { value: null, error: 'Enter at least one percentage, e.g. 50, 80, 100' }
  const out: number[] = []
  for (const p of parts) {
    const n = /^\d+(\.\d+)?$/.test(p.replace(/%$/, '')) ? Number(p.replace(/%$/, '')) : NaN
    if (!(n > 0 && n <= 1000)) return { value: null, error: `"${p}" is not a percentage between 0 and 1000` }
    out.push(n)
  }
  return { value: [...new Set(out)].sort((a, b) => a - b) }
}

function m6ToForm(p: CompanyPolicy): M6FormValues {
  const m: M6FormValues = {}
  if (p.max_agent_monthly_budget_micros !== undefined) m.max_agent_monthly_budget_micros = microsToUsdString(p.max_agent_monthly_budget_micros)
  if (p.hire_default_monthly_budget_micros !== undefined) m.hire_default_monthly_budget_micros = microsToUsdString(p.hire_default_monthly_budget_micros)
  if (p.auto_spend_limit_micros !== undefined) m.auto_spend_limit_micros = microsToUsdString(p.auto_spend_limit_micros)
  if (p.budget_alert_thresholds !== undefined) m.budget_alert_thresholds = p.budget_alert_thresholds.join(', ')
  if (p.model_routing !== undefined) m.model_routing = p.model_routing
  if (p.allow_paid_fallback !== undefined) m.allow_paid_fallback = p.allow_paid_fallback
  if (p.stall_seconds !== undefined) m.stall_seconds = String(p.stall_seconds)
  return m
}

function validateM6(m: M6FormValues): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  const cap = m.max_agent_monthly_budget_micros !== undefined ? parseBudgetInput(m.max_agent_monthly_budget_micros) : null
  if (cap?.error) e.max_agent_monthly_budget_micros = cap.error
  const hire = m.hire_default_monthly_budget_micros !== undefined ? parseBudgetInput(m.hire_default_monthly_budget_micros) : null
  if (hire?.error) e.hire_default_monthly_budget_micros = hire.error
  if (cap?.value != null && hire?.value != null && hire.value > cap.value) e.hire_default_monthly_budget_micros = 'The default for new hires cannot exceed the per-agent cap'
  if (m.auto_spend_limit_micros !== undefined) {
    const raw = m.auto_spend_limit_micros.trim()
    const spend = parseBudgetInput(raw)
    if (raw === '' || spend.error) e.auto_spend_limit_micros = spend.error ?? 'Enter 0 to require a human for every purchase'
  }
  if (m.budget_alert_thresholds !== undefined) {
    const t = parseThresholds(m.budget_alert_thresholds)
    if (t.error) e.budget_alert_thresholds = t.error
  }
  if (m.model_routing !== undefined && !MODEL_ROUTING_OPTIONS.some((o) => o.value === m.model_routing)) e.model_routing = 'Choose a routing mode'
  if (m.stall_seconds !== undefined) {
    const raw = m.stall_seconds.trim()
    if (!/^\d+$/.test(raw) || Number(raw) < 1 || !Number.isSafeInteger(Number(raw))) e.stall_seconds = 'Enter a whole number of seconds, at least 1'
  }
  return e
}

function diffM6(m: M6FormValues, p: CompanyPolicy): UpdatePolicyRequest {
  const out: UpdatePolicyRequest = {}
  const money = (key: 'max_agent_monthly_budget_micros' | 'hire_default_monthly_budget_micros') => {
    const raw = m[key]
    if (raw === undefined || p[key] === undefined) return
    const v = parseBudgetInput(raw).value
    if (v !== p[key]) out[key] = v
  }
  money('max_agent_monthly_budget_micros')
  money('hire_default_monthly_budget_micros')
  if (m.auto_spend_limit_micros !== undefined && p.auto_spend_limit_micros !== undefined) {
    const v = parseBudgetInput(m.auto_spend_limit_micros).value
    if (v !== null && v !== p.auto_spend_limit_micros) out.auto_spend_limit_micros = v
  }
  if (m.budget_alert_thresholds !== undefined && p.budget_alert_thresholds !== undefined) {
    const v = parseThresholds(m.budget_alert_thresholds).value
    if (v && !sameList(v.map(String), [...p.budget_alert_thresholds].sort((a, b) => a - b).map(String))) out.budget_alert_thresholds = v
  }
  if (m.model_routing !== undefined && p.model_routing !== undefined && m.model_routing !== p.model_routing) out.model_routing = m.model_routing
  if (m.allow_paid_fallback !== undefined && p.allow_paid_fallback !== undefined && m.allow_paid_fallback !== p.allow_paid_fallback) out.allow_paid_fallback = m.allow_paid_fallback
  if (m.stall_seconds !== undefined && p.stall_seconds !== undefined) {
    const n = Number(m.stall_seconds)
    if (Number.isFinite(n) && n !== p.stall_seconds) out.stall_seconds = n
  }
  return out
}

/* ------------------------------ Milestone 12 fields ------------------------------ */

/** The role families the backend accepts in `cli_role_families` (the `RoleFamily` union), in the order they are listed. */
export const ROLE_FAMILIES: Array<{ value: RoleFamily; label: string }> = [
  { value: 'founder', label: 'Founder' },
  { value: 'hr', label: 'HR' },
  { value: 'engineering', label: 'Engineering' },
  { value: 'design', label: 'Design' },
  { value: 'marketing', label: 'Marketing' },
  { value: 'sales', label: 'Sales' },
  { value: 'customer_success', label: 'Customer success' },
  { value: 'research', label: 'Research' },
  { value: 'qa', label: 'QA' },
  { value: 'operations', label: 'Operations' },
  { value: 'finance', label: 'Finance' },
  { value: 'generic', label: 'Generic' },
]

export type M12NumberKey =
  | 'management_reserved_slots'
  | 'max_parallel_runs_per_agent'
  | 'max_work_tasks_per_project'
  | 'max_subtasks_per_task'
  | 'circuit_breaker_cooldown_seconds'
  | 'initiative_interval_seconds'

/** Whole-number M12 policy fields with the bounds the backend enforces (`schemas/policy.py`). */
export const M12_NUMBER_FIELDS: Array<{ key: M12NumberKey; label: string; hint: string; min: number; max: number }> = [
  { key: 'management_reserved_slots', label: 'Slots reserved for management', hint: 'Of the max concurrent runs, how many work runs may never use, so planning and reviews are not starved. Must be lower than max concurrent runs.', min: 0, max: 99 },
  { key: 'max_parallel_runs_per_agent', label: 'Parallel runs per agent', hint: 'How many runs one agent may have at once (coding CLI work tasks inside a project only).', min: 1, max: 10 },
  { key: 'max_work_tasks_per_project', label: 'Max work tasks per project', hint: 'Work tasks agents may create in one project.', min: 1, max: 200 },
  { key: 'max_subtasks_per_task', label: 'Max subtasks per task', hint: 'Child tasks one parent task may have.', min: 1, max: 50 },
  { key: 'circuit_breaker_cooldown_seconds', label: 'Circuit breaker cooldown (seconds)', hint: 'A company paused by the circuit breaker resumes itself after this long. 0 means only a human resumes it.', min: 0, max: 604_800 },
  { key: 'initiative_interval_seconds', label: 'Initiative interval (seconds)', hint: 'How long the company must be idle before the AI Co-founder is asked to start new work.', min: 600, max: 2_592_000 },
]

/**
 * M12 values as typed, only for the fields the backend reported (same rule as M6). `cli_role_families` is `null` for "every
 * role family" (the default) or the chosen families.
 */
export interface M12FormValues extends Partial<Record<M12NumberKey, string>> {
  cli_role_families?: RoleFamily[] | null
  cli_run_reservation_micros?: string
  initiative_enabled?: boolean
  project_auto_complete?: boolean
}

export const hasM12Fields = (m12: M12FormValues): boolean => Object.keys(m12).length > 0

function m12ToForm(p: CompanyPolicy): M12FormValues {
  const m: M12FormValues = {}
  if (p.cli_role_families !== undefined) m.cli_role_families = p.cli_role_families === null ? null : [...p.cli_role_families]
  if (p.cli_run_reservation_micros !== undefined) m.cli_run_reservation_micros = microsToUsdString(p.cli_run_reservation_micros)
  for (const f of M12_NUMBER_FIELDS) if (p[f.key] !== undefined) m[f.key] = String(p[f.key])
  if (p.initiative_enabled !== undefined) m.initiative_enabled = p.initiative_enabled
  if (p.project_auto_complete !== undefined) m.project_auto_complete = p.project_auto_complete
  return m
}

function validateM12(m: M12FormValues, limits: Record<LimitKey, string>, saved?: CompanyPolicy): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  if (m.cli_run_reservation_micros !== undefined) {
    const r = parseBudgetInput(m.cli_run_reservation_micros)
    if (r.error || r.value === null) e.cli_run_reservation_micros = r.error ?? 'A reservation per coding CLI run is required'
  }
  for (const f of M12_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const t = raw.trim()
    if (!/^\d+$/.test(t) || !Number.isSafeInteger(Number(t))) e[f.key] = 'Enter a whole number'
    else if (Number(t) < f.min) e[f.key] = `At least ${f.min}`
    else if (Number(t) > f.max) e[f.key] = `At most ${f.max.toLocaleString('en-US')}`
  }
  // The server refuses a reservation that leaves no slot for work, but only when the reservation itself is being changed.
  const slots = m.management_reserved_slots
  const maxRuns = Number(limits.max_concurrent_runs)
  if (!e.management_reserved_slots && slots !== undefined && Number.isFinite(maxRuns) && /^\d+$/.test(limits.max_concurrent_runs.trim()) && Number(slots) >= maxRuns) {
    if (!saved || saved.management_reserved_slots === undefined || Number(slots) !== saved.management_reserved_slots) e.management_reserved_slots = 'Must be lower than max concurrent runs'
  }
  return e
}

function diffM12(m: M12FormValues, p: CompanyPolicy): UpdatePolicyRequest {
  const out: UpdatePolicyRequest = {}
  if (m.cli_role_families !== undefined && p.cli_role_families !== undefined) {
    const a = m.cli_role_families
    const b = p.cli_role_families
    if (a === null || b === null ? a !== b : !sameSet(a, b)) out.cli_role_families = a === null ? null : [...a]
  }
  if (m.cli_run_reservation_micros !== undefined && p.cli_run_reservation_micros !== undefined) {
    const v = parseBudgetInput(m.cli_run_reservation_micros).value
    if (v !== null && v !== p.cli_run_reservation_micros) out.cli_run_reservation_micros = v
  }
  for (const f of M12_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined || p[f.key] === undefined) continue
    const n = Number(raw)
    if (Number.isFinite(n) && n !== p[f.key]) out[f.key] = n
  }
  if (m.initiative_enabled !== undefined && p.initiative_enabled !== undefined && m.initiative_enabled !== p.initiative_enabled) out.initiative_enabled = m.initiative_enabled
  if (m.project_auto_complete !== undefined && p.project_auto_complete !== undefined && m.project_auto_complete !== p.project_auto_complete) out.project_auto_complete = m.project_auto_complete
  return out
}

export interface PolicyFormValues {
  autonomy_mode: AutonomyMode
  human_approval_required_for: ApprovalKind[]
  daily_budget: string
  monthly_budget: string
  default_run_reservation: string
  limits: Record<LimitKey, string>
  allowed_model_providers: string[]
  m6: M6FormValues
  m12: M12FormValues
}

export type PolicyFormErrors = Partial<Record<string, string>>

export function policyToForm(p: CompanyPolicy): PolicyFormValues {
  const limits = {} as Record<LimitKey, string>
  for (const f of LIMIT_FIELDS) limits[f.key] = String(p[f.key])
  return {
    autonomy_mode: p.autonomy_mode,
    human_approval_required_for: [...p.human_approval_required_for],
    daily_budget: microsToUsdString(p.daily_budget_micros),
    monthly_budget: microsToUsdString(p.monthly_budget_micros),
    default_run_reservation: microsToUsdString(p.default_run_reservation_micros),
    limits,
    allowed_model_providers: [...p.allowed_model_providers],
    m6: m6ToForm(p),
    m12: m12ToForm(p),
  }
}

/** `saved` lets cross-field rules apply only to what the person is actually changing, as the server does. */
export function validatePolicyForm(v: PolicyFormValues, saved?: CompanyPolicy): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  const d = parseBudgetInput(v.daily_budget)
  if (d.error) e.daily_budget = d.error
  const m = parseBudgetInput(v.monthly_budget)
  if (m.error) e.monthly_budget = m.error
  if (d.value !== null && m.value !== null && d.value > m.value) e.daily_budget = 'The daily budget cannot exceed the monthly budget'
  const r = parseBudgetInput(v.default_run_reservation)
  if (r.error || r.value === null) e.default_run_reservation = r.error ?? 'A reservation per run is required'
  for (const f of LIMIT_FIELDS) {
    const raw = v.limits[f.key].trim()
    if (!/^\d+$/.test(raw)) e[f.key] = 'Enter a whole number'
    else if (Number(raw) < f.min) e[f.key] = `At least ${f.min}`
    else if (!Number.isSafeInteger(Number(raw))) e[f.key] = 'Too large'
  }
  return { ...e, ...validateM6(v.m6), ...validateM12(v.m12, v.limits, saved) }
}

const sameSet = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join('|') === [...b].sort().join('|')
const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i])

/** Only the fields that changed, in API shape (money as micros). Empty object = nothing to save. */
export function diffPolicy(v: PolicyFormValues, p: CompanyPolicy): UpdatePolicyRequest {
  const out: UpdatePolicyRequest = {}
  if (v.autonomy_mode !== p.autonomy_mode) out.autonomy_mode = v.autonomy_mode
  if (!sameSet(v.human_approval_required_for, p.human_approval_required_for)) out.human_approval_required_for = v.human_approval_required_for
  const daily = parseBudgetInput(v.daily_budget).value
  if (daily !== p.daily_budget_micros) out.daily_budget_micros = daily
  const monthly = parseBudgetInput(v.monthly_budget).value
  if (monthly !== p.monthly_budget_micros) out.monthly_budget_micros = monthly
  const reservation = parseBudgetInput(v.default_run_reservation).value
  if (reservation !== null && reservation !== p.default_run_reservation_micros) out.default_run_reservation_micros = reservation
  for (const f of LIMIT_FIELDS) {
    const n = Number(v.limits[f.key])
    if (Number.isFinite(n) && n !== p[f.key]) out[f.key] = n
  }
  const providers = v.allowed_model_providers.map((s) => s.trim()).filter(Boolean)
  if (!sameList(providers, p.allowed_model_providers)) out.allowed_model_providers = providers
  return { ...out, ...diffM6(v.m6, p), ...diffM12(v.m12, p) }
}
