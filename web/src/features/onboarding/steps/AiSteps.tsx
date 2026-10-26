import { AiSetupForm } from '@/features/ai/AiSetupForm'
import { StepHeading } from '../StepFrame'

/**
 * Step 4: connect the AI, in one step with one save. The model API is required (the save stays disabled until it passed its
 * test); the coding CLIs are optional. `finishLabel` is "Save and continue" in the setup flow and "Save and open my company"
 * when this is the last screen (the gate).
 */
export function AiStep({ companyId, canEdit, finishLabel, onDone }: { companyId: string; canEdit: boolean; finishLabel: string; onDone: () => Promise<void> | void }) {
  return (
    <div>
      <StepHeading eyebrow="Connect your AI" title="Connect your AI">
        Two kinds of AI power your company. The model API is required: it runs the company. Coding CLIs are optional: they do hands-on work. Set both here and save once.
      </StepHeading>
      <AiSetupForm companyId={companyId} canEdit={canEdit} saveLabel={finishLabel} onSaved={onDone} />
    </div>
  )
}
