import { useMemo, useState } from 'react'
import { usePolicy, useUsage } from '@/api'
import type { UsageGroupBy, UsageRow } from '@/api/types'
import { Boundary, Card, CardBody, CardHeader, CostKindBadge, Select, SectionLabel, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap, useCompanyTimezone } from '@/features/canvas/hooks'
import { useCompanyDates } from '@/features/canvas/spend'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { SpendBlock } from '@/features/finance/SpendBlock'
import { COST_KIND_LABEL, formatCost } from '@/lib/money'
import { emptyRow, sumRows } from '@/lib/usage'
import { useProjects } from '@/api'

type Range = 'today' | '7d' | 'month'
const GROUPS: Array<{ value: UsageGroupBy; label: string }> = [
  { value: 'agent', label: 'By agent' },
  { value: 'project', label: 'By project' },
  { value: 'model', label: 'By model' },
  { value: 'day', label: 'By day' },
]

export function FinancePanel() {
  const { companyId } = useCanvas()
  const tz = useCompanyTimezone(companyId)
  const { today, monthStart, weekStart } = useCompanyDates(tz)
  const policy = usePolicy(companyId)
  const [group, setGroup] = useState<UsageGroupBy>('agent')
  const [range, setRange] = useState<Range>('month')
  const from = range === 'today' ? today : range === 'month' ? monthStart : weekStart
  const q = useUsage(companyId, group, from, today)
  const daily = useUsage(companyId, 'day', today, today)
  const month = useUsage(companyId, 'day', monthStart, today)
  const { map } = useAgentMap(companyId)
  const projects = useProjects(companyId)
  const projectName = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.name])), [projects.data])

  const labelFor = (row: UsageRow): string => {
    if (row.label) return row.label
    if (group === 'agent') return map.get(row.key)?.name ?? row.key
    if (group === 'project') return projectName.get(row.key) ?? row.key
    return row.key
  }

  return (
    <PanelFrame title="Finance" subtitle="Spend by provenance: verified charges are never mixed with estimates">
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardBody>
              <Boundary query={daily} unavailableText="The usage ledger comes with the work engine.">
                {(r) => <SpendBlock title="Today" row={r.rows.find((x) => x.key === today) ?? emptyRow(today)} limitMicros={policy.data?.daily_budget_micros ?? null} />}
              </Boundary>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <Boundary query={month} unavailableText="The usage ledger comes with the work engine.">
                {(r) => <SpendBlock title="This month" row={sumRows(r.rows)} limitMicros={policy.data?.monthly_budget_micros ?? null} />}
              </Boundary>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader
            title="Usage"
            description="Aggregates from the usage ledger"
            actions={
              <>
                <Select aria-label="Group by" className="h-7 w-auto text-[12.5px]" value={group} onChange={(e) => setGroup(e.target.value as UsageGroupBy)}>
                  {GROUPS.map((g) => (
                    <option key={g.value} value={g.value}>
                      {g.label}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Period" className="h-7 w-auto text-[12.5px]" value={range} onChange={(e) => setRange(e.target.value as Range)}>
                  <option value="today">Today</option>
                  <option value="7d">Last 7 days</option>
                  <option value="month">This month</option>
                </Select>
              </>
            }
          />
          <Boundary query={q} compact={false} unavailableTitle="Finance is not built yet" unavailableText="Usage and budgets come with the work engine." isEmpty={(d) => d.rows.length === 0} empty={{ title: 'No usage in this period', description: 'Spend appears here once agents start running.' }}>
            {(report) => (
              <>
                <Table>
                  <THead>
                    <TR>
                      <TH>{GROUPS.find((g) => g.value === group)?.label.replace('By ', '')}</TH>
                      <TH className="text-right">
                        <span className="inline-flex items-center gap-1.5">
                          <CostKindBadge kind="verified" /> charged
                        </span>
                      </TH>
                      <TH className="text-right">
                        <span className="inline-flex items-center gap-1.5">
                          <CostKindBadge kind="estimated" />
                        </span>
                      </TH>
                      <TH className="text-right">Tokens in / out</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {report.rows.map((r) => (
                      <TR key={r.key}>
                        <TD className="font-medium">{labelFor(r)}</TD>
                        <TD className="text-right tabular-nums">{formatCost(r.by_kind.verified, 'verified')}</TD>
                        <TD className="text-right tabular-nums text-fg-2">{r.by_kind.estimated > 0 ? formatCost(r.by_kind.estimated, 'estimated') : '—'}</TD>
                        <TD className="text-right tabular-nums text-fg-2">
                          {r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}
                        </TD>
                      </TR>
                    ))}
                    <TR className="bg-raised font-medium">
                      <TD>Total</TD>
                      <TD className="text-right tabular-nums">{formatCost(report.totals.by_kind.verified, 'verified')}</TD>
                      <TD className="text-right tabular-nums text-fg-2">{report.totals.by_kind.estimated > 0 ? formatCost(report.totals.by_kind.estimated, 'estimated') : '—'}</TD>
                      <TD className="text-right tabular-nums text-fg-2">
                        {report.totals.input_tokens.toLocaleString()} / {report.totals.output_tokens.toLocaleString()}
                      </TD>
                    </TR>
                  </TBody>
                </Table>
                <div className="border-t border-line px-4 py-3">
                  <SectionLabel>How to read this</SectionLabel>
                  <p className="mt-1 text-[12.5px] text-fg-3">
                    <strong className="font-medium text-fg-2">{COST_KIND_LABEL.verified}</strong> amounts were reported by the provider or runtime and are real charges.{' '}
                    <strong className="font-medium text-fg-2">{COST_KIND_LABEL.estimated}</strong> amounts are computed from token counts and list prices; the real bill may differ. Usage with an unreported cost is not given a price.
                  </p>
                </div>
              </>
            )}
          </Boundary>
        </Card>
      </div>
    </PanelFrame>
  )
}
