import { Pencil } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import type { Agent, BudgetStatus } from '@/api/types'
import { Badge, BudgetBar, Button, Card, CardBody, CardHeader, EmptyState, Field, FormError, Input, SectionLabel } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { useAgentSave } from '@/features/organization/profile/useAgentSave'
import { AgentChip } from '@/features/shared/AgentChip'
import { agentBudgets, budgetRows, utilizationText, type AgentBudget, type Utilization } from '@/lib/finance'
import { formatCost, formatUsd, microsToUsdString, parseBudgetInput } from '@/lib/money'

const PERIOD_LABEL = { day: 'Daily', month: 'Monthly' } as const

/**
 * Budget utilisation for the company and each agent, with the alert thresholds marked on every bar. Every working
 * agent is listed with its daily and monthly budget side by side, and owners/admins set both right here.
 */
export function BudgetCard({ budgets, thresholds, agents }: { budgets: BudgetStatus[]; thresholds: number[]; agents: Map<string, Agent> }) {
  const { companyId } = useCanvas()
  const canEdit = canEditPolicy(useCompanyRole(companyId))
  const company = budgetRows(budgets, thresholds).filter((r) => r.scope === 'company')
  const byAgent = agentBudgets(budgets, thresholds)
  return (
    <Card>
      <CardHeader title="Budget utilization" description={`Marks show the alert thresholds (${thresholds.join('%, ')}%). Thresholds count estimates plus verified spend.`} />
      {company.length === 0 && byAgent.length === 0 ? (
        <EmptyState compact title="No budgets set" description="Set company and agent budgets in Settings (policy) or on an agent's Budget tab." />
      ) : (
        <CardBody className="space-y-4">
          {company.length > 0 && (
            <section aria-label="Company budgets" className="space-y-3">
              <SectionLabel>Company</SectionLabel>
              <div className="grid gap-4 md:grid-cols-2">
                {company.map((r) => (
                  <PeriodBudget key={r.period} label={`${PERIOD_LABEL[r.period]} budget`} barLabel={`Company ${PERIOD_LABEL[r.period].toLowerCase()} budget`} util={r.util} thresholds={thresholds} />
                ))}
              </div>
            </section>
          )}
          {byAgent.length > 0 && (
            <section aria-label="Agent budgets" className="space-y-3">
              <SectionLabel>Agents</SectionLabel>
              <ul className="divide-y divide-line">
                {byAgent.map((b) => (
                  <AgentBudgetRow key={b.agentId} budget={b} agent={agents.get(b.agentId)} agents={agents} thresholds={thresholds} canEdit={canEdit} />
                ))}
              </ul>
            </section>
          )}
        </CardBody>
      )}
    </Card>
  )
}

/** Spend against one period's limit. The estimate leads because most providers never report a charge. */
function PeriodBudget({ label, barLabel, util, thresholds }: { label: string; barLabel: string; util: Utilization; thresholds: number[] }) {
  const tone = util.level === 'exceeded' ? 'danger' : util.level === 'warning' ? 'warning' : 'success'
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-[12.5px]">
        <span className="text-fg-3">{label}</span>
        {util.limit !== null ? <Badge tone={tone}>{utilizationText(util)}</Badge> : <span className="text-fg-3">No limit</span>}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2 tabular-nums">
        <span className="text-[13px] font-medium text-fg">{formatCost(util.estimated, 'estimated')}</span>
        <span className="text-[12px] text-fg-3">+ {formatCost(util.verified, 'verified')} charged</span>
        {util.limit !== null && <span className="text-[12px] text-fg-3">of {formatUsd(util.limit)}</span>}
      </div>
      {util.limit !== null && <BudgetBar util={util} thresholds={thresholds} label={barLabel} />}
    </div>
  )
}

function AgentBudgetRow({ budget, agent, agents, thresholds, canEdit }: { budget: AgentBudget; agent: Agent | undefined; agents: Map<string, Agent>; thresholds: number[]; canEdit: boolean }) {
  const [editing, setEditing] = useState(false)
  const editable = canEdit && agent !== undefined && agent.status !== 'retired'
  return (
    <li className="space-y-2 py-3 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <AgentChip agentId={budget.agentId} agents={agents} size="xs" className="min-w-0 text-[13px] font-medium" />
        {editable && !editing && (
          <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" aria-hidden />} aria-label={`Edit budget for ${agent.name}`} onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
      </div>
      {editing && agent ? (
        <BudgetForm agent={agent} onDone={() => setEditing(false)} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <PeriodBudget label="Daily budget" barLabel="Agent daily budget" util={budget.day} thresholds={thresholds} />
          <PeriodBudget label="Monthly budget" barLabel="Agent monthly budget" util={budget.month} thresholds={thresholds} />
        </div>
      )}
    </li>
  )
}

/** Daily and monthly limit for one agent. Blank means no limit; the server refuses agents editing budgets. */
function BudgetForm({ agent, onDone }: { agent: Agent; onDone: () => void }) {
  const { save, saving, error, fieldErrors } = useAgentSave(agent)
  const [daily, setDaily] = useState(microsToUsdString(agent.daily_budget_micros))
  const [monthly, setMonthly] = useState(microsToUsdString(agent.monthly_budget_micros))
  const [clientErrors, setClientErrors] = useState<{ daily?: string; monthly?: string }>({})

  async function submit(e: FormEvent) {
    e.preventDefault()
    const d = parseBudgetInput(daily)
    const m = parseBudgetInput(monthly)
    const ce = { daily: d.error, monthly: m.error }
    setClientErrors(ce)
    if (ce.daily || ce.monthly) return
    if (await save({ daily_budget_micros: d.value, monthly_budget_micros: m.value }, `Budget saved for ${agent.name}`)) onDone()
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Budget for ${agent.name}`} className="space-y-3">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Daily budget (USD)" error={clientErrors.daily ?? fieldErrors.daily_budget_micros} hint="Blank for no limit">
          {({ id, invalid, describedBy }) => (
            <Input id={id} inputMode="decimal" autoFocus value={daily} invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setDaily(e.target.value); setClientErrors({}) }} />
          )}
        </Field>
        <Field label="Monthly budget (USD)" error={clientErrors.monthly ?? fieldErrors.monthly_budget_micros} hint="Blank for no limit">
          {({ id, invalid, describedBy }) => (
            <Input id={id} inputMode="decimal" value={monthly} invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setMonthly(e.target.value); setClientErrors({}) }} />
          )}
        </Field>
      </div>
      <FormError message={error} />
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onDone} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" size="sm" variant="primary" loading={saving}>
          Save budget
        </Button>
      </div>
    </form>
  )
}
