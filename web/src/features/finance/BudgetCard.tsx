import type { BudgetStatus } from '@/api/types'
import { Badge, BudgetBar, Card, CardBody, CardHeader, EmptyState, SectionLabel } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import type { Agent } from '@/api/types'
import { budgetRows, utilizationText, type BudgetRow } from '@/lib/finance'
import { formatCost, formatUsd } from '@/lib/money'

const PERIOD_LABEL = { day: 'Daily', month: 'Monthly' } as const

/** Budget utilisation for the company and each agent, with the alert thresholds marked on every bar. */
export function BudgetCard({ budgets, thresholds, agents }: { budgets: BudgetStatus[]; thresholds: number[]; agents: Map<string, Agent> }) {
  const rows = budgetRows(budgets, thresholds)
  const company = rows.filter((r) => r.scope === 'company')
  const byAgent = rows.filter((r) => r.scope === 'agent')
  return (
    <Card>
      <CardHeader title="Budget utilization" description={`Marks show the alert thresholds (${thresholds.join('%, ')}%). Thresholds count verified spend plus estimates.`} />
      {rows.length === 0 ? (
        <EmptyState compact title="No budgets set" description="Set company and agent budgets in Settings (policy) or on an agent's Budget tab." />
      ) : (
        <CardBody className="space-y-4">
          {company.length > 0 && (
            <section aria-label="Company budgets" className="space-y-3">
              <SectionLabel>Company</SectionLabel>
              <ul className="space-y-4">{company.map((r) => <Row key={`c-${r.period}`} row={r} agents={agents} thresholds={thresholds} />)}</ul>
            </section>
          )}
          {byAgent.length > 0 && (
            <section aria-label="Agent budgets" className="space-y-3">
              <SectionLabel>Agents</SectionLabel>
              <ul className="space-y-4">{byAgent.map((r) => <Row key={`a-${r.agentId}-${r.period}`} row={r} agents={agents} thresholds={thresholds} />)}</ul>
            </section>
          )}
        </CardBody>
      )}
    </Card>
  )
}

function Row({ row, agents, thresholds }: { row: BudgetRow; agents: Map<string, Agent>; thresholds: number[] }) {
  const { util } = row
  const tone = util.level === 'exceeded' ? 'danger' : util.level === 'warning' ? 'warning' : 'success'
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <span className="flex min-w-0 items-center gap-1.5 font-medium">
          {row.scope === 'agent' ? <AgentChip agentId={row.agentId} agents={agents} size="xs" /> : 'Company'}
          <span className="font-normal text-fg-3">· {PERIOD_LABEL[row.period]} budget</span>
        </span>
        <span className="flex flex-wrap items-center gap-2 tabular-nums text-fg-2">
          {formatCost(util.verified, 'verified')} charged
          {util.estimated > 0 && <span>+ {formatCost(util.estimated, 'estimated')}</span>}
          <span className="text-fg-3">of {formatUsd(util.limit)}</span>
          <Badge tone={tone}>{utilizationText(util)}</Badge>
        </span>
      </div>
      <BudgetBar util={util} thresholds={thresholds} label={`${row.scope === 'agent' ? 'Agent' : 'Company'} ${PERIOD_LABEL[row.period].toLowerCase()} budget`} />
    </li>
  )
}
