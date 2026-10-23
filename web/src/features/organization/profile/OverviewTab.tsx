import { Link } from 'react-router'
import type { Agent, Department } from '@/api/types'
import { Card, CardBody, CardHeader, Chip, DefinitionList } from '@/components/ui'
import { formatDateTime } from '@/lib/format'

const dash = <span className="text-fg-3">—</span>

export function OverviewTab({
  agent,
  agents,
  departments,
  base,
}: {
  agent: Agent
  agents: Agent[]
  departments: Department[]
  base: string
}) {
  const dept = departments.find((d) => d.id === agent.department_id)
  const manager = agents.find((a) => a.id === agent.manager_agent_id)

  return (
    <div className="grid gap-4 lg:grid-cols-3">
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
              { label: 'Kind', value: <span className="capitalize">{agent.kind}</span> },
              { label: 'Department', value: dept?.name ?? dash },
              {
                label: 'Reports to',
                value: agent.reports_to_human ? (
                  'You (human co-founder)'
                ) : manager ? (
                  <Link to={`${base}/organization/agents/${manager.id}`} className="text-accent-fg hover:underline">
                    {manager.name}
                  </Link>
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
