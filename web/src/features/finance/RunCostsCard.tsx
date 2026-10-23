import { useFinanceRuns } from '@/api'
import { Badge, Boundary, Button, Card, CardHeader, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { RunStatusBadge } from '@/features/shared/badges'
import { formatDateTime, formatRelative } from '@/lib/format'
import { isWastedStatus } from '@/lib/finance'
import { formatCost } from '@/lib/money'

/** Cost of each run in the period, with provenance, linking to the run's detail. */
export function RunCostsCard({ companyId, from, to }: { companyId: string; from: string; to: string }) {
  const q = useFinanceRuns(companyId, from, to)
  const { openPanel } = useCanvas()
  const { map } = useAgentMap(companyId)
  const runs = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <Card>
      <CardHeader title="Cost per run" description="Each run with its cost and how that cost is known" />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        unavailableTitle="Run costs are not built yet"
        unavailableText="Per-run cost reporting comes with guardrails & governance."
        isEmpty={(d) => d.pages.every((p) => p.items.length === 0)}
        empty={{ title: 'No runs in this period', description: 'Runs appear here with their cost once agents work.' }}
      >
        {() => (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>Task</TH>
                  <TH>Agent</TH>
                  <TH>Status</TH>
                  <TH>Model</TH>
                  <TH>Started</TH>
                  <TH className="text-right">Cost</TH>
                </TR>
              </THead>
              <TBody>
                {runs.map((r) => (
                  <TR key={r.run_id} interactive>
                    <TD>
                      <button type="button" className="text-left font-medium hover:underline" onClick={() => openPanel({ kind: 'task', id: r.task_id, run: r.run_id })}>
                        {r.task_title ?? 'Untitled task'}
                      </button>
                    </TD>
                    <TD>
                      <AgentChip agentId={r.agent_id} agents={map} emptyLabel="System" />
                    </TD>
                    <TD>
                      <span className="flex flex-wrap items-center gap-1">
                        <RunStatusBadge status={r.status} />
                        {isWastedStatus(r.status) && <Badge tone="warning">Wasted</Badge>}
                      </span>
                    </TD>
                    <TD className="text-fg-2">{[r.provider, r.model].filter(Boolean).join(' / ') || '—'}</TD>
                    <TD className="text-fg-2" title={formatDateTime(r.started_at)}>
                      {r.started_at ? formatRelative(r.started_at) : '—'}
                    </TD>
                    <TD className="text-right tabular-nums text-fg-2">{formatCost(r.cost_micros, r.cost_kind)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {q.hasNextPage && (
              <div className="border-t border-line p-3 text-center">
                <Button size="sm" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                  Load more runs
                </Button>
              </div>
            )}
          </>
        )}
      </Boundary>
    </Card>
  )
}
