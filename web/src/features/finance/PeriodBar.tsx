import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { FinancePeriod } from '@/api/types'
import { Button, IconButton, Input } from '@/components/ui'
import { formatRangeLabel, isFuturePeriod, isIsoDate, periodRange, shiftAnchor, type DateRange } from '@/lib/finance'

const PERIODS: Array<{ value: FinancePeriod; label: string }> = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
]

/**
 * Period switcher for the Finance panel: day / week / month, previous / next, a date picker, and the exact range echoed
 * back in company time so nobody has to guess which days a figure covers.
 */
export function PeriodBar({
  period,
  anchor,
  today,
  timezone,
  range,
  onPeriod,
  onAnchor,
}: {
  period: FinancePeriod
  anchor: string
  today: string
  timezone: string
  /** The range the figures cover (the server's echo when it sent one, else the requested one). */
  range: DateRange
  onPeriod: (p: FinancePeriod) => void
  onAnchor: (iso: string) => void
}) {
  const nextAnchor = shiftAnchor(period, anchor, 1)
  const nextDisabled = isFuturePeriod(periodRange(period, nextAnchor), today)
  const current = anchor === today
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Reporting period">
      <div className="inline-flex rounded-md border border-line-strong bg-raised p-0.5" role="group" aria-label="Period length">
        {PERIODS.map((p) => (
          <button
            key={p.value}
            type="button"
            aria-pressed={period === p.value}
            onClick={() => onPeriod(p.value)}
            className={`h-6 rounded-sm px-2.5 text-[12.5px] font-medium transition-colors ${period === p.value ? 'bg-accent text-fg-on-accent' : 'text-fg-2 hover:bg-hover hover:text-fg'}`}
          >
            {p.label}
          </button>
        ))}
      </div>
      <IconButton label={`Previous ${period}`} className="size-7" onClick={() => onAnchor(shiftAnchor(period, anchor, -1))}>
        <ChevronLeft className="size-4" aria-hidden />
      </IconButton>
      <IconButton label={`Next ${period}`} className="size-7" disabled={nextDisabled} onClick={() => onAnchor(nextAnchor)}>
        <ChevronRight className="size-4" aria-hidden />
      </IconButton>
      <Input
        type="date"
        aria-label="Pick a date in the period"
        className="h-7 w-auto text-[12.5px]"
        value={anchor}
        max={today}
        onChange={(e) => {
          const v = e.target.value
          if (isIsoDate(v) && v <= today) onAnchor(v)
        }}
      />
      <Button size="sm" disabled={current} onClick={() => onAnchor(today)}>
        Today
      </Button>
      <p className="basis-full text-[12.5px] text-fg-2 sm:ml-1 sm:basis-auto" aria-live="polite">
        <span className="font-medium text-fg">{formatRangeLabel(range)}</span> <span className="text-fg-3">· {timezone} (company time)</span>
      </p>
    </div>
  )
}
