import type { ApprovalKind, AutonomyMode, CompanyPolicy, ModelRouting, RoleFamily, UpdatePolicyRequest } from '@/api/types'
import type { RadarPolicyFields } from '@/api/radar'
import type { PurchasePolicyFields } from '@/api/wallet'
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


/* ------------------------------ Milestone 15 fields ------------------------------ */

/**
 * The code ceilings the server enforces on the purchasing bands (`backend/app/models/policy.py`). They are validated here as
 * well so the form can say so before a save, and shown as helper text. The owner can still approve anything by hand.
 */
export const PURCHASE_CEILINGS_USD = { purchase_auto_approve_micros: 100, purchase_agent_auto_monthly_micros: 500, purchase_cofounder_limit_micros: 1000 } as const

export type M15MoneyKey =
  | 'purchase_auto_approve_micros'
  | 'purchase_agent_auto_monthly_micros'
  | 'purchase_cofounder_limit_micros'
  | 'purchase_cofounder_monthly_micros'
  | 'purchase_tolerance_min_micros'

export const M15_MONEY_FIELDS: Array<{ key: M15MoneyKey; label: string; hint: string; maxUsd?: number }> = [
  { key: 'purchase_auto_approve_micros', label: 'Approve automatically up to (USD per charge)', hint: 'Charges up to this need no one. 0 turns this band off. Ceiling $100.', maxUsd: 100 },
  { key: 'purchase_agent_auto_monthly_micros', label: 'Automatic total per agent and month (USD)', hint: 'Once an agent has had this much approved automatically in a month, the next one goes up a level. Ceiling $500.', maxUsd: 500 },
  { key: 'purchase_cofounder_limit_micros', label: 'AI Co-founder may approve up to (USD per charge)', hint: 'Above this a person decides. 0 turns this band off. Ceiling $1,000.', maxUsd: 1000 },
  { key: 'purchase_cofounder_monthly_micros', label: 'AI Co-founder total per month (USD)', hint: 'The most the AI Co-founder may approve in a month.' },
  { key: 'purchase_tolerance_min_micros', label: 'Tax and FX allowance, at least (USD)', hint: 'A card limit is the amount plus the larger of the percentage below and this. Up to $100.', maxUsd: 100 },
]

export type M15NumberKey = 'purchase_card_ttl_hours' | 'subscription_renewal_alert_days'
export const M15_NUMBER_FIELDS: Array<{ key: M15NumberKey; label: string; hint: string; min: number; max: number }> = [
  { key: 'purchase_card_ttl_hours', label: 'Unused card closes after (hours)', hint: 'A card nobody used in this time is closed, the purchase expires and the hold is released.', min: 1, max: 720 },
  { key: 'subscription_renewal_alert_days', label: 'Renewal review lead time (days)', hint: 'How many days before a renewal the owning agent is asked to review the subscription.', min: 1, max: 60 },
]

/** Purchasing values as typed, only for the fields the backend reported (same rule as M6 and M12). */
export interface M15FormValues extends Partial<Record<M15MoneyKey, string>>, Partial<Record<M15NumberKey, string>> {
  purchasing_enabled?: boolean
  /** Blank = no cap. */
  purchase_monthly_budget_micros?: string
  purchase_recurring_requires_human?: boolean
  purchase_new_vendor_requires_human?: boolean
  /** Percent with up to two decimals ("10" = 1000 basis points). */
  purchase_tolerance_percent?: string
  /** Member id, or '' for the owner. */
  purchase_checkout_member_id?: string
}

export const hasM15Fields = (m15: M15FormValues): boolean => Object.keys(m15).length > 0

const bpsToPercent = (bps: number): string => {
  const whole = Math.floor(bps / 100)
  const frac = String(bps % 100).padStart(2, '0').replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : String(whole)
}

function m15ToForm(src: CompanyPolicy): M15FormValues {
  const p = src as CompanyPolicy & PurchasePolicyFields
  const m: M15FormValues = {}
  if (p.purchasing_enabled !== undefined) m.purchasing_enabled = p.purchasing_enabled
  for (const f of M15_MONEY_FIELDS) {
    const v = p[f.key]
    if (v !== undefined) m[f.key] = microsToUsdString(v)
  }
  if (p.purchase_monthly_budget_micros !== undefined) m.purchase_monthly_budget_micros = microsToUsdString(p.purchase_monthly_budget_micros)
  if (p.purchase_recurring_requires_human !== undefined) m.purchase_recurring_requires_human = p.purchase_recurring_requires_human
  if (p.purchase_new_vendor_requires_human !== undefined) m.purchase_new_vendor_requires_human = p.purchase_new_vendor_requires_human
  if (p.purchase_tolerance_bps !== undefined) m.purchase_tolerance_percent = bpsToPercent(p.purchase_tolerance_bps)
  for (const f of M15_NUMBER_FIELDS) {
    const v = p[f.key]
    if (v !== undefined) m[f.key] = String(v)
  }
  if (p.purchase_checkout_member_id !== undefined) m.purchase_checkout_member_id = p.purchase_checkout_member_id ?? ''
  return m
}

const PERCENT_RE = /^\d{1,2}(\.\d{1,2})?$/
const percentToBps = (s: string): number | null => (PERCENT_RE.test(s.trim()) ? Math.round(Number(s.trim()) * 100) : null)

/** Shared by the Settings form and the setup wizard. */
export function validateM15(m: M15FormValues): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  for (const f of M15_MONEY_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const parsed = parseBudgetInput(raw)
    if (raw.trim() === '' || parsed.error || parsed.value === null) e[f.key] = parsed.error ?? 'Enter an amount. 0 turns the band off.'
    else if (f.maxUsd !== undefined && parsed.value > f.maxUsd * 1_000_000) e[f.key] = `At most $${f.maxUsd.toLocaleString('en-US')}`
  }
  if (m.purchase_monthly_budget_micros !== undefined) {
    const b = parseBudgetInput(m.purchase_monthly_budget_micros)
    if (b.error) e.purchase_monthly_budget_micros = b.error
  }
  if (m.purchase_tolerance_percent !== undefined) {
    const bps = percentToBps(m.purchase_tolerance_percent)
    if (bps === null || bps > 5000) e.purchase_tolerance_percent = 'Enter a percentage from 0 to 50, like 10'
  }
  for (const f of M15_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const t = raw.trim()
    if (!/^\d+$/.test(t) || !Number.isSafeInteger(Number(t))) e[f.key] = 'Enter a whole number'
    else if (Number(t) < f.min) e[f.key] = `At least ${f.min}`
    else if (Number(t) > f.max) e[f.key] = `At most ${f.max.toLocaleString('en-US')}`
  }
  return e
}

/** The PATCH body for what changed, or (without `saved`) the whole form. Money as micros, the percentage as basis points. */
export function diffM15(m: M15FormValues, saved?: CompanyPolicy): Partial<PurchasePolicyFields> {
  const p = saved as (CompanyPolicy & PurchasePolicyFields) | undefined
  const out: Partial<PurchasePolicyFields> = {}
  const changed = <T,>(next: T, prev: T | undefined) => !p || prev === undefined || next !== prev
  if (m.purchasing_enabled !== undefined && changed(m.purchasing_enabled, p?.purchasing_enabled)) out.purchasing_enabled = m.purchasing_enabled
  for (const f of M15_MONEY_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const v = parseBudgetInput(raw).value
    if (v !== null && changed(v, p?.[f.key])) out[f.key] = v
  }
  if (m.purchase_monthly_budget_micros !== undefined) {
    const v = parseBudgetInput(m.purchase_monthly_budget_micros).value
    if (!p || p.purchase_monthly_budget_micros === undefined ? true : v !== p.purchase_monthly_budget_micros) out.purchase_monthly_budget_micros = v
  }
  if (m.purchase_recurring_requires_human !== undefined && changed(m.purchase_recurring_requires_human, p?.purchase_recurring_requires_human)) out.purchase_recurring_requires_human = m.purchase_recurring_requires_human
  if (m.purchase_new_vendor_requires_human !== undefined && changed(m.purchase_new_vendor_requires_human, p?.purchase_new_vendor_requires_human)) out.purchase_new_vendor_requires_human = m.purchase_new_vendor_requires_human
  if (m.purchase_tolerance_percent !== undefined) {
    const bps = percentToBps(m.purchase_tolerance_percent)
    if (bps !== null && changed(bps, p?.purchase_tolerance_bps)) out.purchase_tolerance_bps = bps
  }
  for (const f of M15_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const n = Number(raw)
    if (Number.isFinite(n) && changed(n, p?.[f.key])) out[f.key] = n
  }
  if (m.purchase_checkout_member_id !== undefined) {
    const v = m.purchase_checkout_member_id === '' ? null : m.purchase_checkout_member_id
    if (!p || p.purchase_checkout_member_id === undefined ? true : v !== p.purchase_checkout_member_id) out.purchase_checkout_member_id = v
  }
  return out
}


/* ------------------------------ Milestone 16 fields (market radar) ------------------------------ */

export type M16BoolKey = 'radar_enabled' | 'self_critique_enabled' | 'radar_auto_accept' | 'radar_agent_sources'
export const M16_BOOL_FIELDS: Array<{ key: M16BoolKey; label: string; hint: string }> = [
  { key: 'radar_enabled', label: 'Read the outside world every day', hint: 'Master switch. Off: no sources are fetched and the radar routines stay quiet.' },
  { key: 'self_critique_enabled', label: 'Let the company criticise its own work every week', hint: 'A weekly review of what the company built, which turns into ideas.' },
  { key: 'radar_auto_accept', label: 'The AI Co-founder may accept ideas into work', hint: 'Off: every idea waits for you to accept or reject it on the Radar.' },
  { key: 'radar_agent_sources', label: 'Agents may add sources', hint: 'On: an agent can add a public RSS or Atom feed (at most 20). Off: it can only suggest one, disabled until you enable it.' },
]

export type M16NumberKey = 'radar_max_fetches_per_day' | 'radar_max_items_per_day' | 'radar_max_ideas_per_day' | 'radar_max_open_ideas' | 'radar_max_goals_per_week'
export const M16_NUMBER_FIELDS: Array<{ key: M16NumberKey; label: string; hint: string; min: number; max: number }> = [
  { key: 'radar_max_fetches_per_day', label: 'Fetches per day', hint: 'Web requests the control plane makes for this company each day, robots.txt and redirects included. 0 stops fetching.', min: 0, max: 100_000 },
  { key: 'radar_max_items_per_day', label: 'Stored items per day', hint: 'More than this and only the highest-scoring items are kept. 0 stores nothing.', min: 0, max: 100_000 },
  { key: 'radar_max_ideas_per_day', label: 'New ideas per day', hint: 'Ideas agents may add each day. 0 stops them.', min: 0, max: 100 },
  { key: 'radar_max_open_ideas', label: 'Open ideas at most', hint: 'Agents cannot add ideas while this many wait for a decision.', min: 0, max: 1000 },
  { key: 'radar_max_goals_per_week', label: 'Goals from ideas per week', hint: 'How many new goals the AI Co-founder may start from ideas each week. 0 means none.', min: 0, max: 20 },
]

/** Radar values as typed, only for the fields the backend reported (same rule as M6, M12 and M15). */
export interface M16FormValues extends Partial<Record<M16BoolKey, boolean>>, Partial<Record<M16NumberKey, string>> {
  /** The address that goes into the fetcher's User-Agent. Blank = none. */
  radar_contact_email?: string
}

export const hasM16Fields = (m16: M16FormValues): boolean => Object.keys(m16).length > 0

function m16ToForm(src: CompanyPolicy): M16FormValues {
  const p = src as CompanyPolicy & RadarPolicyFields
  const m: M16FormValues = {}
  for (const f of M16_BOOL_FIELDS) {
    const v = p[f.key]
    if (v !== undefined) m[f.key] = v
  }
  for (const f of M16_NUMBER_FIELDS) {
    const v = p[f.key]
    if (v !== undefined) m[f.key] = String(v)
  }
  if (p.radar_contact_email !== undefined) m.radar_contact_email = p.radar_contact_email ?? ''
  return m
}

// One plain address (it goes into an HTTP header): the server refuses anything else, so say so before the save.
const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/

export function validateM16(m: M16FormValues): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  for (const f of M16_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const t = raw.trim()
    if (!/^\d+$/.test(t) || !Number.isSafeInteger(Number(t))) e[f.key] = 'Enter a whole number'
    else if (Number(t) < f.min) e[f.key] = `At least ${f.min}`
    else if (Number(t) > f.max) e[f.key] = `At most ${f.max.toLocaleString('en-US')}`
  }
  if (m.radar_contact_email !== undefined) {
    const t = m.radar_contact_email.trim()
    if (t.length > 200) e.radar_contact_email = 'At most 200 characters'
    else if (t !== '' && !EMAIL_RE.test(t)) e.radar_contact_email = 'Enter one plain e-mail address, like you@example.com'
  }
  return e
}

/** The PATCH body for what changed. A blank contact address clears it (null). */
export function diffM16(m: M16FormValues, saved?: CompanyPolicy): Partial<RadarPolicyFields> {
  const p = saved as (CompanyPolicy & RadarPolicyFields) | undefined
  const out: Partial<RadarPolicyFields> = {}
  const changed = <T,>(next: T, prev: T | undefined) => !p || prev === undefined || next !== prev
  for (const f of M16_BOOL_FIELDS) {
    const v = m[f.key]
    if (v !== undefined && changed(v, p?.[f.key])) out[f.key] = v
  }
  for (const f of M16_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const n = Number(raw)
    if (raw.trim() !== '' && Number.isFinite(n) && changed(n, p?.[f.key])) out[f.key] = n
  }
  if (m.radar_contact_email !== undefined) {
    const v = m.radar_contact_email.trim() === '' ? null : m.radar_contact_email.trim()
    if (!p || p.radar_contact_email === undefined ? true : v !== p.radar_contact_email) out.radar_contact_email = v
  }
  return out
}

/* ------------------------------ Milestone 18 (approval deadlines, self-check, work graph) and 21 (meetings) ------------------------------ */

/** Seconds the server accepts: a human approval waits 1 h - 7 days, a Co-founder approval 5 min - 24 h (`schemas/policy.py`). */
export const APPROVAL_TIMEOUT_HOURS = { min: 1, max: 168 } as const
export const AGENT_APPROVAL_TIMEOUT_MINUTES = { min: 5, max: 1440 } as const

export const M18_BOOL_FIELDS: Array<{ key: 'self_check_enabled' | 'work_graph_enabled'; label: string; hint: string }> = [
  { key: 'self_check_enabled', label: 'Agents check their own work before finishing', hint: 'Before a task is marked done, the agent reads its result against the acceptance criteria and fixes what is missing (at most twice).' },
  { key: 'work_graph_enabled', label: 'Durable work graph', hint: 'Runs survive a crash and pick up where they stopped, and wait for your answer or an approval without losing their place. Off: every run starts from scratch.' },
]

/** M18 values as typed, only for the fields the backend reported (same rule as M6 and M12). Hours and minutes are what people think in; the API takes seconds. */
export interface M18FormValues {
  approval_timeout_hours?: string
  agent_approval_timeout_minutes?: string
  self_check_enabled?: boolean
  work_graph_enabled?: boolean
}
export const hasM18Fields = (m: M18FormValues): boolean => Object.keys(m).length > 0

/** 86400 -> "24", 5400 -> "1.5" */
const trimNumber = (n: number): string => String(Math.round(n * 100) / 100)

function m18ToForm(p: CompanyPolicy): M18FormValues {
  const m: M18FormValues = {}
  if (p.approval_timeout_seconds !== undefined) m.approval_timeout_hours = trimNumber(p.approval_timeout_seconds / 3600)
  if (p.agent_approval_timeout_seconds !== undefined) m.agent_approval_timeout_minutes = trimNumber(p.agent_approval_timeout_seconds / 60)
  if (p.self_check_enabled !== undefined) m.self_check_enabled = p.self_check_enabled
  if (p.work_graph_enabled !== undefined) m.work_graph_enabled = p.work_graph_enabled
  return m
}

function checkAmount(raw: string, min: number, max: number, unit: string): string | undefined {
  const t = raw.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return `Enter a number of ${unit}`
  if (Number(t) < min) return `At least ${min} ${min === 1 ? unit.replace(/s$/, '') : unit}`
  if (Number(t) > max) return `At most ${max.toLocaleString('en-US')} ${unit}`
  return undefined
}

export function validateM18(m: M18FormValues): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  if (m.approval_timeout_hours !== undefined) {
    const err = checkAmount(m.approval_timeout_hours, APPROVAL_TIMEOUT_HOURS.min, APPROVAL_TIMEOUT_HOURS.max, 'hours')
    if (err) e.approval_timeout_hours = err
  }
  if (m.agent_approval_timeout_minutes !== undefined) {
    const err = checkAmount(m.agent_approval_timeout_minutes, AGENT_APPROVAL_TIMEOUT_MINUTES.min, AGENT_APPROVAL_TIMEOUT_MINUTES.max, 'minutes')
    if (err) e.agent_approval_timeout_minutes = err
  }
  return e
}

function diffM18(m: M18FormValues, p: CompanyPolicy): UpdatePolicyRequest {
  const out: UpdatePolicyRequest = {}
  if (m.approval_timeout_hours !== undefined && p.approval_timeout_seconds !== undefined) {
    const s = Math.round(Number(m.approval_timeout_hours) * 3600)
    if (Number.isFinite(s) && s !== p.approval_timeout_seconds) out.approval_timeout_seconds = s
  }
  if (m.agent_approval_timeout_minutes !== undefined && p.agent_approval_timeout_seconds !== undefined) {
    const s = Math.round(Number(m.agent_approval_timeout_minutes) * 60)
    if (Number.isFinite(s) && s !== p.agent_approval_timeout_seconds) out.agent_approval_timeout_seconds = s
  }
  if (m.self_check_enabled !== undefined && p.self_check_enabled !== undefined && m.self_check_enabled !== p.self_check_enabled) out.self_check_enabled = m.self_check_enabled
  if (m.work_graph_enabled !== undefined && p.work_graph_enabled !== undefined && m.work_graph_enabled !== p.work_graph_enabled) out.work_graph_enabled = m.work_graph_enabled
  return out
}

export type M21NumberKey = 'max_meetings_per_agent_per_day' | 'max_meetings_per_company_per_day'
export const M21_NUMBER_FIELDS: Array<{ key: M21NumberKey; label: string; hint: string; min: number; max: number }> = [
  { key: 'max_meetings_per_agent_per_day', label: 'Meetings per employee per day', hint: 'How many meetings one employee may call in 24 hours. 0 means none. Past the limit they decide it themselves.', min: 0, max: 50 },
  { key: 'max_meetings_per_company_per_day', label: 'Meetings per company per day', hint: 'How many meetings the whole company may hold in 24 hours. 0 means none.', min: 0, max: 500 },
]

export interface M21FormValues extends Partial<Record<M21NumberKey, string>> {
  meetings_enabled?: boolean
}
export const hasM21Fields = (m: M21FormValues): boolean => Object.keys(m).length > 0

function m21ToForm(p: CompanyPolicy): M21FormValues {
  const m: M21FormValues = {}
  if (p.meetings_enabled !== undefined) m.meetings_enabled = p.meetings_enabled
  for (const f of M21_NUMBER_FIELDS) if (p[f.key] !== undefined) m[f.key] = String(p[f.key])
  return m
}

export function validateM21(m: M21FormValues): PolicyFormErrors {
  const e: PolicyFormErrors = {}
  for (const f of M21_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined) continue
    const t = raw.trim()
    if (!/^\d+$/.test(t) || !Number.isSafeInteger(Number(t))) e[f.key] = 'Enter a whole number'
    else if (Number(t) < f.min) e[f.key] = `At least ${f.min}`
    else if (Number(t) > f.max) e[f.key] = `At most ${f.max.toLocaleString('en-US')}`
  }
  return e
}

function diffM21(m: M21FormValues, p: CompanyPolicy): UpdatePolicyRequest {
  const out: UpdatePolicyRequest = {}
  if (m.meetings_enabled !== undefined && p.meetings_enabled !== undefined && m.meetings_enabled !== p.meetings_enabled) out.meetings_enabled = m.meetings_enabled
  for (const f of M21_NUMBER_FIELDS) {
    const raw = m[f.key]
    if (raw === undefined || p[f.key] === undefined) continue
    const n = Number(raw)
    if (Number.isFinite(n) && n !== p[f.key]) out[f.key] = n
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
  m12: M12FormValues
  m15: M15FormValues
  m16: M16FormValues
  m18: M18FormValues
  m21: M21FormValues
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
    m15: m15ToForm(p),
    m16: m16ToForm(p),
    m18: m18ToForm(p),
    m21: m21ToForm(p),
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
  return { ...e, ...validateM6(v.m6), ...validateM12(v.m12, v.limits, saved), ...validateM15(v.m15), ...validateM16(v.m16), ...validateM18(v.m18), ...validateM21(v.m21) }
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
  return { ...out, ...diffM6(v.m6, p), ...diffM12(v.m12, p), ...(diffM15(v.m15, p) as UpdatePolicyRequest), ...(diffM16(v.m16, p) as UpdatePolicyRequest), ...diffM18(v.m18, p), ...diffM21(v.m21, p) }
}
