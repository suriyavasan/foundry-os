import { Table2, BarChart3 } from 'lucide-react'
import { useState } from 'react'
import { useFinanceTrend } from '@/api'
import { StackedBars, type BarSeries } from '@/components/charts/StackedBars'
import { Boundary, Button, Card, CardHeader, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { formatDay, niceMax, trendDayTotal, type TrendDay } from '@/lib/finance'
import { formatCost, formatUsd } from '@/lib/money'

const SERIES: BarSeries[] = [
  { id: 'verified', label: 'Charged (verified)', className: 'fill-success' },
  { id: 'estimated', label: 'Estimated (not a charge)', className: 'text-warning', hatch: true },
  { id: 'unknown', label: 'Unpriced', className: 'fill-neutral' },
]

/** One day as text: estimates are labelled "est." and unpriced usage never gets a dollar figure. */
export function trendDayText(d: TrendDay): string {
  const parts = [`charged ${formatCost(d.verified, 'verified')}`]
  if (d.estimated > 0) parts.push(formatCost(d.estimated, 'estimated'))
  if (d.unknown > 0) parts.push('unpriced usage')
  return `${formatDay(d.date)}: ${parts.join('; ')}`
}

/** Spend per day by cost kind, as a chart with the same numbers available as a table. */
export function TrendCard({ companyId, from, to }: { companyId: string; from: string; to: string }) {
  const q = useFinanceTrend(companyId, from, to)
  const [asTable, setAsTable] = useState(false)
  return (
    <Card>
      <CardHeader
        title="Trend"
        description="Spend per day by cost kind. Days with no spend are shown as gaps."
        actions={
          <Button size="sm" aria-pressed={asTable} icon={asTable ? <BarChart3 className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />} onClick={() => setAsTable((v) => !v)}>
            {asTable ? 'Show chart' : 'Show data table'}
          </Button>
        }
      />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        isEmpty={(d) => d.length === 0 || d.every((x) => trendDayTotal(x) === 0)}
        empty={{ title: 'No spend in this period', description: 'The chart appears once there is something to plot.' }}
      >
        {(days) => (
          <div className="p-4">
            {asTable ? (
              <Table>
                <THead>
                  <TR>
                    <TH>Day</TH>
                    <TH className="text-right">Charged (verified)</TH>
                    <TH className="text-right">Estimated</TH>
                    <TH className="text-right">Unpriced usage</TH>
                  </TR>
                </THead>
                <TBody>
                  {days.map((d) => (
                    <TR key={d.date}>
                      <TD>{formatDay(d.date)}</TD>
                      <TD className="text-right tabular-nums">{formatCost(d.verified, 'verified')}</TD>
                      <TD className="text-right tabular-nums text-fg-2">{d.estimated > 0 ? formatCost(d.estimated, 'estimated') : '—'}</TD>
                      <TD className="text-right text-fg-2">{d.unknown > 0 ? formatCost(d.unknown, 'unknown') : '—'}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : (
              <>
                <StackedBars
                  ariaLabel={`Spend per day from ${formatDay(days[0]!.date)} to ${formatDay(days[days.length - 1]!.date)}. Charged ${formatUsd(days.reduce((s, d) => s + d.verified, 0))}; estimated ${formatCost(days.reduce((s, d) => s + d.estimated, 0), 'estimated')}. A data table is available.`}
                  series={SERIES}
                  bars={days.map((d) => ({ key: d.date, label: formatDay(d.date).replace(/^\w+ /, ''), values: [d.verified, d.estimated, d.unknown], title: trendDayText(d) }))}
                  max={niceMax(Math.max(...days.map(trendDayTotal)))}
                  formatTick={(v) => formatUsd(Math.round(v))}
                />
                <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-fg-2" aria-label="Legend">
                  <li className="flex items-center gap-1.5">
                    <span aria-hidden className="inline-block size-2.5 rounded-sm bg-success" /> Charged (verified)
                  </li>
                  <li className="flex items-center gap-1.5">
                    <span aria-hidden className="cos-hatch inline-block size-2.5 rounded-sm text-warning" /> Estimated, not a charge (est.)
                  </li>
                  <li className="flex items-center gap-1.5">
                    <span aria-hidden className="inline-block size-2.5 rounded-sm bg-neutral" /> Unpriced usage
                  </li>
                </ul>
              </>
            )}
          </div>
        )}
      </Boundary>
    </Card>
  )
}
