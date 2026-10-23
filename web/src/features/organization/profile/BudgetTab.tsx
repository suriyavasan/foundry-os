import { useState, type FormEvent } from 'react'
import type { Agent } from '@/api/types'
import { Button, Card, CardBody, CardHeader, Field, FormError, Input } from '@/components/ui'
import { AgentUsageCard } from './AgentUsageCard'
import { formatUsd, microsToUsdString, parseBudgetInput } from '@/lib/money'
import { useAgentSave } from './useAgentSave'

export function BudgetTab({ agent }: { agent: Agent }) {
  const readOnly = agent.status === 'retired'
  const { save, saving, error, fieldErrors } = useAgentSave(agent)
  const [monthly, setMonthly] = useState(microsToUsdString(agent.monthly_budget_micros))
  const [daily, setDaily] = useState(microsToUsdString(agent.daily_budget_micros))
  const [clientErrors, setClientErrors] = useState<{ monthly?: string; daily?: string }>({})

  async function submit(e: FormEvent) {
    e.preventDefault()
    const m = parseBudgetInput(monthly)
    const d = parseBudgetInput(daily)
    const ce = { monthly: m.error, daily: d.error }
    setClientErrors(ce)
    if (ce.monthly || ce.daily) return
    await save({ monthly_budget_micros: m.value, daily_budget_micros: d.value }, 'Budget saved')
  }

  const monthlyErr = clientErrors.monthly ?? fieldErrors.monthly_budget_micros
  const dailyErr = clientErrors.daily ?? fieldErrors.daily_budget_micros

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Spend limits" description="Leave blank for no limit. Amounts are in USD." />
        <form onSubmit={submit} noValidate>
          <CardBody className="space-y-4">
            <Field label="Monthly budget (USD)" error={monthlyErr} hint={`Currently ${formatUsd(agent.monthly_budget_micros, 'no limit')}`}>
              {({ id, invalid, describedBy }) => (
                <Input id={id} inputMode="decimal" value={monthly} disabled={readOnly} invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setMonthly(e.target.value); setClientErrors({}) }} />
              )}
            </Field>
            <Field label="Daily budget (USD)" error={dailyErr} hint={`Currently ${formatUsd(agent.daily_budget_micros, 'no limit')}`}>
              {({ id, invalid, describedBy }) => (
                <Input id={id} inputMode="decimal" value={daily} disabled={readOnly} invalid={invalid} aria-describedby={describedBy} onChange={(e) => { setDaily(e.target.value); setClientErrors({}) }} />
              )}
            </Field>
            <FormError message={error} />
            <div className="flex justify-end">
              <Button type="submit" variant="primary" loading={saving} disabled={readOnly}>
                Save budget
              </Button>
            </div>
          </CardBody>
        </form>
      </Card>
      <AgentUsageCard agent={agent} />
    </div>
  )
}
