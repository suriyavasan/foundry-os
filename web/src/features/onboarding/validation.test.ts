import { describe, expect, it } from 'vitest'
import { LIMITS, mapServerFieldErrors, toCreateCompanyBody, validateOnboarding, type OnboardingValues } from './validation'

const valid = (over: Partial<OnboardingValues> = {}): OnboardingValues => ({
  name: 'Acme',
  mission: '',
  vision: '',
  strategic_goal: '',
  constraints: [],
  timezone: 'UTC',
  founder_name: 'Atlas',
  founder_model_provider: '',
  founder_model_name: '',
  ...over,
})

describe('validateOnboarding', () => {
  it('accepts a minimal valid form', () => {
    expect(validateOnboarding(valid())).toEqual({})
  })
  it('requires a company name (whitespace is empty)', () => {
    expect(validateOnboarding(valid({ name: '   ' })).name).toMatch(/required/i)
  })
  it('enforces the 120 character name limit', () => {
    expect(validateOnboarding(valid({ name: 'x'.repeat(LIMITS.companyName) })).name).toBeUndefined()
    expect(validateOnboarding(valid({ name: 'x'.repeat(LIMITS.companyName + 1) })).name).toMatch(/120/)
  })
  it('requires a provider when a model is given', () => {
    expect(validateOnboarding(valid({ founder_model_name: 'opus' })).founder_model_provider).toBeTruthy()
    expect(validateOnboarding(valid({ founder_model_name: 'opus', founder_model_provider: 'anthropic' }))).toEqual({})
  })
})

describe('toCreateCompanyBody', () => {
  it('trims, drops blanks and defaults the founder name', () => {
    const body = toCreateCompanyBody(valid({ name: '  Acme  ', mission: ' m ', constraints: [' a ', '', 'b'], founder_name: ' ' }))
    expect(body.name).toBe('Acme')
    expect(body.mission).toBe('m')
    expect(body.vision).toBeUndefined()
    expect(body.constraints).toEqual(['a', 'b'])
    expect(body.founder?.name).toBe('Atlas')
    expect(body.founder?.model_provider).toBeUndefined()
  })
})

describe('mapServerFieldErrors', () => {
  it('maps server paths onto form fields', () => {
    expect(
      mapServerFieldErrors({ name: 'too long', 'founder.name': 'bad', 'constraints.0': 'empty', unknown: 'x' }),
    ).toEqual({ name: 'too long', founder_name: 'bad', constraints: 'empty' })
  })
})
