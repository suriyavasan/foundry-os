import { describe, expect, it } from 'vitest'
import { GATE_STEPS, SETUP_STEPS, canContinue, canGoBack, flowReducer, initialFlow, isLastStep, progressText, stepsFor, type FlowEvent, type FlowState } from './onboarding'

const run = (s: FlowState, ...events: FlowEvent[]) => events.reduce(flowReducer, s)

describe('onboarding step machine: first run', () => {
  it('has seven steps in order, the chat apps step between the repository and the team', () => {
    expect(SETUP_STEPS).toEqual(['welcome', 'account', 'company', 'ai', 'repo', 'channels', 'team'])
    expect(stepsFor('setup')).toBe(SETUP_STEPS)
  })

  it('walks welcome -> account -> company -> ai -> repo -> channels -> team', () => {
    let s = initialFlow('setup')
    expect(s.step).toBe('welcome')
    s = run(s, { type: 'next' })
    expect(s.step).toBe('account')
    s = run(s, { type: 'account_created' })
    expect(s).toMatchObject({ step: 'company', accountCreated: true })
    s = run(s, { type: 'company_created', companyId: 'c9', idea: 'Sell lemonade' })
    expect(s).toMatchObject({ step: 'ai', companyId: 'c9', idea: 'Sell lemonade' })
    s = run(s, { type: 'next' })
    expect(s.step).toBe('repo')
    expect(run(s, { type: 'next' }).step).toBe('repo') // the repo step leaves only through repo_done
    s = run(s, { type: 'repo_done' })
    expect(s).toMatchObject({ step: 'channels', repoDone: true })
    expect(isLastStep(s)).toBe(false)
    s = run(s, { type: 'next' }) // the chat apps step is skippable: Skip for now and Continue are both `next`
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
    expect(s.step).toBe('repo')
    expect(canGoBack(s)).toBe(true) // repo -> ai is fine
    expect(run(s, { type: 'back' }).step).toBe('ai')
    s = run(s, { type: 'next' }, { type: 'repo_done' })
    expect(s.step).toBe('channels')
    expect(canGoBack(s)).toBe(false) // channels -> repo would run the repository and the first note again
    expect(run(s, { type: 'back' }).step).toBe('channels')
    s = run(s, { type: 'next' })
    expect(canGoBack(s)).toBe(true) // team -> channels is harmless
    expect(run(s, { type: 'back' }).step).toBe('channels')
  })

  it('shows "Step n of 7"', () => {
    expect(progressText(initialFlow('setup'))).toBe('Step 1 of 7 · Welcome')
    expect(progressText({ mode: 'setup', step: 'ai' })).toBe('Step 4 of 7 · Connect your AI')
    expect(progressText({ mode: 'setup', step: 'repo' })).toBe('Step 5 of 7 · Your code')
    expect(progressText({ mode: 'setup', step: 'channels' })).toBe('Step 6 of 7 · Chat apps')
    expect(progressText({ mode: 'setup', step: 'team' })).toBe('Step 7 of 7 · Meet your team')
  })
})

describe('onboarding step machine: gate-only mode for existing users', () => {
  it('is just the one AI step, "Connect your AI", with the company already known', () => {
    expect(GATE_STEPS).toEqual(['ai'])
    const s = initialFlow('gate', { companyId: 'c1' })
    expect(s).toMatchObject({ mode: 'gate', step: 'ai', companyId: 'c1' })
    expect(progressText(s)).toBe('Step 1 of 1 · Connect your AI')
  })

  it('starts and ends on the AI step, with no way back and nowhere to go next', () => {
    const s = initialFlow('gate', { companyId: 'c1' })
    expect(canGoBack(s)).toBe(false)
    expect(isLastStep(s)).toBe(true)
    expect(run(s, { type: 'next' })).toBe(s)
    expect(run(s, { type: 'back' })).toBe(s)
  })

  it('does not know about the account or company steps', () => {
    const s = initialFlow('gate', { companyId: 'c1' })
    expect(run(s, { type: 'account_created' })).toBe(s)
    expect(run(s, { type: 'company_created', companyId: 'z', idea: '' })).toBe(s)
    expect(run(s, { type: 'repo_done' })).toBe(s)
  })
})

describe('canContinue', () => {
  it('the AI step needs a ready model API and a valid CLI choice, unless the backend has no AI setup at all', () => {
    expect(canContinue('ai', { aiReady: false, aiAvailable: true })).toBe(false)
    expect(canContinue('ai', { aiReady: true, aiAvailable: true })).toBe(true)
    expect(canContinue('ai', { aiReady: false, aiAvailable: false })).toBe(true)
  })
  it('the other steps submit their own forms', () => {
    expect(canContinue('team', { aiReady: false, aiAvailable: true })).toBe(true)
  })
})
