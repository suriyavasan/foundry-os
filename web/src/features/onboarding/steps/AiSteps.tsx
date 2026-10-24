import { useCallback, useState } from 'react'
import { errorMessage, isNotDeployed, useAiSetup, useUpdateAiSetup } from '@/api'
import { Button, FormError } from '@/components/ui'
import { ConnectAiSection } from '@/features/ai/ConnectAiSection'
import { WorkModeSection, type WorkChoice } from '@/features/ai/WorkModeSection'
import { aiSetupUpdate } from '@/lib/aiSetup'
import { canContinue } from '@/lib/onboarding'
import { StepHeading } from '../StepFrame'

/** Step 4: connect the AI. Mandatory: Continue stays disabled until the server says the setup is ready. */
export function ConnectAiStep({ companyId, canEdit, onContinue }: { companyId: string; canEdit: boolean; onContinue: () => void }) {
  const ai = useAiSetup(companyId)
  const aiAvailable = !(ai.isError && isNotDeployed(ai.error))
  const ok = canContinue('ai', { aiReady: ai.data?.ready === true, aiAvailable, workProblem: null })
  return (
    <div>
      <StepHeading eyebrow="Connect your AI" title="Connect your AI">
        Your AI Co-founder and every other employee think through a language model. Connect a provider, test it, and choose the model. Nothing can run without it.
      </StepHeading>
      <ConnectAiSection companyId={companyId} canEdit={canEdit} />
      <div className="mt-6 flex items-center justify-end gap-3 border-t border-line pt-4">
        {!ok && ai.data && <p className="mr-auto text-[12.5px] text-fg-3">Continue unlocks when the test passes.</p>}
        <Button variant="primary" className="h-10 px-5" disabled={!ok} onClick={onContinue}>
          Continue
        </Button>
      </div>
    </div>
  )
}

/**
 * Step 5: how work gets done. Skippable: skipping keeps (or sets) API only. `finishLabel` is "Continue" in the setup flow
 * and "Open my company" when this is the last screen (the gate).
 */
export function WorkModeStep({ companyId, canManage, finishLabel, onDone }: { companyId: string; canManage: boolean; finishLabel: string; onDone: () => Promise<void> | void }) {
  const ai = useAiSetup(companyId)
  const save = useUpdateAiSetup(companyId)
  const [choice, setChoice] = useState<WorkChoice>({ mode: 'api', runtime: null, problem: null })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const aiAvailable = !(ai.isError && isNotDeployed(ai.error))
  const onChoice = useCallback((c: WorkChoice) => setChoice(c), [])

  async function proceed(mode: 'chosen' | 'api') {
    setError(null)
    setBusy(true)
    try {
      if (aiAvailable && ai.data) {
        const wanted = mode === 'api' ? { mode: 'api' as const, runtime: null } : { mode: choice.mode, runtime: choice.runtime }
        const changed = wanted.mode !== ai.data.work_mode || (wanted.mode === 'hybrid' && wanted.runtime !== ai.data.work_runtime)
        const body = aiSetupUpdate(ai.data, wanted.mode, wanted.runtime)
        if (changed && body) await save.mutateAsync(body)
      }
      await onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const ok = canContinue('work', { aiReady: true, aiAvailable, workProblem: choice.problem })
  return (
    <div>
      <StepHeading eyebrow="How work gets done" title="How should work get done?">
        Your API always runs the company: the AI Co-founder, HR, leads and reviews. Choose what your employees use for their hands-on work.
      </StepHeading>
      {aiAvailable ? (
        <WorkModeSection companyId={companyId} canManage={canManage} onChange={onChoice} />
      ) : (
        <p role="status" className="rounded-md border border-line bg-raised p-4 text-[13px] text-fg-2">
          This server predates the AI setup (M10), so there is nothing to choose yet. Every employee works through your model provider.
        </p>
      )}
      <FormError message={error} />
      <div className="mt-6 flex items-center justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" disabled={busy} onClick={() => void proceed('api')}>
          Skip, use API only
        </Button>
        <Button variant="primary" className="h-10 px-5" loading={busy} disabled={!ok} onClick={() => void proceed('chosen')}>
          {finishLabel}
        </Button>
      </div>
    </div>
  )
}
