import { useMemo } from 'react'
import { useSchedules } from '@/api'
import { Badge, Boundary, Widget } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { describeCadence, sortSchedules } from '@/features/routines/cadence'
import { formatRelative } from '@/lib/format'
import { useCanvas } from '../CanvasContext'
import { useAgentMap, useTick } from '../hooks'

export function RoutinesWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useSchedules(companyId)
  const { map } = useAgentMap(companyId)
  useTick(30_000)
  const items = useMemo(() => sortSchedules(q.data ?? []), [q.data])

  return (
    <Widget title="Routines" subtitle="Schedules" onExpand={() => openPanel({ kind: 'routines' })} expandLabel="Open routines" className={className}>
      <Boundary
        query={q}
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No routines yet', description: 'Heartbeats and recurring routines the company runs on a schedule appear here.' }}
        unavailableText="Schedules come with the work engine."
      >
        {() => (
          <ul className="divide-y divide-line">
            {items.map((s) => (
              <li key={s.id} className="flex items-center gap-2.5 px-3.5 py-2">
                <AgentChip agentId={s.agent_id} agents={map} size="sm" hideName />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[12.5px] text-fg">{s.name}</span>
                    <Badge tone={s.kind === 'heartbeat' ? 'info' : 'neutral'}>{s.kind}</Badge>
                  </div>
                  <div className="truncate text-[11.5px] text-fg-3">{describeCadence(s)}</div>
                </div>
                <div className="shrink-0 text-right text-[11.5px]">
                  {s.enabled ? (
                    <>
                      <div className="text-fg-3">Next</div>
                      <time dateTime={s.next_run_at ?? undefined} className="text-fg-2">
                        {s.next_run_at ? formatRelative(s.next_run_at) : 'not scheduled'}
                      </time>
                    </>
                  ) : (
                    <Badge tone="neutral">Disabled</Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
    </Widget>
  )
}
