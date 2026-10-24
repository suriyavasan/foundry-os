/**
 * The onboarding step machine (docs/specs/m10 §6). Pure, so the rules are tested without a DOM.
 *
 *   setup  (first run): welcome -> account -> company -> ai -> work -> team
 *   gate   (an existing company whose AI is not ready): ai -> work
 *
 * Back never goes behind something that already happened: once the account exists there is no way back to the account
 * form, and once the company exists there is no way back to the company form. Moving forward is always an explicit
 * event from the step that finished its job (`account_created`, `company_created`, `next`).
 */

export type StepId = 'welcome' | 'account' | 'company' | 'ai' | 'work' | 'team'
export type FlowMode = 'setup' | 'gate'

export const SETUP_STEPS: readonly StepId[] = ['welcome', 'account', 'company', 'ai', 'work', 'team']
export const GATE_STEPS: readonly StepId[] = ['ai', 'work']

export const STEP_LABEL: Record<StepId, string> = {
  welcome: 'Welcome',
  account: 'Your account',
  company: 'Your company',
  ai: 'Connect your AI',
  work: 'How work gets done',
  team: 'Meet your team',
}

export const stepsFor = (mode: FlowMode): readonly StepId[] => (mode === 'setup' ? SETUP_STEPS : GATE_STEPS)

export interface FlowState {
  mode: FlowMode
  step: StepId
  accountCreated: boolean
  companyId: string | null
  /** The one-line idea typed in the company step; it becomes the first note once the AI works (end of the work step). */
  idea: string
  done: boolean
}

export type FlowEvent =
  | { type: 'next' }
  | { type: 'back' }
  | { type: 'account_created' }
  | { type: 'company_created'; companyId: string; idea: string }
  | { type: 'finish' }

export function initialFlow(mode: FlowMode, opts: { companyId?: string | null } = {}): FlowState {
  return {
    mode,
    step: stepsFor(mode)[0]!,
    accountCreated: false,
    companyId: opts.companyId ?? null,
    idea: '',
    done: false,
  }
}

export function stepIndex(state: Pick<FlowState, 'mode' | 'step'>): number {
  return stepsFor(state.mode).indexOf(state.step)
}

export function isLastStep(state: Pick<FlowState, 'mode' | 'step'>): boolean {
  const steps = stepsFor(state.mode)
  return state.step === steps[steps.length - 1]
}

export function canGoBack(state: FlowState): boolean {
  const i = stepIndex(state)
  if (i <= 0) return false
  const prev = stepsFor(state.mode)[i - 1]!
  if (prev === 'account') return !state.accountCreated
  if (prev === 'company') return !state.companyId
  if (prev === 'welcome') return !state.accountCreated
  // gate: the AI step is the first, so never; work -> ai is allowed.
  return true
}

export function flowReducer(state: FlowState, ev: FlowEvent): FlowState {
  const steps = stepsFor(state.mode)
  const i = stepIndex(state)
  switch (ev.type) {
    case 'next':
      if (state.step === 'account' && !state.accountCreated) return state
      if (state.step === 'company' && !state.companyId) return state
      return i < steps.length - 1 ? { ...state, step: steps[i + 1]! } : state
    case 'back':
      return canGoBack(state) ? { ...state, step: steps[i - 1]! } : state
    case 'account_created':
      return state.step === 'account' ? { ...state, accountCreated: true, step: 'company' } : state
    case 'company_created':
      return state.step === 'company' ? { ...state, companyId: ev.companyId, idea: ev.idea, step: 'ai' } : state
    case 'finish':
      return { ...state, done: true }
  }
}

/** The "Step 2 of 6" line. */
export function progressText(state: Pick<FlowState, 'mode' | 'step'>): string {
  const steps = stepsFor(state.mode)
  return `Step ${stepIndex(state) + 1} of ${steps.length} · ${STEP_LABEL[state.step]}`
}

/**
 * Whether the Continue of a step is enabled. The AI step needs a ready setup (the server said so); the work step needs a
 * valid choice (see `workModeProblem` in lib/aiSetup); the other steps submit their own forms.
 */
export function canContinue(step: StepId, ctx: { aiReady: boolean; aiAvailable: boolean; workProblem: string | null }): boolean {
  if (step === 'ai') return ctx.aiReady || !ctx.aiAvailable
  if (step === 'work') return !ctx.aiAvailable || ctx.workProblem === null
  return true
}
