import type { ApprovalKind, AutonomyMode, CompanyPolicy, ModelRouting, UpdatePolicyRequest } from '@/api/types'
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

export interface PolicyFormValues {
  autonomy_mode: AutonomyMode
  human_approval_required_for: ApprovalKind[]
  daily_budget: string
  monthly_budget: string
  default_run_reservation: string
  limits: Record<LimitKey, string>
  allowed_model_providers: string[]
  m6: M6FormValues
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
  }
}

export function validatePolicyForm(v: PolicyFormValues): PolicyFormErrors {
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
  return { ...e, ...validateM6(v.m6) }
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
  return { ...out, ...diffM6(v.m6, p) }
}
