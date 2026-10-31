import clsx from 'clsx'
import { useMemo } from 'react'
import { useActivity } from '@/api'
import { STREAM_LABEL, useStream } from '@/api/stream'
import { Avatar } from '@/components/avatar'
import { EmptyState, ErrorState, LoadingState, Widget } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'
import { feedItemForAudit, mergeFeed, type FeedItem, type FeedTone } from '@/lib/streamEvents'
import { RunningMeetings } from '@/features/meetings/RunningMeetings'
import { AgentChip } from '@/features/shared/AgentChip'
import { useCanvas } from '../CanvasContext'
import { useAgentMap, useTick } from '../hooks'

const TONE_DOT: Record<FeedTone, string> = {
  neutral: 'bg-neutral',
  accent: 'bg-accent',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

/** Newest-first stream of what agents are doing right now, from the SSE feed plus the audit log. */
export function LiveActivityWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const stream = useStream()
  const audit = useActivity(companyId, {}, 30)
  const { map } = useAgentMap(companyId)
  useTick(30_000)

  const items = useMemo(() => {
    const seeded = (audit.data?.pages[0]?.items ?? []).map(feedItemForAudit)
    return mergeFeed([stream.feed, seeded], 40)
  }, [stream.feed, audit.data])

  const live = stream.status === 'live'

  return (
    <Widget
      title="Live activity"
      subtitle={
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden data-live={live} className={clsx('cos-live-dot inline-block size-1.5 rounded-full', live ? 'bg-success' : 'bg-neutral')} />
          {live ? 'Streaming' : STREAM_LABEL[stream.status]}
        </span>
      }
      tall
      onExpand={() => openPanel({ kind: 'activity' })}
      expandLabel="Open full activity log"
      className={className}
    >
      <RunningMeetings />
      {audit.isPending && items.length === 0 ? (
        <LoadingState rows={6} label="Loading activity" />
      ) : audit.isError && items.length === 0 ? (
        <ErrorState compact error={audit.error} onRetry={() => void audit.refetch()} retrying={audit.isFetching} />
      ) : items.length === 0 ? (
        <EmptyState compact title="Nothing has happened yet" description="Agent runs, decisions and changes appear here the moment they happen." />
      ) : (
        <ol aria-live="off" className="divide-y divide-line">
          {items.map((item) => (
            <FeedRow key={item.key} item={item} agents={map} onOpen={item.panel ? () => openPanel(item.panel!) : undefined} />
          ))}
        </ol>
      )}
    </Widget>
  )
}

function FeedRow({ item, agents, onOpen }: { item: FeedItem; agents: ReturnType<typeof useAgentMap>['map']; onOpen?: () => void }) {
  const agent = item.agentId ? agents.get(item.agentId) : undefined
  const body = (
    <>
      <span className="mt-0.5 shrink-0">
        {agent ? <AgentChip agentId={agent.id} agents={agents} size="sm" hideName /> : <Avatar family="generic" size="sm" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] leading-snug break-words text-fg">
          {agent && <span className="font-medium">{agent.name} </span>}
          <span className={agent ? 'text-fg-2' : undefined}>{item.text}</span>
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-fg-3">
          <span aria-hidden className={clsx('inline-block size-1.5 rounded-full', TONE_DOT[item.tone])} />
          <time dateTime={item.at} title={formatDateTime(item.at)}>
            {formatRelative(item.at)}
          </time>
        </span>
      </span>
    </>
  )
  return (
    <li className="cos-feed-item">
      {onOpen ? (
        <button type="button" onClick={onOpen} className="flex w-full items-start gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-hover">
          {body}
        </button>
      ) : (
        <div className="flex items-start gap-2.5 px-3.5 py-2">{body}</div>
      )}
    </li>
  )
}
