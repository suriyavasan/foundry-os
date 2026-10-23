import { describe, expect, it } from 'vitest'
import { makeRule } from '@/test/factories'
import {
  DECISION_ORDER,
  EVENT_DECISIONS,
  TIERS,
  TIER_DEFAULTS,
  canConfirmEmergencyStop,
  conditionsSummary,
  decisionLabel,
  decisionTone,
  directionVsDefault,
  groupRateLimits,
  normalizeEmergencyStatus,
  normalizeRateLimits,
  overridesByTier,
  rateFraction,
  rateLevel,
  resolveDecision,
  scopeLabel,
  scopeMatches,
  strictness,
} from './guardrails'

const eng = { id: 'a-eng', family: 'engineering' }
const mkt = { id: 'a-mkt', family: 'marketing' }

describe('built-in defaults (spec table)', () => {
  it('covers every tier exactly once with the spec decisions', () => {
    expect(TIER_DEFAULTS.map((d) => d.tier)).toEqual(TIERS)
    const by = Object.fromEntries(TIER_DEFAULTS.map((d) => [d.tier, d.decision]))
    expect(by).toMatchObject({
      read: 'allow',
      internal_write: 'allow',
      org_change: 'allow',
      external_read: 'allow',
      external_communication: 'peer_review',
      external_commitment: 'human_approval',
      spend: 'cofounder_approval',
      identity: 'cofounder_approval',
      destructive: 'human_approval',
    })
  })
})

describe('resolveDecision (specific action > family > tier default)', () => {
  it('falls back to the tier default when no rule matches', () => {
    expect(resolveDecision([], { tier: 'destructive' }, eng)).toEqual({ decision: 'human_approval', source: 'default', rule: null })
    const unrelated = makeRule({ tier: 'spend', decision: 'deny' })
    expect(resolveDecision([unrelated], { tier: 'destructive' }, eng).source).toBe('default')
  })

  it('a tier rule beats the default', () => {
    const r = makeRule({ tier: 'external_communication', decision: 'deny' })
    expect(resolveDecision([r], { tier: 'external_communication' }, eng)).toMatchObject({ decision: 'deny', source: 'tier_rule', rule: r })
  })

  it('a rule for the exact action beats a tier rule, whatever the scope', () => {
    const tierAgent = makeRule({ tier: 'external_communication', scope_type: 'agent', scope_value: eng.id, decision: 'deny' })
    const action = makeRule({ tier: 'external_communication', action: 'email.send', decision: 'allow' })
    const res = resolveDecision([tierAgent, action], { tier: 'external_communication', action: 'email.send' }, eng)
    expect(res).toMatchObject({ decision: 'allow', source: 'action_rule', rule: action })
  })

  it('an action rule does not apply to another action of the same tier', () => {
    const action = makeRule({ tier: 'external_communication', action: 'email.send', decision: 'deny' })
    expect(resolveDecision([action], { tier: 'external_communication', action: 'post.public' }, eng).source).toBe('default')
  })

  it('narrower scope wins: agent over family over all', () => {
    const all = makeRule({ decision: 'allow' })
    const fam = makeRule({ scope_type: 'family', scope_value: 'engineering', decision: 'peer_review' })
    const ag = makeRule({ scope_type: 'agent', scope_value: eng.id, decision: 'deny' })
    const input = { tier: 'external_communication' as const }
    expect(resolveDecision([all, fam, ag], input, eng).decision).toBe('deny')
    expect(resolveDecision([all, fam], input, eng).decision).toBe('peer_review')
    expect(resolveDecision([all], input, eng).decision).toBe('allow')
  })

  it('ignores rules scoped to another family or agent', () => {
    const fam = makeRule({ scope_type: 'family', scope_value: 'engineering', decision: 'deny' })
    const ag = makeRule({ scope_type: 'agent', scope_value: 'someone-else', decision: 'deny' })
    expect(resolveDecision([fam, ag], { tier: 'external_communication' }, mkt).source).toBe('default')
  })

  it('without an agent only "all agents" rules apply', () => {
    const fam = makeRule({ scope_type: 'family', scope_value: 'engineering', decision: 'deny' })
    const all = makeRule({ decision: 'allow' })
    expect(resolveDecision([fam], { tier: 'external_communication' }, null).source).toBe('default')
    expect(resolveDecision([fam, all], { tier: 'external_communication' }, null).decision).toBe('allow')
  })

  it('on equal specificity the most recently updated rule wins', () => {
    const old = makeRule({ decision: 'allow', updated_at: '2026-10-01T00:00:00Z' })
    const fresh = makeRule({ decision: 'deny', updated_at: '2026-10-20T00:00:00Z' })
    expect(resolveDecision([fresh, old], { tier: 'external_communication' }, eng).decision).toBe('deny')
    expect(resolveDecision([old, fresh], { tier: 'external_communication' }, eng).decision).toBe('deny')
  })

  it('ignores blank action input', () => {
    const action = makeRule({ action: 'email.send', decision: 'deny' })
    expect(resolveDecision([action], { tier: 'external_communication', action: '  ' }, eng).source).toBe('default')
  })
})

describe('scopeMatches / scopeLabel', () => {
  it('matches by scope type', () => {
    expect(scopeMatches({ scope_type: 'all', scope_value: null }, null)).toBe(true)
    expect(scopeMatches({ scope_type: 'agent', scope_value: 'x' }, { id: 'x', family: null })).toBe(true)
    expect(scopeMatches({ scope_type: 'family', scope_value: 'qa' }, { id: 'x', family: 'qa' })).toBe(true)
    expect(scopeMatches({ scope_type: 'family', scope_value: 'qa' }, { id: 'x', family: 'hr' })).toBe(false)
  })
  it('labels scopes in words', () => {
    expect(scopeLabel({ scope_type: 'all', scope_value: null })).toBe('All agents')
    expect(scopeLabel({ scope_type: 'family', scope_value: 'engineering' })).toBe('Family: Engineering')
    expect(scopeLabel({ scope_type: 'agent', scope_value: 'a1' }, () => 'Iris')).toBe('Agent: Iris')
    expect(scopeLabel({ scope_type: 'agent', scope_value: 'a1' })).toBe('Agent: unknown agent')
  })
})

describe('decision chips', () => {
  it('has a distinct label and tone for each decision', () => {
    const labels = DECISION_ORDER.map(decisionLabel)
    expect(labels).toEqual(['Allow', 'Peer review', 'AI Co-founder approval', 'Human approval', 'Deny'])
    expect(new Set(labels).size).toBe(5)
    expect(DECISION_ORDER.map(decisionTone)).toEqual(['success', 'info', 'accent', 'warning', 'danger'])
  })
  it('knows rate_limited and degrades unknown values to a neutral label', () => {
    expect(EVENT_DECISIONS).toContain('rate_limited')
    expect(decisionLabel('rate_limited')).toBe('Rate limited')
    expect(decisionTone('rate_limited')).toBe('warning')
    expect(decisionLabel('quarantined')).toBe('Quarantined')
    expect(decisionTone('quarantined')).toBe('neutral')
  })
  it('orders decisions from least to most strict', () => {
    expect(DECISION_ORDER.map(strictness)).toEqual([0, 1, 2, 3, 4])
  })
  it('flags a rule that loosens or tightens the default', () => {
    expect(directionVsDefault('destructive', 'allow')).toBe('loosens')
    expect(directionVsDefault('destructive', 'deny')).toBe('tightens')
    expect(directionVsDefault('destructive', 'human_approval')).toBe('same')
    expect(directionVsDefault('read', 'peer_review')).toBe('tightens')
  })
})

describe('conditions summary', () => {
  it('reads known conditions in words', () => {
    expect(conditionsSummary({})).toBe('No conditions')
    expect(conditionsSummary(null)).toBe('No conditions')
    expect(conditionsSummary({ max_amount_micros: 50_000_000, max_per_day: 5, allowed_domains: ['a.com', 'b.io'] })).toBe('up to $50.00 · 5/day · domains: a.com, b.io')
  })
  it('shows unknown conditions rather than hiding them', () => {
    expect(conditionsSummary({ business_hours: true })).toBe('business hours: true')
  })
})

describe('overrides', () => {
  it('counts the rules per tier', () => {
    const m = overridesByTier([makeRule({ tier: 'spend' }), makeRule({ tier: 'spend', action: 'buy.domain' }), makeRule({ tier: 'read' })])
    expect(m.get('spend')).toHaveLength(2)
    expect(m.get('read')).toHaveLength(1)
    expect(m.has('identity')).toBe(false)
  })
})

describe('rate limit math', () => {
  const r = (used: number, limit: number) => ({ used, limit })
  it('computes fraction and level at the edges', () => {
    expect(rateFraction(r(30, 60))).toBe(0.5)
    expect(rateFraction(r(1, 0))).toBeNull()
    expect(rateLevel(r(47, 60))).toBe('ok')
    expect(rateLevel(r(48, 60))).toBe('warn')
    expect(rateLevel(r(59, 60))).toBe('warn')
    expect(rateLevel(r(60, 60))).toBe('full')
    expect(rateLevel(r(75, 60))).toBe('full')
    expect(rateLevel(r(5, 0))).toBe('ok')
  })
  it('groups per agent, busiest first, company-wide first of all', () => {
    const items = [
      { agent_id: 'a', key: 'x', limit: 10, used: 1, window_seconds: 60, resets_at: null },
      { agent_id: 'b', key: 'x', limit: 10, used: 9, window_seconds: 60, resets_at: null },
      { agent_id: null, key: 'y', limit: 100, used: 5, window_seconds: 60, resets_at: null },
      { agent_id: 'b', key: 'z', limit: 10, used: 2, window_seconds: 60, resets_at: null },
    ]
    const groups = groupRateLimits(items)
    expect(groups.map((g) => g.agentId)).toEqual([null, 'b', 'a'])
    expect(groups[1]!.limits.map((l) => l.key)).toEqual(['x', 'z'])
  })
  it('normalises {items} or a bare array and drops malformed rows', () => {
    const row = { agent_id: 'a', key: 'api_calls_per_min', limit: 60, used: 3, window_seconds: 60, resets_at: '2026-10-24T09:01:00Z' }
    expect(normalizeRateLimits({ items: [row, { key: 'bad' }, null] })).toHaveLength(1)
    expect(normalizeRateLimits([row])).toHaveLength(1)
    expect(normalizeRateLimits('nope')).toEqual([])
  })
})

describe('emergency stop', () => {
  it('only the exact word STOP confirms', () => {
    expect(canConfirmEmergencyStop('STOP')).toBe(true)
    expect(canConfirmEmergencyStop('  STOP  ')).toBe(true)
    expect(canConfirmEmergencyStop('stop')).toBe(false)
    expect(canConfirmEmergencyStop('Stop')).toBe(false)
    expect(canConfirmEmergencyStop('STO')).toBe(false)
    expect(canConfirmEmergencyStop('STOPP')).toBe(false)
    expect(canConfirmEmergencyStop('')).toBe(false)
  })
  it('reads the status flat or nested and refuses anything else', () => {
    expect(normalizeEmergencyStatus({ active: true, activated_at: '2026-10-24T09:00:00Z', activated_by: 'owner', reason: 'runaway', runs_cancelled: 3 })).toEqual({
      active: true,
      activated_at: '2026-10-24T09:00:00Z',
      activated_by: 'owner',
      reason: 'runaway',
      runs_cancelled: 3,
    })
    expect(normalizeEmergencyStatus({ emergency_stop: { active: false } })).toMatchObject({ active: false, activated_at: null })
    expect(normalizeEmergencyStatus({ status: 'ok' })).toBeNull()
    expect(normalizeEmergencyStatus(null)).toBeNull()
  })
})
