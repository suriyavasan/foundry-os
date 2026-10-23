import type { ActionTier, GuardrailConditions, GuardrailDecision, GuardrailRule, GuardrailRuleCreate, GuardrailScopeType } from '@/api/types'
import { TIERS } from '@/lib/guardrails'
import { microsToUsdString, usdToMicros } from '@/lib/money'
import { ROLE_FAMILIES } from '@/lib/roleFamily'

export interface RuleFormValues {
  /** `tier`: the rule covers a whole tier. `action`: it covers one named action (and wins over a tier rule). */
  targetKind: 'tier' | 'action'
  tier: ActionTier | ''
  action: string
  scopeType: GuardrailScopeType
  family: string
  agentId: string
  decision: GuardrailDecision
  /** USD, blank = no limit. */
  maxAmount: string
  /** Whole number, blank = no limit. */
  maxPerDay: string
  domains: string[]
}

export type RuleFormErrors = Partial<Record<'tier' | 'action' | 'family' | 'agentId' | 'maxAmount' | 'maxPerDay' | 'domains', string>>

export const emptyRuleForm = (): RuleFormValues => ({
  targetKind: 'tier',
  tier: '',
  action: '',
  scopeType: 'all',
  family: '',
  agentId: '',
  decision: 'human_approval',
  maxAmount: '',
  maxPerDay: '',
  domains: [],
})

export function ruleToForm(r: GuardrailRule): RuleFormValues {
  const c = r.conditions ?? {}
  return {
    targetKind: r.action ? 'action' : 'tier',
    tier: r.tier,
    action: r.action ?? '',
    scopeType: r.scope_type,
    family: r.scope_type === 'family' ? (r.scope_value ?? '') : '',
    agentId: r.scope_type === 'agent' ? (r.scope_value ?? '') : '',
    decision: r.decision,
    maxAmount: typeof c.max_amount_micros === 'number' ? microsToUsdString(c.max_amount_micros) : '',
    maxPerDay: typeof c.max_per_day === 'number' ? String(c.max_per_day) : '',
    domains: Array.isArray(c.allowed_domains) ? [...c.allowed_domains] : [],
  }
}

export const ACTION_RE = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}$/
/** A bare host name or `*.host`; no scheme, path or port. */
export const DOMAIN_RE = /^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/

export function validateRuleForm(v: RuleFormValues): RuleFormErrors {
  const e: RuleFormErrors = {}
  if (!v.tier || !TIERS.includes(v.tier)) e.tier = v.targetKind === 'action' ? 'Pick the tier this action belongs to' : 'Pick a tier'
  if (v.targetKind === 'action') {
    if (!v.action.trim()) e.action = 'Enter the action name, e.g. email.send'
    else if (!ACTION_RE.test(v.action.trim())) e.action = 'Letters, digits and . _ : / - only (max 80)'
  }
  if (v.scopeType === 'family' && !(ROLE_FAMILIES as readonly string[]).includes(v.family)) e.family = 'Pick a role family'
  if (v.scopeType === 'agent' && !v.agentId) e.agentId = 'Pick an agent'
  if (v.maxAmount.trim() !== '') {
    const m = usdToMicros(v.maxAmount)
    if (m === null) e.maxAmount = 'Enter a USD amount like 25 or 25.50'
  }
  if (v.maxPerDay.trim() !== '') {
    if (!/^\d+$/.test(v.maxPerDay.trim()) || Number(v.maxPerDay) < 1 || !Number.isSafeInteger(Number(v.maxPerDay))) e.maxPerDay = 'Enter a whole number, at least 1'
  }
  const bad = v.domains.find((d) => !DOMAIN_RE.test(d.trim().toLowerCase()))
  if (bad) e.domains = `"${bad}" is not a host name (use example.com or *.example.com)`
  return e
}

/** The API body. Call only after validation passes. Blank conditions are omitted, never sent as 0. */
export function formToRequest(v: RuleFormValues): GuardrailRuleCreate {
  const conditions: GuardrailConditions = {}
  if (v.maxAmount.trim() !== '') conditions.max_amount_micros = usdToMicros(v.maxAmount)
  if (v.maxPerDay.trim() !== '') conditions.max_per_day = Number(v.maxPerDay.trim())
  const domains = [...new Set(v.domains.map((d) => d.trim().toLowerCase()).filter(Boolean))]
  if (domains.length > 0) conditions.allowed_domains = domains
  return {
    tier: v.tier as ActionTier,
    action: v.targetKind === 'action' ? v.action.trim() : null,
    scope_type: v.scopeType,
    scope_value: v.scopeType === 'family' ? v.family : v.scopeType === 'agent' ? v.agentId : null,
    decision: v.decision,
    conditions,
  }
}
