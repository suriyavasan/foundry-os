import { useRef } from 'react'
import { isNotDeployed } from '@/api'
import { EmptyState, ErrorState, LoadingState, NotAvailable, Widget } from '@/components/ui'
import { CommandInput, type CommandInputHandle } from '@/features/commandcenter/CommandInput'
import { ExchangeCard } from '@/features/commandcenter/ExchangeCard'
import { useCommandCenter } from '@/features/commandcenter/useCommandCenter'
import { useRefTitles } from '@/features/notes/hooks'
import { isActiveExchange } from '@/lib/commandCenter'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { useCanvas } from '../CanvasContext'
import { useAgentMap, useCompanyRole, useTick } from '../hooks'

/** How many exchanges the widget lists; the whole history is in the Command Center panel. */
const WIDGET_EXCHANGES = 12
const EXAMPLES = ['/help', '/status', '/goals']

/**
 * The Command Center: type a command or tell the AI Co-founder anything; every input gets an output right under it. Slash
 * commands answer at once; free text is read by the AI Co-founder, with its progress shown while it works. It replaces the
 * Founder's-notes widget (free text is still stored as a note; the Notes panel is unchanged).
 */
export function CommandCenterWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const cc = useCommandCenter(companyId)
  const role = useCompanyRole(companyId)
  const { agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const now = useTick(15_000)
  const input = useRef<CommandInputHandle>(null)
  const cofounder = findCofounder(agents)
  const unavailable = isNotDeployed(cc.feed.error)
  const canWrite = role !== 'viewer'
  const working = cc.exchanges.filter(isActiveExchange).length
  const shown = cc.exchanges.slice(0, WIDGET_EXCHANGES)
  const more = cc.exchanges.length > WIDGET_EXCHANGES || cc.feed.hasNextPage

  return (
    <Widget
      title="Command Center"
      subtitle={working > 0 ? `${working} in progress` : undefined}
      tall
      onExpand={() => openPanel({ kind: 'command-center' })}
      expandLabel="Open the full Command Center history"
      className={className}
      bodyClassName="flex flex-col overflow-hidden"
    >
      {!unavailable && canWrite && (
        <div className="shrink-0 border-b border-line p-3">
          <CommandInput companyId={companyId} onSubmit={cc.submit} compact handle={input} />
        </div>
      )}
      {!unavailable && !canWrite && <p className="shrink-0 border-b border-line px-3.5 py-2 text-[12px] text-fg-3">You can read the history. Viewers cannot send commands.</p>}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {cc.feed.isPending ? (
          <LoadingState rows={4} label="Loading the Command Center" />
        ) : unavailable ? (
          <NotAvailable compact className="p-4" milestone="M10" description={`Commands and the ${AI_COFOUNDER_LABEL}'s answers arrive with the Command Center.`} />
        ) : cc.feed.isError ? (
          <ErrorState compact error={cc.feed.error} onRetry={() => void cc.feed.refetch()} retrying={cc.feed.isFetching} />
        ) : cc.exchanges.length === 0 ? (
          <EmptyState
            compact
            title="Nothing sent yet"
            description={`Type / for commands, or tell the ${AI_COFOUNDER_LABEL} anything. Every input gets an answer here.`}
            action={
              canWrite ? (
                <div className="flex flex-wrap justify-center gap-1.5">
                  {EXAMPLES.map((e) => (
                    <button key={e} type="button" onClick={() => input.current?.fill(e)} className="rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-[12px] text-fg-2 transition-colors hover:bg-hover hover:text-fg">
                      {e}
                    </button>
                  ))}
                </div>
              ) : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {shown.map((ex) => (
              <ExchangeCard key={ex.id} exchange={ex} companyId={companyId} cofounderId={cofounder?.id ?? null} titles={titles} now={now} onOpenPanel={openPanel} onRetry={cc.retryExchange} retrying={cc.retryingId === ex.id} />
            ))}
            {more && (
              <li className="px-3.5 py-2 text-[12px] text-fg-3">
                Older exchanges are in the{' '}
                <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'command-center' })}>
                  full history
                </button>
                .
              </li>
            )}
          </ul>
        )}
      </div>
    </Widget>
  )
}
