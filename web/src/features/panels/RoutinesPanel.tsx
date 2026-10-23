import { useMemo } from 'react'
import { useSchedules } from '@/api'
import { Badge, Boundary, Card, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { describeCadence, sortSchedules } from '@/features/routines/cadence'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatDateTime, formatRelative } from '@/lib/format'

export function RoutinesPanel() {
  const { companyId } = useCanvas()
  const q = useSchedules(companyId)
  const { map } = useAgentMap(companyId)
  const items = useMemo(() => sortSchedules(q.data ?? []), [q.data])
  return (
    <PanelFrame title="Routines" subtitle="Heartbeats and recurring work, fired once per slot">
      <Card>
        <Boundary
          query={q}
          compact={false}
          unavailableTitle="Routines are not built yet"
          unavailableText="Schedules come with the work engine."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No routines yet', description: 'The AI Founder can set up heartbeats and recurring routines; they appear here.' }}
        >
          {() => (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Kind</TH>
                  <TH>Agent</TH>
                  <TH>Cadence</TH>
                  <TH>Next run</TH>
                  <TH>Last fired</TH>
                  <TH>State</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((s) => (
                  <TR key={s.id}>
                    <TD className="font-medium">{s.name}</TD>
                    <TD>
                      <Badge tone={s.kind === 'heartbeat' ? 'info' : 'neutral'}>{s.kind}</Badge>
                    </TD>
                    <TD>
                      <AgentChip agentId={s.agent_id} agents={map} />
                    </TD>
                    <TD className="text-fg-2">{describeCadence(s)}</TD>
                    <TD className="text-fg-2" title={formatDateTime(s.next_run_at)}>
                      {s.enabled && s.next_run_at ? formatRelative(s.next_run_at) : '—'}
                    </TD>
                    <TD className="text-fg-3" title={formatDateTime(s.last_fired_at)}>
                      {s.last_fired_at ? formatRelative(s.last_fired_at) : 'never'}
                    </TD>
                    <TD>{s.enabled ? <Badge tone="success">Enabled</Badge> : <Badge tone="neutral">Disabled</Badge>}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Boundary>
      </Card>
    </PanelFrame>
  )
}
