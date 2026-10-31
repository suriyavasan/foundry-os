import { useChannels } from '@/api'
import { Button } from '@/components/ui'
import { ChannelsManager } from '@/features/channels/ChannelsManager'
import { StepHeading } from '../StepFrame'

/**
 * Step 6: chat apps. Always skippable: nothing here is needed to use the company, and it can all be done later in Settings.
 * The button reads "Skip for now" until something is connected, then "Continue"; both just move on.
 */
export function ChannelsStep({ companyId, onDone }: { companyId: string; onDone: () => void }) {
  const q = useChannels(companyId)
  const connected = (q.data?.items.length ?? 0) > 0
  return (
    <div>
      <StepHeading eyebrow="Chat apps" title="Talk to your company from your phone">
        Connect Telegram, Discord or WhatsApp to send instructions, answer your team's questions and approve decisions from a chat. You can also do this later in Settings.
      </StepHeading>
      <ChannelsManager companyId={companyId} />
      <div className="mt-5 flex justify-end">
        <Button variant={connected ? 'primary' : 'secondary'} onClick={onDone}>
          {connected ? 'Continue' : 'Skip for now'}
        </Button>
      </div>
    </div>
  )
}
