import { ArrowUpRight } from 'lucide-react'
import { useAllTasks } from '@/api'
import type { Agent } from '@/api/types'
import { AgentAvatar } from '@/components/avatar'
import { Badge, Chip, HoverCardContent, MetaRow, StatusBadge, type Tone } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { RING_STYLE, ringStateFor, type RingState } from '@/lib/liveState'
import { formatCost } from '@/lib/money'
import { ROLE_FAMILY_LABEL, inferRoleFamily } from '@/lib/roleFamily'
import { useCanvas } from './CanvasContext'
import { useAgentSpendToday } from './spend'
import { useCompanyTimezone, useTick } from './hooks'

const RING_TONE: Record<RingState, Tone> = {
  working: 'accent',
  idle: 'neutral',
  blocked: 'warning',
  paused: 'neutral',
  proposed: 'info',
  failed: 'danger',
  offline: 'neutral',
  retired: 'neutral',
}

/** Everything the owner wants to know about one employee at a glance. Lives inside a Radix HoverCard. */
export function AgentHoverCardContent({ agent }: { agent: Agent }) {
  const { companyId, openPanel } = useCanvas()
  useTick(15_000)
  const tz = useCompanyTimezone(companyId)
  const tasks = useAllTasks(companyId)
  const spend = useAgentSpendToday(companyId, agent.id, tz)

  const family = inferRoleFamily(agent)
  const ring = ringStateFor(agent)
  const live = agent.live
  const taskRef = live?.current_task ?? null
  const taskTitle = taskRef?.title ?? tasks.data?.find((t) => t.id === taskRef?.id)?.title ?? null

  const liveText =
    live === undefined
      ? 'Live activity is not reported by this backend yet.'
      : (live?.summary ?? (ring === 'idle' ? 'Idle. Waiting for work.' : RING_STYLE[ring].label))

  return (
    <HoverCardContent align="center" side="bottom">
      <div className="flex items-start gap-3">
        <AgentAvatar agent={agent} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold text-fg">{agent.name}</div>
          <div className="truncate text-[12px] text-fg-3">
            {agent.role_title} · {ROLE_FAMILY_LABEL[family]}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <StatusBadge status={agent.status} />
            <Badge tone={RING_TONE[ring]}>{RING_STYLE[ring].label}</Badge>
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-md border border-line bg-raised px-2.5 py-2">
        <p className="text-[12.5px] break-words text-fg">{liveText}</p>
        {taskRef && (
          <button
            type="button"
            onClick={() => openPanel({ kind: 'task', id: taskRef.id })}
            className="mt-1.5 inline-flex max-w-full items-center gap-1 text-left text-[12.5px] font-medium text-accent-fg hover:underline"
          >
            <span className="truncate">{taskTitle ?? 'Open current task'}</span>
            <ArrowUpRight className="size-3 shrink-0" aria-hidden />
          </button>
        )}
        {live?.since && (
          <p className="mt-1 text-[11.5px] text-fg-3">
            {live.state === 'working' ? 'Working for' : 'Since'} {formatRelative(live.since)}
          </p>
        )}
      </div>

      <div className="mt-3 space-y-1">
        <MetaRow label="Model">
          {agent.model_name ? `${agent.model_provider ? agent.model_provider + ' / ' : ''}${agent.model_name}` : '—'}
        </MetaRow>
        <MetaRow label="Runtime">{agent.runtime === 'unassigned' ? 'Not assigned' : <Chip>{agent.runtime}</Chip>}</MetaRow>
        {!spend.unavailable && (
          <MetaRow label="Spend today">
            {spend.loading ? (
              '…'
            ) : spend.row ? (
              <span>
                {formatCost(spend.row.by_kind.verified, 'verified')}
                {spend.row.by_kind.estimated > 0 && <span className="text-fg-3"> · {formatCost(spend.row.by_kind.estimated, 'estimated')}</span>}
              </span>
            ) : (
              formatCost(0, 'verified')
            )}
          </MetaRow>
        )}
      </div>
      <p className="mt-2.5 text-[11.5px] text-fg-3">Click the avatar to open the full profile.</p>
    </HoverCardContent>
  )
}
