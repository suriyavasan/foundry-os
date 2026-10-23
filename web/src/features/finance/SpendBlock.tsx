import clsx from 'clsx'
import type { UsageRow } from '@/api/types'
import { SectionLabel } from '@/components/ui'
import { budgetFraction } from '@/lib/usage'
import { COST_KIND_LABEL, formatCost, formatUsd } from '@/lib/money'

/**
 * One period of spend against its limit. Verified spend is the solid segment and the headline number
 * ("charged"); estimated spend is a separate hatched segment with its own label and is never added to
 * the headline. Unknown-cost usage is reported as such, without a dollar figure.
 */
export function SpendBlock({ title, row, limitMicros }: { title: string; row: UsageRow; limitMicros: number | null }) {
  const { verified, estimated, unknown } = row.by_kind
  const limit = limitMicros && limitMicros > 0 ? limitMicros : null
  const vFrac = budgetFraction(verified, limit)
  const eFrac = budgetFraction(estimated, limit)
  const over = (vFrac ?? 0) + (eFrac ?? 0) > 1
  const vPct = Math.min(100, (vFrac ?? 0) * 100)
  const ePct = Math.max(0, Math.min(100 - vPct, (eFrac ?? 0) * 100))

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <SectionLabel>{title}</SectionLabel>
        <span className="text-[11.5px] text-fg-3">{limit ? `Limit ${formatUsd(limit)}` : 'No limit set'}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-[22px] leading-7 font-semibold tracking-tight text-fg tabular-nums">{formatCost(verified, 'verified')}</span>
        <span className="text-[12px] text-fg-3">charged · {COST_KIND_LABEL.verified.toLowerCase()}</span>
      </div>
      {limit && (
        <div
          role="progressbar"
          aria-label={`${title} spend against limit`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(vPct)}
          aria-valuetext={`${formatCost(verified, 'verified')} charged${estimated ? `, ${formatCost(estimated, 'estimated')}` : ''} of ${formatUsd(limit)}`}
          className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-active"
        >
          <div className={clsx('h-full', over ? 'bg-danger' : 'bg-success')} style={{ width: `${vPct}%` }} />
          {ePct > 0 && <div className={clsx('cos-hatch h-full', over ? 'text-danger' : 'text-warning')} style={{ width: `${ePct}%` }} />}
        </div>
      )}
      <dl className="mt-2 space-y-0.5 text-[12px]">
        <div className="flex items-center justify-between gap-2">
          <dt className="flex items-center gap-1.5 text-fg-3">
            <span aria-hidden className="inline-block size-2 rounded-sm bg-success" />
            Verified
          </dt>
          <dd className="text-fg-2 tabular-nums">{formatCost(verified, 'verified')}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="flex items-center gap-1.5 text-fg-3">
            <span aria-hidden className="cos-hatch inline-block size-2 rounded-sm text-warning" />
            Estimated
          </dt>
          <dd className="text-fg-2 tabular-nums">{formatCost(estimated, 'estimated')}</dd>
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
