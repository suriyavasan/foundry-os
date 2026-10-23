import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useActivity } from '@/api'
import { Page } from '@/components/layout/Page'
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  NotAvailable,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui'
import { useCompanyId } from '@/features/shell/useCompanyId'
import { ActivityList } from './ActivityList'

const TARGET_TYPES = [
  { value: '', label: 'All targets' },
  { value: 'company', label: 'Company' },
  { value: 'agent', label: 'Agents' },
  { value: 'department', label: 'Departments' },
]

export function ActivityPage() {
  const companyId = useCompanyId()
  const [targetType, setTargetType] = useState('')
  const q = useActivity(companyId, targetType ? { target_type: targetType } : {})
  const events = q.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <Page title="Activity & Decisions" description="An append-only record of everything that changed in this company, and later, the decisions behind it.">
      <Tabs defaultValue="activity">
        <TabsList aria-label="Activity sections">
          <TabsTrigger value="activity">Audit log</TabsTrigger>
          <TabsTrigger value="decisions">Decisions</TabsTrigger>
        </TabsList>
        <TabsContent value="activity">
          <Card>
            <CardHeader
              title="Audit log"
              description="Newest first"
              actions={
                <>
                  <Select
                    aria-label="Filter by target type"
                    className="h-7 w-auto text-[12.5px]"
                    value={targetType}
                    onChange={(e) => setTargetType(e.target.value)}
                  >
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
              <EmptyState
                title="No activity"
                description={targetType ? 'No events for this target type.' : 'State changes in this company are recorded here.'}
              />
            ) : (
              <>
                <ActivityList events={events} />
                <div className="flex items-center justify-between border-t border-line px-4 py-3">
                  <span className="text-[12px] text-fg-3">{events.length} event{events.length === 1 ? '' : 's'} loaded</span>
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
        </TabsContent>
        <TabsContent value="decisions">
          <Card>
            <NotAvailable
              milestone="M5"
              title="Decisions are not built yet"
              description="Plans, trade-offs and approvals the AI Founder and you decide on will be recorded here with their rationale."
            />
          </Card>
        </TabsContent>
      </Tabs>
    </Page>
  )
}
