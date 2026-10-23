import type { Agent, Department } from '@/api/types'
import { Badge, Card, CardBody, CardHeader, Chip, DefinitionList } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatDateTime, formatRelative } from '@/lib/format'
import { RING_STYLE, ringStateFor } from '@/lib/liveState'
import { ROLE_FAMILY_LABEL, inferRoleFamily } from '@/lib/roleFamily'
import { AGENT_KIND_LABEL } from '@/lib/terms'

const dash = <span className="text-fg-3">—</span>

export function OverviewTab({
  agent,
  agents,
  departments,
}: {
  agent: Agent
  agents: Agent[]
  departments: Department[]
}) {
  const { openPanel } = useCanvas()
  const ring = ringStateFor(agent)
  const live = agent.live
  const dept = departments.find((d) => d.id === agent.department_id)
  const manager = agents.find((a) => a.id === agent.manager_agent_id)

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-3">
        <CardHeader title="Right now" actions={<Badge tone={ring === 'working' ? 'accent' : ring === 'blocked' ? 'warning' : ring === 'failed' ? 'danger' : 'neutral'}>{RING_STYLE[ring].label}</Badge>} />
        <CardBody>
          {live === undefined ? (
            <p className="text-[13px] text-fg-3">Live activity is not reported by this backend yet.</p>
          ) : (
            <div className="space-y-1">
              <p className="text-[13px] text-fg">{live?.summary ?? (ring === 'idle' ? 'Idle. Waiting for work.' : RING_STYLE[ring].label)}</p>
              {live?.current_task && (
                <button type="button" onClick={() => openPanel({ kind: 'task', id: live.current_task!.id })} className="text-[13px] font-medium text-accent-fg hover:underline">
                  {live.current_task.title ?? 'Open current task'}
                </button>
              )}
              {live?.since && <p className="text-[12px] text-fg-3">Since {formatRelative(live.since)}</p>}
            </div>
          )}
        </CardBody>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader title="Role" />
        <CardBody className="space-y-4">
          <div>
            <div className="mb-1 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Responsibilities</div>
            {agent.responsibilities ? (
              <p className="text-[13px] whitespace-pre-wrap text-fg">{agent.responsibilities}</p>
            ) : (
              <p className="text-[13px] text-fg-3">No responsibilities written yet. Add them under Configuration.</p>
            )}
          </div>
          <div>
            <div className="mb-1 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Instructions</div>
            {agent.instructions ? (
              <p className="max-h-60 overflow-y-auto text-[13px] whitespace-pre-wrap text-fg-2">{agent.instructions}</p>
            ) : (
              <p className="text-[13px] text-fg-3">No instructions.</p>
            )}
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Details" />
        <CardBody>
          <DefinitionList
            items={[
              { label: 'Kind', value: AGENT_KIND_LABEL[agent.kind] ?? agent.kind },
              { label: 'Employee type', value: ROLE_FAMILY_LABEL[inferRoleFamily(agent)] },
              { label: 'Department', value: dept?.name ?? dash },
              {
                label: 'Reports to',
                value: agent.reports_to_human ? (
                  'You (the Founder)'
                ) : manager ? (
                  <button type="button" onClick={() => openPanel({ kind: 'agent', id: manager.id })} className="text-accent-fg hover:underline">
                    {manager.name}
                  </button>
                ) : agent.manager_agent_id ? (
                  <span className="text-fg-3">Unknown agent</span>
                ) : (
                  dash
                ),
              },
              {
                label: 'Model',
                value: agent.model_name
                  ? `${agent.model_provider ? agent.model_provider + ' / ' : ''}${agent.model_name}`
                  : dash,
              },
              {
                label: 'Runtime',
                value: agent.runtime === 'unassigned' ? <span className="text-fg-3">Not assigned</span> : <Chip>{agent.runtime}</Chip>,
              },
              { label: 'Created', value: formatDateTime(agent.created_at) },
              { label: 'Status changed', value: formatDateTime(agent.status_changed_at) },
            ]}
          />
        </CardBody>
      </Card>
    </div>
  )
}
