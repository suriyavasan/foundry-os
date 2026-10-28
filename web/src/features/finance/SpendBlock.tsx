import type { UsageRow } from '@/api/types'
import { BudgetBar, SectionLabel } from '@/components/ui'
import { utilization } from '@/lib/finance'
import { COST_KIND_LABEL, formatCost, formatUsd } from '@/lib/money'

/**
 * One period of spend against its limit. The estimate is the headline, labelled "est." and "not a charge", because
 * most providers never report a charge; verified spend ("charged") is listed separately and the two are never
 * summed into one figure. On the bar, verified is the solid segment and the estimate the hatched one. Unknown-cost
 * usage is reported as such, without a dollar figure. `thresholds` (percent) are drawn as ticks only when the
 * policy supplies them.
 */
export function SpendBlock({ title, row, limitMicros, thresholds }: { title: string; row: UsageRow; limitMicros: number | null; thresholds?: number[] | null }) {
  const { verified, estimated, unknown } = row.by_kind
  const limit = limitMicros && limitMicros > 0 ? limitMicros : null
  const util = utilization(verified, estimated, limit, thresholds ?? [])

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <SectionLabel>{title}</SectionLabel>
        <span className="text-[11.5px] text-fg-3">{limit ? `Limit ${formatUsd(limit)}` : 'No limit set'}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-[22px] leading-7 font-semibold tracking-tight text-fg tabular-nums">{formatCost(estimated, 'estimated')}</span>
        <span className="text-[12px] text-fg-3">{COST_KIND_LABEL.estimated.toLowerCase()} · not a charge</span>
      </div>
      {limit && <BudgetBar className="mt-2" util={util} thresholds={thresholds ?? []} label={`${title} spend against limit`} />}
      <dl className="mt-2 space-y-0.5 text-[12px]">
        <div className="flex items-center justify-between gap-2">
          <dt className="flex items-center gap-1.5 text-fg-3">
            <span aria-hidden className="cos-hatch inline-block size-2 rounded-sm text-warning" />
            Estimated
          </dt>
          <dd className="text-fg-2 tabular-nums">{formatCost(estimated, 'estimated')}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="flex items-center gap-1.5 text-fg-3">
            <span aria-hidden className="inline-block size-2 rounded-sm bg-success" />
            Verified
          </dt>
          <dd className="text-fg-2 tabular-nums">{formatCost(verified, 'verified')}</dd>
        </div>
        {unknown > 0 && (
          <div className="flex items-center justify-between gap-2">
            <dt className="text-fg-3">Unpriced usage</dt>
            <dd className="text-fg-2">{formatCost(unknown, 'unknown')}</dd>
          </div>
        )}
      </dl>
      {estimated > 0 && <p className="mt-1 text-[11.5px] text-fg-3">Estimates are not charges; the real bill may differ.</p>}
    </div>
  )
}
