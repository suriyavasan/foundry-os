import { useMemo } from 'react'
import { usePolicy, useUsage } from '@/api'
import { Boundary, Button, Widget } from '@/components/ui'
import { SpendBlock } from '@/features/finance/SpendBlock'
import { isSameMonth } from '@/lib/dates'
import { normalizeThresholds } from '@/lib/finance'
import { emptyRow, sumRows } from '@/lib/usage'
import { useCanvas } from '../CanvasContext'
import { useCompanyTimezone } from '../hooks'
import { useCompanyDates } from '../spend'

export function SpendWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const tz = useCompanyTimezone(companyId)
  const { today, monthStart } = useCompanyDates(tz)
  const usage = useUsage(companyId, 'day', monthStart, today)
  const policy = usePolicy(companyId)
  // Alert thresholds are only marked when the policy reports them (M6); otherwise the bar is just spend against the limit.
  const thresholds = normalizeThresholds(policy.data?.budget_alert_thresholds)

  const periods = useMemo(() => {
    if (!usage.data) return null
    const days = usage.data.rows.filter((r) => isSameMonth(r.key, today))
    return { today: usage.data.rows.find((r) => r.key === today) ?? emptyRow(today), month: sumRows(days) }
  }, [usage.data, today])

  return (
    <Widget title="Spend" subtitle="Company-wide" onExpand={() => openPanel({ kind: 'finance' })} expandLabel="Open finance" className={className}>
      <Boundary query={usage} unavailableText="The usage ledger and budgets come with the work engine.">
        {() =>
          periods && (
            <div className="space-y-4 p-3.5">
              <SpendBlock title="Today" row={periods.today} limitMicros={policy.data?.daily_budget_micros ?? null} thresholds={thresholds} />
              <div className="border-t border-line pt-3.5">
                <SpendBlock title="This month" row={periods.month} limitMicros={policy.data?.monthly_budget_micros ?? null} thresholds={thresholds} />
              </div>
              {policy.isError && <p className="text-[11.5px] text-fg-3">Budget limits could not be loaded, so no limit is shown.</p>}
              <Button size="sm" className="w-full" onClick={() => openPanel({ kind: 'finance' })}>
                Open finance: budgets, trend, expenses
              </Button>
            </div>
          )
        }
      </Boundary>
    </Widget>
  )
}
