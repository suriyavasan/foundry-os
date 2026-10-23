import type { ApprovalKind, AutonomyMode, CompanyPolicy, UpdatePolicyRequest } from '@/api/types'
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
  { key: 'heartbeat_interval_seconds', label: 'Founder heartbeat (seconds)', hint: 'How often the AI Founder wakes to plan.', min: 1 },
]

export interface PolicyFormValues {
  autonomy_mode: AutonomyMode
  human_approval_required_for: ApprovalKind[]
  daily_budget: string
  monthly_budget: string
  default_run_reservation: string
  limits: Record<LimitKey, string>
  allowed_model_providers: string[]
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
  return e
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
  return out
}
