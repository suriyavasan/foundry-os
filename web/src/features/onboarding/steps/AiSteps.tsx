import { isNotDeployed, useAiStack } from '@/api'
import { Button } from '@/components/ui'
import { AiStack } from '@/features/ai/AiStack'
import { canContinue } from '@/lib/onboarding'
import { StepHeading } from '../StepFrame'

/**
 * Step 4: connect the AI. Add one or more; Continue unlocks when the server says the stack is ready (something tested that
 * covers heavy work and something that covers everyday work). `finishLabel` is "Continue" in the setup flow and
 * "Open my company" when this is the last screen (the gate).
 */
export function AiStep({ companyId, canEdit, finishLabel, onDone }: { companyId: string; canEdit: boolean; finishLabel: string; onDone: () => Promise<void> | void }) {
  const stack = useAiStack(companyId)
  const available = !(stack.isError && isNotDeployed(stack.error))
  const ready = stack.data?.ready === true
  return (
    <div>
      <StepHeading eyebrow="Connect your AI" title="Connect your AI">
        Add the AI your company will use. You can add more later and decide which does the heavy lifting.
      </StepHeading>
      <AiStack companyId={companyId} canEdit={canEdit} />
      <div className="mt-5 flex justify-end">
        <Button variant="primary" disabled={!canContinue('ai', { aiReady: ready, aiAvailable: available })} onClick={() => void onDone()}>
          {finishLabel}
        </Button>
      </div>
    </div>
  )
}
