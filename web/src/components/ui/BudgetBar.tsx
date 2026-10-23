import clsx from 'clsx'
import { COST_KIND_LABEL, formatCost, formatUsd } from '@/lib/money'
import type { Utilization } from '@/lib/finance'

/**
 * A budget as a bar: verified spend is the solid segment, estimates are a separate hatched segment, and each alert
 * threshold is a tick on the track (reached ones are drawn heavier). Nothing is drawn without a limit.
 * Thresholds are only marked when given; with none, the bar is just spend against the limit.
 */
export function BudgetBar({ util, thresholds = [], label, className }: { util: Utilization; thresholds?: number[]; label: string; className?: string }) {
  if (util.limit === null) return null
  const over = util.level === 'exceeded'
  const warn = util.level === 'warning'
  const tone = over ? 'text-danger' : warn ? 'text-warning' : 'text-success'
  return (
    <div className={clsx('relative', className)}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(Math.min(100, util.verifiedBarPct))}
        aria-valuetext={`${formatCost(util.verified, 'verified')} charged${util.estimated ? `, ${formatCost(util.estimated, 'estimated')}` : ''} of ${formatUsd(util.limit)}`}
        className="flex h-2 w-full overflow-hidden rounded-full bg-active"
      >
        <div className={clsx('h-full', over ? 'bg-danger' : warn ? 'bg-warning' : 'bg-success')} style={{ width: `${util.verifiedBarPct}%` }} />
        {util.estimatedBarPct > 0 && <div className={clsx('cos-hatch h-full', tone)} style={{ width: `${util.estimatedBarPct}%` }} />}
      </div>
      {thresholds.map((t) => {
        const reached = util.reached.includes(t)
        return (
          <span
            key={t}
            aria-hidden
            data-threshold={t}
            data-reached={reached || undefined}
            title={`${t}% alert${reached ? ' (reached)' : ''}`}
            className={clsx('absolute top-[-3px] h-[14px] w-px', reached ? 'bg-fg' : 'bg-fg-3')}
            style={{ left: `calc(${Math.min(100, t)}% - 0.5px)` }}
          />
        )
      })}
      {thresholds.length > 0 && (
        <div aria-hidden className="relative mt-1.5 h-3 text-[10.5px] text-fg-3">
          {thresholds.map((t) => (
            <span key={t} className={clsx('absolute tabular-nums', t >= 95 ? '-translate-x-full' : t <= 5 ? '' : '-translate-x-1/2')} style={{ left: `${Math.min(100, t)}%` }}>
              {t}%
            </span>
          ))}
        </div>
      )}
      {thresholds.length > 0 && (
        <span className="sr-only">
          {COST_KIND_LABEL.verified} spend {formatCost(util.verified, 'verified')}
          {util.estimated > 0 ? `, ${COST_KIND_LABEL.estimated.toLowerCase()} ${formatCost(util.estimated, 'estimated')}` : ''}; alert thresholds at {thresholds.join(', ')} percent.
        </span>
      )}
    </div>
  )
}
