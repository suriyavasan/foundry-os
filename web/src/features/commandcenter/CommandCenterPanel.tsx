import { isNotDeployed } from '@/api'
import { Card } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { SLASH_COMMANDS, usageOf } from '@/lib/commandCenter'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { QuestionsPill } from './QuestionsPill'
import { CommandChat } from './CommandChat'
import { useCommandCenter } from './useCommandCenter'

/** `?panel=command-center`: the whole history, newest first, with the same input and the full command list. */
export function CommandCenterPanel() {
  const { companyId } = useCanvas()
  const cc = useCommandCenter(companyId)
  const unavailable = isNotDeployed(cc.feed.error)

  return (
    <PanelFrame title="Command Center" actions={<QuestionsPill companyId={companyId} inPanel />} subtitle={`The whole conversation. Free text goes to the ${AI_COFOUNDER_LABEL}; anything starting with / is a command.`}>
      <div className="space-y-4">
        <Card className="h-[min(720px,calc(100vh-14rem))] min-h-[420px] overflow-hidden">
          <CommandChat companyId={companyId} cc={cc} inputId="command-center-input-panel" autoFocus />
        </Card>
        {!unavailable && (
          <Card>
            <div className="border-b border-line px-4 py-3">
              <h2 className="text-[13px] font-semibold text-fg">Commands</h2>
              <p className="mt-0.5 text-[12px] text-fg-3">They run at once, with your permissions. Anything else is a message for the {AI_COFOUNDER_LABEL}.</p>
            </div>
            <dl className="divide-y divide-line">
              {SLASH_COMMANDS.map((c) => (
                <div key={c.name} className="grid grid-cols-[minmax(0,15rem)_1fr] gap-3 px-4 py-2 text-[12.5px]">
                  <dt className="font-mono text-fg">{usageOf(c)}</dt>
                  <dd className="text-fg-3">
                    {c.description}
                    {c.changes ? ' · changes things' : ''}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        )}
      </div>
    </PanelFrame>
  )
}
