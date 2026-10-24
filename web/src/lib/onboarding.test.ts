import { describe, expect, it } from 'vitest'
import { GATE_STEPS, SETUP_STEPS, canContinue, canGoBack, flowReducer, initialFlow, isLastStep, progressText, stepsFor, type FlowEvent, type FlowState } from './onboarding'

const run = (s: FlowState, ...events: FlowEvent[]) => events.reduce(flowReducer, s)

describe('onboarding step machine: first run', () => {
  it('has six steps in order', () => {
    expect(SETUP_STEPS).toEqual(['welcome', 'account', 'company', 'ai', 'work', 'team'])
    expect(stepsFor('setup')).toBe(SETUP_STEPS)
  })

  it('walks welcome -> account -> company -> ai -> work -> team', () => {
    let s = initialFlow('setup')
    expect(s.step).toBe('welcome')
    s = run(s, { type: 'next' })
    expect(s.step).toBe('account')
    s = run(s, { type: 'account_created' })
    expect(s).toMatchObject({ step: 'company', accountCreated: true })
    s = run(s, { type: 'company_created', companyId: 'c9', idea: 'Sell lemonade' })
    expect(s).toMatchObject({ step: 'ai', companyId: 'c9', idea: 'Sell lemonade' })
    s = run(s, { type: 'next' })
    expect(s.step).toBe('work')
    s = run(s, { type: 'next' })
    expect(s.step).toBe('team')
    expect(isLastStep(s)).toBe(true)
    expect(run(s, { type: 'next' }).step).toBe('team')
  })

  it('cannot skip the account or the company', () => {
    let s = run(initialFlow('setup'), { type: 'next' })
    expect(run(s, { type: 'next' }).step).toBe('account') // no account yet
    s = run(s, { type: 'account_created' })
    expect(run(s, { type: 'next' }).step).toBe('company') // no company yet
    expect(run(initialFlow('setup'), { type: 'account_created' }).step).toBe('welcome') // account_created only counts on the account step
    expect(run(s, { type: 'company_created', companyId: 'x', idea: '' }).step).toBe('ai')
  })

  it('never goes back behind something that already happened', () => {
    let s = run(initialFlow('setup'), { type: 'next' })
    expect(canGoBack(s)).toBe(true) // account -> welcome, nothing created yet
    expect(run(s, { type: 'back' }).step).toBe('welcome')
    s = run(s, { type: 'account_created' })
    expect(canGoBack(s)).toBe(false) // company -> account would re-run the account form
    s = run(s, { type: 'company_created', companyId: 'c1', idea: '' })
    expect(canGoBack(s)).toBe(false) // ai -> company
    s = run(s, { type: 'next' })
    expect(canGoBack(s)).toBe(true) // work -> ai is fine
    expect(run(s, { type: 'back' }).step).toBe('ai')
    expect(canGoBack(run(s, { type: 'next' }))).toBe(true) // team -> work
  })

  it('shows "Step n of 6"', () => {
    expect(progressText(initialFlow('setup'))).toBe('Step 1 of 6 · Welcome')
    expect(progressText({ mode: 'setup', step: 'ai' })).toBe('Step 4 of 6 · Connect your AI')
  })
})

describe('onboarding step machine: gate-only mode for existing users', () => {
  it('is just the two AI steps, starting at "Connect your AI", with the company already known', () => {
    expect(GATE_STEPS).toEqual(['ai', 'work'])
    const s = initialFlow('gate', { companyId: 'c1' })
    expect(s).toMatchObject({ mode: 'gate', step: 'ai', companyId: 'c1' })
    expect(progressText(s)).toBe('Step 1 of 2 · Connect your AI')
  })

  it('moves ai -> work, can go back from work, ends on work', () => {
    let s = initialFlow('gate', { companyId: 'c1' })
    expect(canGoBack(s)).toBe(false)
    s = run(s, { type: 'next' })
    expect(s.step).toBe('work')
    expect(isLastStep(s)).toBe(true)
    expect(canGoBack(s)).toBe(true)
    expect(run(s, { type: 'back' }).step).toBe('ai')
    expect(run(s, { type: 'next' }).step).toBe('work')
  })

  it('does not know about the account or company steps', () => {
    const s = initialFlow('gate', { companyId: 'c1' })
    expect(run(s, { type: 'account_created' })).toBe(s)
    expect(run(s, { type: 'company_created', companyId: 'z', idea: '' })).toBe(s)
  })
})

describe('canContinue', () => {
  it('the AI step needs a ready setup, unless the backend has no AI setup at all', () => {
    expect(canContinue('ai', { aiReady: false, aiAvailable: true, workProblem: null })).toBe(false)
    expect(canContinue('ai', { aiReady: true, aiAvailable: true, workProblem: null })).toBe(true)
    expect(canContinue('ai', { aiReady: false, aiAvailable: false, workProblem: null })).toBe(true)
  })
  it('the work step needs a valid choice', () => {
    expect(canContinue('work', { aiReady: true, aiAvailable: true, workProblem: 'Pick a CLI' })).toBe(false)
    expect(canContinue('work', { aiReady: true, aiAvailable: true, workProblem: null })).toBe(true)
  })
})
