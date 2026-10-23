import { describe, expect, it } from 'vitest'
import type { CompanyPolicy } from '@/api/types'
import { diffPolicy, policyToForm, validatePolicyForm } from './policyForm'

const policy: CompanyPolicy = {
  autonomy_mode: 'autonomous',
  human_approval_required_for: ['external_purchase', 'budget_increase'],
  daily_budget_micros: null,
  monthly_budget_micros: 100_000_000,
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

describe('policy form', () => {
  it('round-trips with no diff', () => {
    expect(diffPolicy(policyToForm(policy), policy)).toEqual({})
  })

  it('shows budgets in USD and blank for no limit', () => {
    const f = policyToForm(policy)
    expect(f.daily_budget).toBe('')
    expect(f.monthly_budget).toBe('100.00')
    expect(f.default_run_reservation).toBe('0.05')
  })

  it('sends only changed fields, with money as integer micros', () => {
    const f = policyToForm(policy)
    f.daily_budget = '5'
    f.autonomy_mode = 'supervised'
    f.limits.max_concurrent_runs = '4'
    expect(diffPolicy(f, policy)).toEqual({ daily_budget_micros: 5_000_000, autonomy_mode: 'supervised', max_concurrent_runs: 4 })
  })

  it('can clear a budget back to no limit (null)', () => {
    const f = policyToForm(policy)
    f.monthly_budget = ''
    expect(diffPolicy(f, policy)).toEqual({ monthly_budget_micros: null })
  })

  it('treats approval kinds as a set (order does not matter)', () => {
    const f = policyToForm(policy)
    f.human_approval_required_for = ['budget_increase', 'external_purchase']
    expect(diffPolicy(f, policy)).toEqual({})
    f.human_approval_required_for = []
    expect(diffPolicy(f, policy)).toEqual({ human_approval_required_for: [] })
  })

  it('validates budgets and limits', () => {
    const f = policyToForm(policy)
    f.daily_budget = 'abc'
    f.limits.max_concurrent_runs = '0'
    f.limits.max_headcount = '1.5'
    const e = validatePolicyForm(f)
    expect(e.daily_budget).toBeDefined()
    expect(e.max_concurrent_runs).toBe('At least 1')
    expect(e.max_headcount).toBe('Enter a whole number')
  })

  it('rejects a daily budget above the monthly one', () => {
    const f = policyToForm(policy)
    f.daily_budget = '500'
    expect(validatePolicyForm(f).daily_budget).toMatch(/monthly/)
  })

  it('allows zero revisions and zero delegation depth', () => {
    const f = policyToForm(policy)
    f.limits.max_revisions = '0'
    f.limits.max_delegation_depth = '0'
    expect(validatePolicyForm(f)).toEqual({})
  })
})
