import { describe, expect, it } from 'vitest'
import type { CompanyPolicy } from '@/api/types'
import { diffPolicy, hasM6Fields, parseThresholds, policyToForm, validatePolicyForm } from './policyForm'

const base: CompanyPolicy = {
  autonomy_mode: 'autonomous',
  human_approval_required_for: [],
  daily_budget_micros: null,
  monthly_budget_micros: null,
  max_concurrent_runs: 2,
  max_delegation_depth: 3,
  max_tasks_per_planning_cycle: 10,
  max_attempts_per_task: 3,
  max_revisions: 2,
  max_run_seconds: 900,
  max_headcount: 12,
  heartbeat_interval_seconds: 900,
  default_run_reservation_micros: 50_000,
  allowed_model_providers: [],
}
const m6: CompanyPolicy = {
  ...base,
  max_agent_monthly_budget_micros: 50_000_000,
  hire_default_monthly_budget_micros: 10_000_000,
  budget_alert_thresholds: [50, 80, 100],
  model_routing: 'free_first',
  allow_paid_fallback: false,
  auto_spend_limit_micros: 0,
  stall_seconds: 180,
}

describe('policy form: Milestone 6 fields', () => {
  it('hides every M6 field the backend did not send (nothing is faked)', () => {
    const f = policyToForm(base)
    expect(f.m6).toEqual({})
    expect(hasM6Fields(f.m6)).toBe(false)
    expect(diffPolicy(f, base)).toEqual({})
  })

  it('shows only the fields that are present', () => {
    const partial: CompanyPolicy = { ...base, stall_seconds: 120, model_routing: 'pinned' }
    const f = policyToForm(partial)
    expect(Object.keys(f.m6).sort()).toEqual(['model_routing', 'stall_seconds'])
    f.m6.stall_seconds = '240'
    expect(diffPolicy(f, partial)).toEqual({ stall_seconds: 240 })
  })

  it('shows USD for money and a list for thresholds, and round-trips with no diff', () => {
    const f = policyToForm(m6)
    expect(f.m6).toEqual({
      max_agent_monthly_budget_micros: '50.00',
      hire_default_monthly_budget_micros: '10.00',
      auto_spend_limit_micros: '0.00',
      budget_alert_thresholds: '50, 80, 100',
      model_routing: 'free_first',
      allow_paid_fallback: false,
      stall_seconds: '180',
    })
    expect(diffPolicy(f, m6)).toEqual({})
    expect(validatePolicyForm(f)).toEqual({})
  })

  it('sends changed M6 fields only, money as exact micros', () => {
    const f = policyToForm(m6)
    f.m6.max_agent_monthly_budget_micros = '19.99'
    f.m6.auto_spend_limit_micros = '25'
    f.m6.model_routing = 'quality_first'
    f.m6.allow_paid_fallback = true
    f.m6.budget_alert_thresholds = '25 75, 100'
    expect(diffPolicy(f, m6)).toEqual({
      max_agent_monthly_budget_micros: 19_990_000,
      auto_spend_limit_micros: 25_000_000,
      model_routing: 'quality_first',
      allow_paid_fallback: true,
      budget_alert_thresholds: [25, 75, 100],
    })
  })

  it('can clear the per-agent cap and the hire default (null = none)', () => {
    const f = policyToForm(m6)
    f.m6.max_agent_monthly_budget_micros = ''
    f.m6.hire_default_monthly_budget_micros = ''
    expect(validatePolicyForm(f)).toEqual({})
    expect(diffPolicy(f, m6)).toEqual({ max_agent_monthly_budget_micros: null, hire_default_monthly_budget_micros: null })
  })

  it('treats thresholds as a set (order and spacing do not matter)', () => {
    const f = policyToForm(m6)
    f.m6.budget_alert_thresholds = '100,50 , 80'
    expect(diffPolicy(f, m6)).toEqual({})
  })

  it('validates', () => {
    const f = policyToForm(m6)
    f.m6.max_agent_monthly_budget_micros = 'abc'
    f.m6.auto_spend_limit_micros = ''
    f.m6.budget_alert_thresholds = '50, abc'
    f.m6.stall_seconds = '0'
    const e = validatePolicyForm(f)
    expect(e.max_agent_monthly_budget_micros).toBeDefined()
    expect(e.auto_spend_limit_micros).toBeDefined()
    expect(e.budget_alert_thresholds).toMatch(/abc/)
    expect(e.stall_seconds).toBeDefined()
  })

  it('does not let the hire default exceed the per-agent cap', () => {
    const f = policyToForm(m6)
    f.m6.hire_default_monthly_budget_micros = '60'
    expect(validatePolicyForm(f).hire_default_monthly_budget_micros).toMatch(/cannot exceed/)
  })

  it('parses thresholds', () => {
    expect(parseThresholds('50, 80, 100')).toEqual({ value: [50, 80, 100] })
    expect(parseThresholds('80% 50%')).toEqual({ value: [50, 80] })
    expect(parseThresholds('50,50')).toEqual({ value: [50] })
    expect(parseThresholds('').error).toBeDefined()
    expect(parseThresholds('0').error).toBeDefined()
    expect(parseThresholds('-5').error).toBeDefined()
    expect(parseThresholds('5000').error).toBeDefined()
  })
})
