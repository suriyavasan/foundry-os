import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useActivity, useDecisions } from '@/api'
import { Boundary, Button, Card, CardHeader, EmptyState, ErrorState, LoadingState, Select, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { ActivityList } from '@/features/activity/ActivityList'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { CreatorChip } from '@/features/shared/AgentChip'
import { formatDateTime, formatRelative } from '@/lib/format'

const TARGET_TYPES = [
  { value: '', label: 'All targets' },
  { value: 'company', label: 'Company' },
  { value: 'agent', label: 'Agents' },
  { value: 'department', label: 'Departments' },
  { value: 'task', label: 'Tasks' },
  { value: 'goal', label: 'Goals' },
  { value: 'project', label: 'Projects' },
  { value: 'approval', label: 'Approvals' },
  { value: 'artifact', label: 'Artifacts' },
]

export function ActivityPanel() {
  return (
    <PanelFrame title="Activity & decisions" subtitle="An append-only record of everything that changed, and the decisions behind it">
      <Tabs defaultValue="activity">
        <TabsList aria-label="Activity sections">
          <TabsTrigger value="activity">Audit log</TabsTrigger>
          <TabsTrigger value="decisions">Decisions</TabsTrigger>
        </TabsList>
        <TabsContent value="activity">
          <AuditLog />
        </TabsContent>
        <TabsContent value="decisions">
          <Decisions />
        </TabsContent>
      </Tabs>
    </PanelFrame>
  )
}

function AuditLog() {
  const { companyId } = useCanvas()
  const [targetType, setTargetType] = useState('')
  const q = useActivity(companyId, targetType ? { target_type: targetType } : {})
  const events = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <Card>
      <CardHeader
        title="Audit log"
        description="Newest first"
        actions={
          <>
            <Select aria-label="Filter by target type" className="h-7 w-auto text-[12.5px]" value={targetType} onChange={(e) => setTargetType(e.target.value)}>
              {TARGET_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
            <Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={q.isFetching && !q.isFetchingNextPage && !q.isPending} onClick={() => q.refetch()}>
              Refresh
            </Button>
          </>
        }
      />
      {q.isPending ? (
        <LoadingState rows={8} label="Loading activity" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} retrying={q.isFetching} />
      ) : events.length === 0 ? (
        <EmptyState title="No activity" description={targetType ? 'No events for this target type.' : 'State changes in this company are recorded here.'} />
      ) : (
        <>
          <ActivityList events={events} />
          <div className="flex items-center justify-between border-t border-line px-4 py-3">
            <span className="text-[12px] text-fg-3">
              {events.length} event{events.length === 1 ? '' : 's'} loaded
            </span>
            {q.hasNextPage ? (
              <Button size="sm" loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
                Load more
              </Button>
            ) : (
              <span className="text-[12px] text-fg-3">End of log</span>
            )}
          </div>
        </>
      )}
    </Card>
  )
}

function Decisions() {
  const { companyId, openPanel } = useCanvas()
  const q = useDecisions(companyId)
  const { map } = useAgentMap(companyId)
  return (
    <Card>
      <Boundary
        query={q}
        compact={false}
        unavailableTitle="Decisions are not built yet"
        unavailableText="Plans, trade-offs and approvals the AI Founder and you decide on are recorded here with their rationale."
        isEmpty={(d) => d.items.length === 0}
        empty={{ title: 'No decisions recorded yet', description: 'When the AI Founder or a manager makes a notable call, the rationale is logged here.' }}
      >
        {(page) => (
          <ul className="divide-y divide-line">
            {page.items.map((d) => (
              <li key={d.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-[13px] font-medium text-fg">{d.title}</h3>
                  <time dateTime={d.created_at} title={formatDateTime(d.created_at)} className="shrink-0 text-[12px] text-fg-3">
                    {formatRelative(d.created_at)}
                  </time>
                </div>
                {d.rationale && <p className="mt-1 text-[12.5px] whitespace-pre-wrap text-fg-2">{d.rationale}</p>}
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[12px] text-fg-3">
                  <CreatorChip type={d.made_by_type} id={d.made_by_id} agents={map} />
                  {d.related_task_id && (
                    <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: d.related_task_id! })}>
                      Related task
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
    </Card>
  )
}
