import type {
  ActionTier,
  EmergencyStopStatus,
  GuardrailConditions,
  GuardrailDecision,
  GuardrailEventDecision,
  GuardrailRule,
  RateLimitStatus,
  RoleFamily,
} from '@/api/types'
import type { Tone } from '@/components/ui/Badge'
import { titleCase } from './format'
import { formatUsd } from './money'
import { ROLE_FAMILY_LABEL } from './roleFamily'

/* ------------------------------ tiers & decisions ------------------------------ */

export const TIERS: ActionTier[] = [
  'read',
  'internal_write',
  'org_change',
  'external_read',
  'external_communication',
  'external_commitment',
  'spend',
  'identity',
  'destructive',
]

export const TIER_LABEL: Record<ActionTier, string> = {
  read: 'Read',
  internal_write: 'Internal write',
  org_change: 'Org change',
  external_read: 'External read',
  external_communication: 'External communication',
  external_commitment: 'External commitment',
  spend: 'Spend',
  identity: 'Identity',
  destructive: 'Destructive',
}

export const TIER_EXAMPLES: Record<ActionTier, string> = {
  read: 'Get context or state, search colleagues, read artifacts',
  internal_write: 'Create or update tasks, artifacts, progress, decisions, memory proposals',
  org_change: 'Hire, assign skills, create role templates, create schedules',
  external_read: 'Web search or fetch, GitHub read',
  external_communication: 'Send email, post publicly, open pull requests on external repos, invite humans',
  external_commitment: 'Accept terms, register accounts or domains, sign up for services',
  spend: 'Any purchase or subscription outside model usage',
  identity: 'Create mailboxes, change DNS, deploy the control UI, rotate credentials',
  destructive: 'Delete or overwrite external resources, cancel others’ work at scale',
}

export function tierLabel(tier: string): string {
  return TIER_LABEL[tier as ActionTier] ?? titleCase(tier)
}

/** Least to most restrictive. */
export const DECISION_ORDER: GuardrailDecision[] = ['allow', 'peer_review', 'cofounder_approval', 'human_approval', 'deny']

export const DECISION_LABEL: Record<GuardrailDecision, string> = {
  allow: 'Allow',
  peer_review: 'Peer review',
  cofounder_approval: 'AI Co-founder approval',
  human_approval: 'Human approval',
  deny: 'Deny',
}

export const DECISION_HELP: Record<GuardrailDecision, string> = {
  allow: 'The agent acts immediately (still audited and rate limited).',
  peer_review: 'Another agent checks the exact content first, then the action goes ahead.',
  cofounder_approval: 'The AI Co-founder decides before the action runs.',
  human_approval: 'A human decides before the action runs.',
  deny: 'The action is refused (403 guardrail_denied).',
}

export const DECISION_TONE: Record<GuardrailDecision, Tone> = {
  allow: 'success',
  peer_review: 'info',
  cofounder_approval: 'accent',
  human_approval: 'warning',
  deny: 'danger',
}

/** Chip label for any logged decision; unknown values degrade to a neutral title-cased label. */
export function decisionLabel(d: string): string {
  if (d === 'rate_limited') return 'Rate limited'
  return DECISION_LABEL[d as GuardrailDecision] ?? titleCase(d)
}
export function decisionTone(d: string): Tone {
  if (d === 'rate_limited') return 'warning'
  return DECISION_TONE[d as GuardrailDecision] ?? 'neutral'
}
export function decisionHelp(d: string): string | null {
  return DECISION_HELP[d as GuardrailDecision] ?? (d === 'rate_limited' ? 'The agent exceeded a rate limit (429 rate_limited).' : null)
}

/** Higher = stricter. Unknown values sort as the most permissive so they never hide a stricter one. */
export function strictness(d: GuardrailDecision): number {
  return Math.max(0, DECISION_ORDER.indexOf(d))
}

export const EVENT_DECISIONS: GuardrailEventDecision[] = [...DECISION_ORDER, 'rate_limited']

/* --------------------------------- defaults ------------------------------------ */

export interface TierDefault {
  tier: ActionTier
  decision: GuardrailDecision
  /** The spec's qualifier for the default, shown beside the chip. */
  note: string | null
}

/**
 * The built-in baseline (docs/specs/m6 section 1). The server decides; this table only shows the baseline that
 * company rules override. `spend` and `identity` carry a qualifier the table cannot express as one chip.
 */
export const TIER_DEFAULTS: TierDefault[] = [
  { tier: 'read', decision: 'allow', note: null },
  { tier: 'internal_write', decision: 'allow', note: null },
  { tier: 'org_change', decision: 'allow', note: 'Within headcount and budget limits (M4 rules)' },
  { tier: 'external_read', decision: 'allow', note: 'Rate limited; private URLs are blocked' },
  { tier: 'external_communication', decision: 'peer_review', note: 'Another agent checks the content, then it is allowed' },
  { tier: 'external_commitment', decision: 'human_approval', note: null },
  { tier: 'spend', decision: 'cofounder_approval', note: 'Human approval above the automatic spend limit' },
  { tier: 'identity', decision: 'cofounder_approval', note: 'Plus human approval for anything touching domains or DNS ownership' },
  { tier: 'destructive', decision: 'human_approval', note: null },
]

export const defaultFor = (tier: ActionTier): TierDefault | undefined => TIER_DEFAULTS.find((d) => d.tier === tier)

/* ---------------------------- rule resolution preview --------------------------- */

export interface AgentRef {
  id: string
  family: RoleFamily | string | null
}

export type ResolutionSource = 'action_rule' | 'tier_rule' | 'default'

export interface Resolution {
  decision: GuardrailDecision
  source: ResolutionSource
  rule: GuardrailRule | null
}

/** Does the rule's scope cover this agent? An `all` rule covers everyone, also when no agent is given. */
export function scopeMatches(rule: Pick<GuardrailRule, 'scope_type' | 'scope_value'>, agent: AgentRef | null): boolean {
  if (rule.scope_type === 'all') return true
  if (!agent) return false
  if (rule.scope_type === 'agent') return rule.scope_value === agent.id
  return rule.scope_value !== null && rule.scope_value === agent.family
}

const SCOPE_RANK: Record<GuardrailRule['scope_type'], number> = { agent: 3, family: 2, all: 1 }

/**
 * Which decision applies. Mirrors the spec ("specific action > family > tier default"):
 * 1. a rule for the exact action beats a rule for the whole tier, and any rule beats the built-in default;
 * 2. within one of those, the narrower scope wins: this agent, then its family, then all agents;
 * 3. ties go to the most recently updated rule.
 * Conditions (amount, rate, domains) are evaluated by the server at run time and are not part of this preview.
 */
export function resolveDecision(rules: GuardrailRule[], input: { tier: ActionTier; action?: string | null }, agent: AgentRef | null): Resolution {
  const action = input.action?.trim() || null
  const pick = (candidates: GuardrailRule[]): GuardrailRule | null => {
    let best: GuardrailRule | null = null
    for (const r of candidates) {
      if (!scopeMatches(r, agent)) continue
      if (
        !best ||
        SCOPE_RANK[r.scope_type] > SCOPE_RANK[best.scope_type] ||
        (SCOPE_RANK[r.scope_type] === SCOPE_RANK[best.scope_type] && r.updated_at > best.updated_at)
      ) {
        best = r
      }
    }
    return best
  }
  const byAction = action ? pick(rules.filter((r) => r.action === action)) : null
  if (byAction) return { decision: byAction.decision, source: 'action_rule', rule: byAction }
  const byTier = pick(rules.filter((r) => r.tier === input.tier && !r.action))
  if (byTier) return { decision: byTier.decision, source: 'tier_rule', rule: byTier }
  return { decision: defaultFor(input.tier)?.decision ?? 'deny', source: 'default', rule: null }
}

export const SOURCE_LABEL: Record<ResolutionSource, string> = {
  action_rule: 'Rule for this action',
  tier_rule: 'Rule for this tier',
  default: 'Built-in default',
}

/** Rules that replace a tier's baseline (tier-wide rules and action rules inside it), per tier. */
export function overridesByTier(rules: GuardrailRule[]): Map<ActionTier, GuardrailRule[]> {
  const out = new Map<ActionTier, GuardrailRule[]>()
  for (const r of rules) out.set(r.tier, [...(out.get(r.tier) ?? []), r])
  return out
}

export type Direction = 'loosens' | 'tightens' | 'same'

/** Compare a decision to the tier's built-in default: does the rule make the company less safe? */
export function directionVsDefault(tier: ActionTier, decision: GuardrailDecision): Direction {
  const base = defaultFor(tier)
  if (!base) return 'same'
  const a = strictness(decision)
  const b = strictness(base.decision)
  return a < b ? 'loosens' : a > b ? 'tightens' : 'same'
}

/* ------------------------------- labels & summaries ------------------------------ */

export function scopeLabel(rule: Pick<GuardrailRule, 'scope_type' | 'scope_value'>, agentName?: (id: string) => string | null): string {
  if (rule.scope_type === 'all') return 'All agents'
  if (rule.scope_type === 'family') return `Family: ${ROLE_FAMILY_LABEL[rule.scope_value as RoleFamily] ?? titleCase(rule.scope_value ?? 'unknown')}`
  return `Agent: ${(rule.scope_value && agentName?.(rule.scope_value)) || 'unknown agent'}`
}

/** "up to $50.00 · 5/day · example.com, acme.io"; empty conditions read "No conditions". */
export function conditionsSummary(c: GuardrailConditions | null | undefined): string {
  if (!c) return 'No conditions'
  const parts: string[] = []
  if (typeof c.max_amount_micros === 'number') parts.push(`up to ${formatUsd(c.max_amount_micros)}`)
  if (typeof c.max_per_day === 'number') parts.push(`${c.max_per_day}/day`)
  if (Array.isArray(c.allowed_domains) && c.allowed_domains.length > 0) parts.push(`domains: ${c.allowed_domains.join(', ')}`)
  const known = new Set(['max_amount_micros', 'max_per_day', 'allowed_domains'])
  for (const [k, v] of Object.entries(c)) {
    if (known.has(k) || v === null || v === undefined || v === '') continue
    parts.push(`${titleCase(k).toLowerCase()}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  }
  return parts.length ? parts.join(' · ') : 'No conditions'
}

/* ---------------------------------- rate limits ---------------------------------- */

export const RATE_LIMIT_LABEL: Record<string, string> = {
  api_calls_per_min: 'API calls per minute',
  tasks_created_per_hour: 'Tasks created per hour',
  external_communications_per_day: 'External communications per day',
  hires_per_day: 'Hires per day',
  skill_installs_per_day: 'Skill installs per day',
  web_fetches_per_hour: 'Web fetches per hour',
}

export const rateLimitLabel = (key: string): string => RATE_LIMIT_LABEL[key] ?? titleCase(key)

export type UsageLevel = 'ok' | 'warn' | 'full'

/** Share of the limit used, 0..1+ (null when the limit is not positive). */
export function rateFraction(r: Pick<RateLimitStatus, 'used' | 'limit'>): number | null {
  return r.limit > 0 ? r.used / r.limit : null
}
export function rateLevel(r: Pick<RateLimitStatus, 'used' | 'limit'>): UsageLevel {
  const f = rateFraction(r)
  if (f === null) return 'ok'
  return f >= 1 ? 'full' : f >= 0.8 ? 'warn' : 'ok'
}

export interface AgentRateGroup {
  agentId: string | null
  limits: RateLimitStatus[]
  /** Worst usage fraction in the group, for ordering. */
  worst: number
}

/** Per-agent groups, busiest first; company-wide windows (no agent) come first of all. */
export function groupRateLimits(items: RateLimitStatus[]): AgentRateGroup[] {
  const map = new Map<string, RateLimitStatus[]>()
  for (const r of items) map.set(r.agent_id ?? '', [...(map.get(r.agent_id ?? '') ?? []), r])
  const groups: AgentRateGroup[] = [...map.entries()].map(([id, limits]) => ({
    agentId: id || null,
    limits: [...limits].sort((a, b) => (rateFraction(b) ?? 0) - (rateFraction(a) ?? 0)),
    worst: Math.max(0, ...limits.map((l) => rateFraction(l) ?? 0)),
  }))
  return groups.sort((a, b) => (a.agentId === null ? -1 : b.agentId === null ? 1 : b.worst - a.worst))
}

/** The response is `{items}` or a bare array; anything else is no data. */
export function normalizeRateLimits(raw: unknown): RateLimitStatus[] {
  const list = Array.isArray(raw) ? raw : raw && typeof raw === 'object' && Array.isArray((raw as { items?: unknown }).items) ? (raw as { items: unknown[] }).items : []
  const out: RateLimitStatus[] = []
  for (const r of list) {
    if (!r || typeof r !== 'object') continue
    const o = r as Record<string, unknown>
    if (typeof o.key !== 'string' || typeof o.limit !== 'number' || typeof o.used !== 'number') continue
    out.push({
      agent_id: typeof o.agent_id === 'string' ? o.agent_id : null,
      key: o.key,
      limit: o.limit,
      used: o.used,
      window_seconds: typeof o.window_seconds === 'number' ? o.window_seconds : 0,
      resets_at: typeof o.resets_at === 'string' ? o.resets_at : null,
    })
  }
  return out
}

/* --------------------------------- emergency stop --------------------------------- */

export const EMERGENCY_CONFIRM_WORD = 'STOP'

/** The stop button stays disabled until the exact word is typed (case-sensitive, surrounding spaces ignored). */
export function canConfirmEmergencyStop(typed: string): boolean {
  return typed.trim() === EMERGENCY_CONFIRM_WORD
}

/** Accepts the status as the object itself or nested under `emergency_stop`. `null` when it is not a status. */
export function normalizeEmergencyStatus(raw: unknown): EmergencyStopStatus | null {
  if (!raw || typeof raw !== 'object') return null
  const root = raw as Record<string, unknown>
  const o = root.emergency_stop && typeof root.emergency_stop === 'object' ? (root.emergency_stop as Record<string, unknown>) : root
  if (typeof o.active !== 'boolean') return null
  const s = (v: unknown) => (typeof v === 'string' && v !== '' ? v : null)
  return {
    active: o.active,
    activated_at: s(o.activated_at),
    activated_by: s(o.activated_by),
    reason: s(o.reason),
    runs_cancelled: typeof o.runs_cancelled === 'number' ? o.runs_cancelled : null,
  }
}
