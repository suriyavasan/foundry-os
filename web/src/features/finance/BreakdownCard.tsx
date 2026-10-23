import { useMemo, useState } from 'react'
import { useFinanceBreakdown, useProjects } from '@/api'
import type { FinanceBreakdownRow, FinanceGroupBy } from '@/api/types'
import { Boundary, Card, CardHeader, CostKindBadge, Select, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useAgentMap } from '@/features/canvas/hooks'
import { formatCost } from '@/lib/money'

const GROUPS: Array<{ value: FinanceGroupBy; label: string }> = [
  { value: 'agent', label: 'Agent' },
  { value: 'project', label: 'Project' },
  { value: 'provider', label: 'Provider' },
  { value: 'model', label: 'Model' },
  { value: 'runtime', label: 'Runtime' },
]

/** Spend by agent, project, provider, model or runtime; charged and estimated in separate columns. */
export function BreakdownCard({ companyId, from, to }: { companyId: string; from: string; to: string }) {
  const [group, setGroup] = useState<FinanceGroupBy>('agent')
  const q = useFinanceBreakdown(companyId, group, from, to)
  const { map } = useAgentMap(companyId)
  const projects = useProjects(companyId)
  const projectName = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.name])), [projects.data])
  const label = (r: FinanceBreakdownRow) => r.label ?? (group === 'agent' ? map.get(r.key)?.name : group === 'project' ? projectName.get(r.key) : undefined) ?? r.key
  return (
    <Card>
      <CardHeader
        title="Breakdown"
        description="Where the money went in this period"
        actions={
          <Select aria-label="Break down by" className="h-7 w-auto text-[12.5px]" value={group} onChange={(e) => setGroup(e.target.value as FinanceGroupBy)}>
            {GROUPS.map((g) => (
              <option key={g.value} value={g.value}>
                By {g.label.toLowerCase()}
              </option>
            ))}
          </Select>
        }
      />
      <Boundary query={q} milestone="M6" compact={false} isEmpty={(d) => d.length === 0} empty={{ title: 'No usage in this period', description: 'Spend appears here once agents run.' }}>
        {(rows) => (
          <Table>
            <THead>
              <TR>
                <TH>{GROUPS.find((g) => g.value === group)!.label}</TH>
                <TH className="text-right">
                  <span className="inline-flex items-center gap-1.5">
                    <CostKindBadge kind="verified" /> charged
                  </span>
                </TH>
                <TH className="text-right">
                  <CostKindBadge kind="estimated" />
                </TH>
                <TH className="text-right">Runs</TH>
                <TH className="text-right">Tokens in / out</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.key}>
                  <TD className="font-medium">{label(r)}</TD>
                  <TD className="text-right tabular-nums">{formatCost(r.verified_micros, 'verified')}</TD>
                  <TD className="text-right tabular-nums text-fg-2">{r.estimated_micros > 0 ? formatCost(r.estimated_micros, 'estimated') : '—'}</TD>
                  <TD className="text-right tabular-nums text-fg-2">{r.runs.toLocaleString()}</TD>
                  <TD className="text-right tabular-nums text-fg-2">
                    {r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
    </Card>
  )
}
