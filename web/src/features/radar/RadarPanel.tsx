import { Radar as RadarIcon, Zap } from 'lucide-react'
import { ApiError, errorMessage, isNotDeployed, useUpdatePolicy } from '@/api'
import { useFetchRadarNow, useRadarStatus, type RadarStatus } from '@/api/radar'
import type { UpdatePolicyRequest } from '@/api/types'
import { Badge, Button, Card, ErrorState, LoadingState, NotAvailable, Tabs, TabsContent, TabsList, TabsTrigger, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { formatRelative } from '@/lib/format'
import { BriefsTab } from './BriefsTab'
import { FeedTab } from './FeedTab'
import { IdeasTab } from './IdeasTab'
import { KeywordsTab } from './KeywordsTab'
import { isRadarTab, type RadarTab } from './radarLogic'
import { SourcesTab } from './SourcesTab'
import { useRadarTab } from './useRadarNav'

/**
 * The market radar: what the company reads outside, what it concluded, and the backlog of ideas that came out of it.
 * Briefs, Ideas, Feed, Sources and Keywords. Owners and admins act (accept or reject ideas, switch sources, edit
 * keywords); viewers read. Everything that came from outside is untrusted text and is labelled as such. The tab lives in
 * the URL (`?panel=radar&tab=ideas`).
 */
export function RadarPanel() {
  const { companyId } = useCanvas()
  const q = useRadarStatus(companyId)
  const [tab, setTab] = useRadarTab()
  const canAct = canEditPolicy(useCompanyRole(companyId))

  const frame = (children: React.ReactNode) => (
    <PanelFrame title="Radar" subtitle="What the company reads outside, and what it makes of it">
      {children}
    </PanelFrame>
  )

  if (q.isPending) {
    return frame(
      <Card>
        <LoadingState rows={5} label="Loading the radar" />
      </Card>,
    )
  }
  if (q.isError) {
    return frame(
      <Card>
        {isNotDeployed(q.error) ? (
          <NotAvailable milestone="M16" title="The radar is not built yet" description="Market sources, briefs and the idea backlog come with M16. Until then the company reads nothing from outside." />
        ) : (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        )}
      </Card>,
    )
  }
  if (!q.data.available) {
    return frame(
      <Card>
        <NotAvailable milestone={q.data.milestone ?? 'M16'} title="The radar is not built yet" description="Market sources, briefs and the idea backlog come with M16." />
      </Card>,
    )
  }
  const status = q.data

  return frame(
    <div className="space-y-4">
      <Summary companyId={companyId} status={status} canAct={canAct} />
      <Tabs value={tab} onValueChange={(v) => isRadarTab(v) && setTab(v as RadarTab)}>
        <TabsList aria-label="Radar sections">
          <TabsTrigger value="briefs">Briefs</TabsTrigger>
          <TabsTrigger value="ideas">Ideas{status.open_ideas > 0 ? ` (${status.open_ideas})` : ''}</TabsTrigger>
          <TabsTrigger value="feed">Feed</TabsTrigger>
          <TabsTrigger value="sources">Sources</TabsTrigger>
          <TabsTrigger value="keywords">Keywords</TabsTrigger>
        </TabsList>
        <TabsContent value="briefs">
          <BriefsTab companyId={companyId} status={status} />
        </TabsContent>
        <TabsContent value="ideas">
          <IdeasTab companyId={companyId} canDecide={canAct} />
        </TabsContent>
        <TabsContent value="feed">
          <FeedTab companyId={companyId} canAct={canAct} />
        </TabsContent>
        <TabsContent value="sources">
          <SourcesTab companyId={companyId} canAct={canAct} />
        </TabsContent>
        <TabsContent value="keywords">
          <KeywordsTab companyId={companyId} canAct={canAct} />
        </TabsContent>
      </Tabs>
    </div>,
  )
}

function Summary({ companyId, status, canAct }: { companyId: string; status: RadarStatus; canAct: boolean }) {
  const fetchNow = useFetchRadarNow(companyId)
  const policy = useUpdatePolicy(companyId)
  const toast = useToast()

  const turnOn = () =>
    policy.mutate({ radar_enabled: true } as UpdatePolicyRequest, {
      onSuccess: () => toast.success('The radar is on'),
      onError: (err) => toast.error(errorMessage(err, 'Could not turn the radar on')),
    })
  const runNow = () =>
    fetchNow.mutate(undefined, {
      onSuccess: (r) => (r.enqueued ? toast.success('A fetch was queued') : toast.error(r.message ?? 'Nothing was queued')),
      onError: (err) => toast.error(err instanceof ApiError && err.status === 429 ? 'Too many manual fetches this hour. Try again later.' : errorMessage(err, 'Could not queue a fetch')),
    })

  const runner =
    status.runner_role === 'market_researcher'
      ? `Run by ${status.runner_agent_name ?? 'the Market Researcher'}`
      : status.runner_role === 'cofounder'
        ? `Run by ${status.runner_agent_name ?? 'the AI Co-founder'} (no Market Researcher hired yet)`
        : 'No agent runs the daily radar yet'

  return (
    <div className="space-y-3">
      {!status.enabled && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-raised px-3 py-2.5">
          <div className="flex items-start gap-2.5">
            <RadarIcon className="mt-0.5 size-4 shrink-0 text-fg-3" aria-hidden />
            <div>
              <p className="text-[13px] font-semibold text-fg">The radar is off</p>
              <p className="text-[12.5px] text-fg-3">Nothing is fetched and the radar routines stay quiet. What was collected before is still below.</p>
            </div>
          </div>
          {canAct ? (
            <Button variant="primary" size="sm" loading={policy.isPending} onClick={turnOn}>
              Turn the radar on
            </Button>
          ) : (
            <span className="text-[12px] text-fg-3">Only an owner or admin can turn it on.</span>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[12.5px] text-fg-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>{runner}</span>
          <span>
            {status.sources_enabled} of {status.sources_total} sources on ({status.sources_fetched} fetched, {status.sources_search_only} through web search)
          </span>
          <span>{status.items.new.toLocaleString('en-US')} new items</span>
          {status.items.flagged > 0 && <Badge tone="warning">{status.items.flagged} flagged</Badge>}
          <span>{status.keywords_enabled} keywords on</span>
          <span>Last fetch: {status.last_fetched_at ? formatRelative(status.last_fetched_at) : 'never'}</span>
          <span>Last brief: {status.last_brief_at ? formatRelative(status.last_brief_at) : 'none yet'}</span>
        </div>
        {canAct && status.enabled && (
          <Button size="sm" icon={<Zap className="size-3.5" />} loading={fetchNow.isPending} onClick={runNow}>
            Fetch now
          </Button>
        )}
      </div>
      {status.failing_sources.length > 0 && (
        <div role="alert" className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-warning">
          {status.failing_sources.length} {status.failing_sources.length === 1 ? 'source is' : 'sources are'} failing: {status.failing_sources.slice(0, 4).map((s) => s.name).join(', ')}
          {status.failing_sources.length > 4 ? ` and ${status.failing_sources.length - 4} more` : ''}. See Sources.
        </div>
      )}
    </div>
  )
}
