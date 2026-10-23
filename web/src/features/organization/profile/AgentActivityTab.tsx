import { RefreshCw } from 'lucide-react'
import { useActivity } from '@/api'
import { Button, Card, EmptyState, ErrorState, LoadingState } from '@/components/ui'
import { ActivityList } from '@/features/activity/ActivityList'

export function AgentActivityTab({ companyId, agentId }: { companyId: string; agentId: string }) {
  const q = useActivity(companyId, { target_type: 'agent', target_id: agentId }, 25)
  const events = q.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <Card>
      {q.isPending ? (
        <LoadingState rows={5} label="Loading activity" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} retrying={q.isFetching} />
      ) : events.length === 0 ? (
        <EmptyState title="No activity for this agent" description="Changes to this agent are recorded here." />
      ) : (
        <>
          <ActivityList events={events} />
          {q.hasNextPage && (
            <div className="flex justify-center border-t border-line p-3">
              <Button
                size="sm"
                icon={<RefreshCw className="size-3.5" />}
                loading={q.isFetchingNextPage}
                onClick={() => q.fetchNextPage()}
              >
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  )
}
