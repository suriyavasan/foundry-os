import type { Agent, Department } from '@/api/types'
import { AgentAvatar } from '@/components/avatar'
import { Chip, StatusBadge, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatUsd } from '@/lib/money'

export function AgentsTable({
  agents,
  departments,
  allAgents,
}: {
  agents: Agent[]
  departments: Department[]
  allAgents: Agent[]
}) {
  const { openPanel } = useCanvas()
  const dept = new Map(departments.map((d) => [d.id, d.name]))
  const names = new Map(allAgents.map((a) => [a.id, a.name]))
  return (
    <Table>
      <THead>
        <TR>
          <TH>Name</TH>
          <TH>Role</TH>
          <TH>Department</TH>
          <TH>Manager</TH>
          <TH>Status</TH>
          <TH>Model</TH>
          <TH>Runtime</TH>
          <TH className="text-right">Budget (mo / day)</TH>
        </TR>
      </THead>
      <TBody>
        {agents.map((a) => (
          <TR key={a.id} interactive>
            <TD>
              <button type="button" onClick={() => openPanel({ kind: 'agent', id: a.id })} className="flex items-center gap-2 text-left font-medium text-fg hover:underline">
                <AgentAvatar agent={a} size="sm" />
                {a.name}
              </button>
            </TD>
            <TD className="text-fg-2">{a.role_title}</TD>
            <TD className="text-fg-2">{(a.department_id && dept.get(a.department_id)) || <span className="text-fg-3">—</span>}</TD>
            <TD className="text-fg-2">
              {a.reports_to_human ? (
                <span className="text-fg-2">You</span>
              ) : a.manager_agent_id ? (
                (names.get(a.manager_agent_id) ?? <span className="text-fg-3">Unknown</span>)
              ) : (
                <span className="text-fg-3">—</span>
              )}
            </TD>
            <TD>
              <StatusBadge status={a.status} />
            </TD>
            <TD className="text-fg-2">
              {a.model_name ? `${a.model_provider ? a.model_provider + ' / ' : ''}${a.model_name}` : <span className="text-fg-3">—</span>}
            </TD>
            <TD>{a.runtime === 'unassigned' ? <span className="text-fg-3">unassigned</span> : <Chip>{a.runtime}</Chip>}</TD>
            <TD className="text-right whitespace-nowrap tabular-nums text-fg-2">
              {formatUsd(a.monthly_budget_micros)} / {formatUsd(a.daily_budget_micros)}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}
