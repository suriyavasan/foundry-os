import { useState } from 'react'
import { isNotDeployed, useFinanceSummary, usePolicy } from '@/api'
import type { FinancePeriod } from '@/api/types'
import { Card, CardBody, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap, useCompanyTimezone } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useCompanyDates } from '@/features/canvas/spend'
import { BasicFinance } from '@/features/finance/BasicFinance'
import { BreakdownCard } from '@/features/finance/BreakdownCard'
import { BudgetCard } from '@/features/finance/BudgetCard'
import { ExpensesCard } from '@/features/finance/ExpensesCard'
import { PeriodBar } from '@/features/finance/PeriodBar'
import { RunCostsCard } from '@/features/finance/RunCostsCard'
import { TotalsCards } from '@/features/finance/TotalsCards'
import { TrendCard } from '@/features/finance/TrendCard'
import { DEFAULT_THRESHOLDS, normalizeThresholds, periodRange } from '@/lib/finance'

/**
 * Finance: spend by provenance for a day, week or month in company time, budgets against their alert thresholds,
 * breakdown, trend, wasted cost, external expenses and per-run cost. Before M6 (no `/finance/*`) it falls back to
 * the usage-ledger view and says what is missing.
 */
export function FinancePanel() {
  const { companyId } = useCanvas()
  const tz = useCompanyTimezone(companyId)
  const { today } = useCompanyDates(tz)
  const policy = usePolicy(companyId)
  const { map } = useAgentMap(companyId)
  const [period, setPeriod] = useState<FinancePeriod>('month')
  // null = follow today (the anchor moves when the company-local day changes).
  const [picked, setPicked] = useState<string | null>(null)
  const anchor = picked ?? today
  const range = periodRange(period, anchor)
  const summary = useFinanceSummary(companyId, range.from, range.to)

  if (isNotDeployed(summary.error)) {
    return (
      <PanelFrame title="Finance" subtitle="Spend by provenance: verified charges are never mixed with estimates">
        <div className="space-y-4">
          <Card>
            <CardBody>
              <NotAvailable
                compact
                milestone="M6"
                description="Budget utilization with alert thresholds, the trend chart, breakdown by provider and runtime, wasted cost and external expenses. Showing the usage ledger meanwhile."
              />
            </CardBody>
          </Card>
          <BasicFinance />
        </div>
      </PanelFrame>
    )
  }

  const shown = summary.data?.from && summary.data.to ? { from: summary.data.from, to: summary.data.to } : range
  const thresholds = normalizeThresholds(policy.data?.budget_alert_thresholds) ?? DEFAULT_THRESHOLDS

  return (
    <PanelFrame title="Finance" subtitle="Spend by provenance: verified charges are never mixed with estimates">
      <div className="space-y-4">
        <PeriodBar period={period} anchor={anchor} today={today} timezone={summary.data?.timezone || tz} range={shown} onPeriod={(p) => setPeriod(p)} onAnchor={(iso) => setPicked(iso === today ? null : iso)} />
        {summary.isPending ? (
          <Card>
            <LoadingState rows={4} label="Loading finance summary" />
          </Card>
        ) : (
          <>
            {summary.isError ? (
              <Card>
                <ErrorState error={summary.error} onRetry={() => void summary.refetch()} retrying={summary.isFetching} />
              </Card>
            ) : !summary.data ? (
              <Card>
                <ErrorState title="Unexpected finance response" error={new Error('The server answered with something that is not a finance summary.')} onRetry={() => void summary.refetch()} />
              </Card>
            ) : (
              <>
                <TotalsCards summary={summary.data} />
                <BudgetCard budgets={summary.data.budgets} thresholds={thresholds} agents={map} />
              </>
            )}
            {/* The other reports load after the summary, so a backend without M6 shows the fallback and not a flash of "not built yet" cards. */}
            <TrendCard companyId={companyId} from={range.from} to={range.to} />
            <BreakdownCard companyId={companyId} from={range.from} to={range.to} />
            <ExpensesCard companyId={companyId} from={range.from} to={range.to} today={today} />
            <RunCostsCard companyId={companyId} from={range.from} to={range.to} />
          </>
        )}
      </div>
    </PanelFrame>
  )
}

