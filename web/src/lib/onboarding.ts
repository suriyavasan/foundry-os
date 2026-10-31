/**
 * The onboarding step machine (docs/specs/m10 §6). Pure, so the rules are tested without a DOM.
 *
 *   setup  (first run): welcome -> account -> company -> ai -> repo -> channels -> team
 *   gate   (an existing company whose AI is not ready): ai
 *
 * Back never goes behind something that already happened: once the account exists there is no way back to the account
 * form, once the company exists there is no way back to the company form, and once the repository step is done (a
 * repository chosen, or "no GitHub") there is no way back to it: it would run the idea's first note and the repository
 * again. The chat apps step after it is skippable ("Skip for now" is the same `next` as Continue). Moving forward is always an explicit
 * event from the step that finished its job (`account_created`, `company_created`, `next`).
 */

export type StepId = 'welcome' | 'account' | 'company' | 'ai' | 'repo' | 'channels' | 'team'
export type FlowMode = 'setup' | 'gate'

export const SETUP_STEPS: readonly StepId[] = ['welcome', 'account', 'company', 'ai', 'repo', 'channels', 'team']
export const GATE_STEPS: readonly StepId[] = ['ai']

export const STEP_LABEL: Record<StepId, string> = {
  welcome: 'Welcome',
  account: 'Your account',
  company: 'Your company',
  ai: 'Connect your AI',
  repo: 'Your code',
  channels: 'Chat apps',
  team: 'Meet your team',
}

export const stepsFor = (mode: FlowMode): readonly StepId[] => (mode === 'setup' ? SETUP_STEPS : GATE_STEPS)

export interface FlowState {
  mode: FlowMode
  step: StepId
  accountCreated: boolean
  /** The repository step is over: a repository was chosen and started, or the Founder chose no GitHub. */
  repoDone: boolean
  companyId: string | null
  /** The one-line idea typed in the company step; it becomes the first note once the AI works (end of the AI step). */
  idea: string
  done: boolean
}

export type FlowEvent =
  | { type: 'next' }
  | { type: 'back' }
  | { type: 'account_created' }
  | { type: 'company_created'; companyId: string; idea: string }
  | { type: 'repo_done' }
  | { type: 'finish' }

export function initialFlow(mode: FlowMode, opts: { companyId?: string | null } = {}): FlowState {
  return {
    mode,
    step: stepsFor(mode)[0]!,
    accountCreated: false,
    repoDone: false,
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
  if (prev === 'repo') return !state.repoDone
  // repo -> ai is allowed (the AI step is the first of the gate, so never back there).
  return true
}

export function flowReducer(state: FlowState, ev: FlowEvent): FlowState {
  const steps = stepsFor(state.mode)
  const i = stepIndex(state)
  switch (ev.type) {
    case 'next':
      if (state.step === 'account' && !state.accountCreated) return state
      if (state.step === 'company' && !state.companyId) return state
      if (state.step === 'repo' && !state.repoDone) return state // leaves through `repo_done`, so the idea is never skipped
      return i < steps.length - 1 ? { ...state, step: steps[i + 1]! } : state
    case 'back':
      return canGoBack(state) ? { ...state, step: steps[i - 1]! } : state
    case 'account_created':
      return state.step === 'account' ? { ...state, accountCreated: true, step: 'company' } : state
    case 'company_created':
      return state.step === 'company' ? { ...state, companyId: ev.companyId, idea: ev.idea, step: 'ai' } : state
    case 'repo_done':
      return state.step === 'repo' ? { ...state, repoDone: true, step: 'channels' } : state
    case 'finish':
      return { ...state, done: true }
  }
}

/** The "Step 2 of 7" line. */
export function progressText(state: Pick<FlowState, 'mode' | 'step'>): string {
  const steps = stepsFor(state.mode)
  return `Step ${stepIndex(state) + 1} of ${steps.length} · ${STEP_LABEL[state.step]}`
}

/**
 * Whether Continue of the AI step is enabled: the server must say the AI stack is ready (M17). A backend without it never
 * blocks. The other steps submit their own forms.
 */
export function canContinue(step: StepId, ctx: { aiReady: boolean; aiAvailable: boolean }): boolean {
  if (step === 'ai') return !ctx.aiAvailable || ctx.aiReady
  return true
}
