import { QuestionsPill } from '@/features/commandcenter/QuestionsPill'
import { CommandChat } from '@/features/commandcenter/CommandChat'
import { useCommandCenter } from '@/features/commandcenter/useCommandCenter'
import { Widget } from '@/components/ui'
import { isActiveExchange } from '@/lib/commandCenter'
import { useCanvas } from '../CanvasContext'

/**
 * The Command Center, as a chat: tell the AI Co-founder anything or type a slash command; every input gets an answer right under
 * it, and the agents' questions to you arrive in the same conversation. It shares the first fold with the organization map. The
 * whole history is in the Command Center panel.
 */
export function CommandCenterWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const cc = useCommandCenter(companyId)
  const working = cc.exchanges.filter(isActiveExchange).length

  return (
    <Widget
      title="Command Center"
      subtitle={working > 0 ? `${working} in progress` : undefined}
      actions={<QuestionsPill companyId={companyId} />}
      chat
      onExpand={() => openPanel({ kind: 'command-center' })}
      expandLabel="Open the full Command Center history"
      className={className}
      bodyClassName="overflow-hidden"
    >
      <CommandChat companyId={companyId} cc={cc} compact />
    </Widget>
  )
}
