import { describe, expect, it } from 'vitest'
import type { CompanyPolicy } from '@/api/types'
import { M12_NUMBER_FIELDS, ROLE_FAMILIES, diffPolicy, hasM12Fields, policyToForm, validatePolicyForm } from './policyForm'

const base: CompanyPolicy = {
  autonomy_mode: 'autonomous',
  human_approval_required_for: [],
  daily_budget_micros: null,
  monthly_budget_micros: null,
  max_concurrent_runs: 3,
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
const m12: CompanyPolicy = {
  ...base,
  cli_role_families: null,
  cli_run_reservation_micros: 1_000_000,
  management_reserved_slots: 1,
  max_parallel_runs_per_agent: 1,
  max_work_tasks_per_project: 15,
  max_subtasks_per_task: 5,
  circuit_breaker_cooldown_seconds: 1800,
  initiative_enabled: true,
  initiative_interval_seconds: 21_600,
  project_auto_complete: true,
}

describe('policy form: Milestone 12 fields', () => {
  it('hides every M12 field the backend did not send', () => {
    const f = policyToForm(base)
    expect(f.m12).toEqual({})
    expect(hasM12Fields(f.m12)).toBe(false)
    expect(diffPolicy(f, base)).toEqual({})
  })

  it('shows money as USD, keeps null as "every role family", and round-trips with no diff', () => {
    const f = policyToForm(m12)
    expect(f.m12).toEqual({
      cli_role_families: null,
      cli_run_reservation_micros: '1.00',
      management_reserved_slots: '1',
      max_parallel_runs_per_agent: '1',
      max_work_tasks_per_project: '15',
      max_subtasks_per_task: '5',
      circuit_breaker_cooldown_seconds: '1800',
      initiative_interval_seconds: '21600',
      initiative_enabled: true,
      project_auto_complete: true,
    })
    expect(diffPolicy(f, m12)).toEqual({})
    expect(validatePolicyForm(f, m12)).toEqual({})
  })

  it('offers exactly the role families the backend accepts', () => {
    expect(ROLE_FAMILIES.map((r) => r.value)).toEqual(['founder', 'hr', 'engineering', 'design', 'marketing', 'sales', 'customer_success', 'research', 'qa', 'operations', 'finance', 'generic'])
  })

  it('sends only what changed, with money as micros', () => {
    const f = policyToForm(m12)
    f.m12.cli_run_reservation_micros = '2.5'
    f.m12.max_work_tasks_per_project = '40'
    f.m12.initiative_enabled = false
    f.m12.cli_role_families = ['engineering', 'qa']
    expect(diffPolicy(f, m12)).toEqual({
      cli_run_reservation_micros: 2_500_000,
      max_work_tasks_per_project: 40,
      initiative_enabled: false,
      cli_role_families: ['engineering', 'qa'],
    })
  })

  it('treats a family list as a set and sends null to go back to every family', () => {
    const narrowed: CompanyPolicy = { ...m12, cli_role_families: ['engineering', 'qa'] }
    const f = policyToForm(narrowed)
    f.m12.cli_role_families = ['qa', 'engineering']
    expect(diffPolicy(f, narrowed)).toEqual({})
    f.m12.cli_role_families = null
    expect(diffPolicy(f, narrowed)).toEqual({ cli_role_families: null })
    f.m12.cli_role_families = []
    expect(diffPolicy(f, narrowed)).toEqual({ cli_role_families: [] })
  })

  it('enforces the backend bounds on every whole-number field', () => {
    const f = policyToForm(m12)
    for (const field of M12_NUMBER_FIELDS) f.m12[field.key] = String(field.max + 1)
    const errors = validatePolicyForm(f, m12)
    for (const field of M12_NUMBER_FIELDS) expect(errors[field.key]).toMatch(/At most/)
    for (const field of M12_NUMBER_FIELDS) f.m12[field.key] = String(Math.max(0, field.min - 1))
    const low = validatePolicyForm(f, m12)
    // 0 is valid for reserved slots and the cooldown; every other field has a minimum above zero.
    expect(low.management_reserved_slots).toBeUndefined()
    expect(low.circuit_breaker_cooldown_seconds).toBeUndefined()
    expect(low.max_parallel_runs_per_agent).toMatch(/At least 1/)
    expect(low.initiative_interval_seconds).toMatch(/At least 600/)
    f.m12.max_subtasks_per_task = 'x'
    expect(validatePolicyForm(f, m12).max_subtasks_per_task).toBe('Enter a whole number')
  })

  it('rejects a bad CLI reservation', () => {
    const f = policyToForm(m12)
    f.m12.cli_run_reservation_micros = ''
    expect(validatePolicyForm(f, m12).cli_run_reservation_micros).toBeTruthy()
    f.m12.cli_run_reservation_micros = 'abc'
    expect(validatePolicyForm(f, m12).cli_run_reservation_micros).toMatch(/USD/)
  })

  it('requires reserved slots below max concurrent runs, but only when the reservation itself is being changed', () => {
    const f = policyToForm(m12)
    f.m12.management_reserved_slots = '3'
    expect(validatePolicyForm(f, m12).management_reserved_slots).toBe('Must be lower than max concurrent runs')
    // Lowering max concurrent runs under an unchanged reservation is allowed: the dispatcher clamps it.
    const g = policyToForm(m12)
    g.limits.max_concurrent_runs = '1'
    expect(validatePolicyForm(g, m12).management_reserved_slots).toBeUndefined()
  })
})
