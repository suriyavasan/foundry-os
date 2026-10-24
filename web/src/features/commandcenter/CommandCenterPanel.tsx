import { isNotDeployed } from '@/api'
import { Button, Card, EmptyState, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useAgentMap, useCompanyRole, useTick } from '@/features/canvas/hooks'
import { useRefTitles } from '@/features/notes/hooks'
import { SLASH_COMMANDS, usageOf } from '@/lib/commandCenter'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { CommandInput } from './CommandInput'
import { ExchangeCard } from './ExchangeCard'
import { useCommandCenter } from './useCommandCenter'

/** `?panel=command-center`: the whole history, newest first, with the same input and the full command list. */
export function CommandCenterPanel() {
  const { companyId, openPanel } = useCanvas()
  const cc = useCommandCenter(companyId)
  const role = useCompanyRole(companyId)
  const { agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const now = useTick(15_000)
  const cofounder = findCofounder(agents)
  const unavailable = isNotDeployed(cc.feed.error)
  const canWrite = role !== 'viewer'

  return (
    <PanelFrame title="Command Center" subtitle={`Everything you typed, and what came back. Free text goes to the ${AI_COFOUNDER_LABEL}.`}>
      <div className="space-y-4">
        {!unavailable && canWrite && (
          <Card className="p-3">
            <CommandInput companyId={companyId} onSubmit={cc.submit} id="command-center-input-panel" autoFocus />
          </Card>
        )}
        <Card>
          {cc.feed.isPending ? (
            <LoadingState rows={5} label="Loading the Command Center" />
          ) : unavailable ? (
            <NotAvailable milestone="M10" title="The Command Center is not built yet" description={`Commands and the ${AI_COFOUNDER_LABEL}'s answers arrive with this backend's Command Center.`} />
          ) : cc.feed.isError ? (
            <ErrorState error={cc.feed.error} onRetry={() => void cc.feed.refetch()} retrying={cc.feed.isFetching} />
          ) : cc.exchanges.length === 0 ? (
            <EmptyState title="Nothing sent yet" description="Type /help to see what you can do from here." />
          ) : (
            <>
              <ul className="divide-y divide-line">
                {cc.exchanges.map((ex) => (
                  <ExchangeCard key={ex.id} exchange={ex} companyId={companyId} cofounderId={cofounder?.id ?? null} titles={titles} now={now} onOpenPanel={openPanel} onRetry={cc.retryExchange} retrying={cc.retryingId === ex.id} />
                ))}
              </ul>
              {cc.feed.hasNextPage && (
                <div className="border-t border-line p-3 text-center">
                  <Button loading={cc.feed.isFetchingNextPage} onClick={() => void cc.feed.fetchNextPage()}>
                    Load older
                  </Button>
                </div>
              )}
            </>
          )}
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
