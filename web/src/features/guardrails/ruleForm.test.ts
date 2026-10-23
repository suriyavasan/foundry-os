import { describe, expect, it } from 'vitest'
import { makeRule } from '@/test/factories'
import { emptyRuleForm, formToRequest, ruleToForm, validateRuleForm, type RuleFormValues } from './ruleForm'

const valid = (over: Partial<RuleFormValues> = {}): RuleFormValues => ({ ...emptyRuleForm(), tier: 'spend', decision: 'human_approval', ...over })

describe('rule form', () => {
  it('requires a tier', () => {
    expect(validateRuleForm(emptyRuleForm()).tier).toBe('Pick a tier')
    expect(validateRuleForm(valid())).toEqual({})
  })

  it('an action rule needs an action name that looks like one', () => {
    expect(validateRuleForm(valid({ targetKind: 'action' })).action).toBeDefined()
    expect(validateRuleForm(valid({ targetKind: 'action', action: 'email send' })).action).toBeDefined()
    expect(validateRuleForm(valid({ targetKind: 'action', action: 'email.send' }))).toEqual({})
    expect(validateRuleForm(valid({ targetKind: 'action', action: '' , tier: '' })).tier).toMatch(/action belongs to/)
  })

  it('a family or agent scope needs its target', () => {
    expect(validateRuleForm(valid({ scopeType: 'family' })).family).toBeDefined()
    expect(validateRuleForm(valid({ scopeType: 'family', family: 'nonsense' })).family).toBeDefined()
    expect(validateRuleForm(valid({ scopeType: 'family', family: 'engineering' }))).toEqual({})
    expect(validateRuleForm(valid({ scopeType: 'agent' })).agentId).toBeDefined()
    expect(validateRuleForm(valid({ scopeType: 'agent', agentId: 'a1' }))).toEqual({})
  })

  it('validates conditions', () => {
    expect(validateRuleForm(valid({ maxAmount: 'abc' })).maxAmount).toBeDefined()
    expect(validateRuleForm(valid({ maxPerDay: '0' })).maxPerDay).toBeDefined()
    expect(validateRuleForm(valid({ maxPerDay: '2.5' })).maxPerDay).toBeDefined()
    expect(validateRuleForm(valid({ domains: ['example.com', '*.acme.io'] }))).toEqual({})
    expect(validateRuleForm(valid({ domains: ['https://example.com'] })).domains).toMatch(/not a host name/)
    expect(validateRuleForm(valid({ domains: ['localhost'] })).domains).toBeDefined()
  })

  it('builds the request with money as exact micros and omits blank conditions', () => {
    expect(formToRequest(valid({ maxAmount: '19.99', maxPerDay: '5', domains: [' Example.COM ', 'example.com'], scopeType: 'family', family: 'sales', decision: 'peer_review' }))).toEqual({
      tier: 'spend',
      action: null,
      scope_type: 'family',
      scope_value: 'sales',
      decision: 'peer_review',
      conditions: { max_amount_micros: 19_990_000, max_per_day: 5, allowed_domains: ['example.com'] },
    })
    expect(formToRequest(valid())).toMatchObject({ scope_type: 'all', scope_value: null, conditions: {} })
    expect(formToRequest(valid({ targetKind: 'action', action: ' email.send ', scopeType: 'agent', agentId: 'a1' }))).toMatchObject({ action: 'email.send', scope_value: 'a1' })
  })

  it('round-trips an existing rule through the form', () => {
    const rule = makeRule({ tier: 'spend', action: 'buy.domain', scope_type: 'agent', scope_value: 'a1', decision: 'deny', conditions: { max_amount_micros: 5_500_000, max_per_day: 2, allowed_domains: ['a.com'] } })
    const back = formToRequest(ruleToForm(rule))
    expect(back).toEqual({ tier: 'spend', action: 'buy.domain', scope_type: 'agent', scope_value: 'a1', decision: 'deny', conditions: { max_amount_micros: 5_500_000, max_per_day: 2, allowed_domains: ['a.com'] } })
  })
})
