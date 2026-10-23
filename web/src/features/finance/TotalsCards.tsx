import type { FinanceSummary } from '@/api/types'
import { Card, CardBody } from '@/components/ui'
import { COST_KIND_HELP, COST_KIND_LABEL, formatCost, formatUsd } from '@/lib/money'

function Tile({ label, swatch, value, hint, valueClass }: { label: string; swatch?: React.ReactNode; value: React.ReactNode; hint?: React.ReactNode; valueClass?: string }) {
  return (
    <Card>
      <CardBody className="space-y-1">
        <div className="flex items-center gap-1.5 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">
          {swatch}
          {label}
        </div>
        <div className={`text-[20px] leading-7 font-semibold tracking-tight tabular-nums ${valueClass ?? 'text-fg'}`}>{value}</div>
        {hint && <div className="text-[12px] text-fg-3">{hint}</div>}
      </CardBody>
    </Card>
  )
}

/**
 * Totals split by provenance. Only the verified figure is called "charged". An estimate carries "≈" and "est." and its
 * own hatched swatch; unpriced usage never shows a dollar amount.
 */
export function TotalsCards({ summary }: { summary: FinanceSummary }) {
  const { totals, wasted } = summary
  const wastedAny = wasted.verified_micros > 0 || wasted.estimated_micros > 0 || wasted.runs > 0
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" role="group" aria-label="Totals for the period">
      <Tile
        label="Charged"
        swatch={<span aria-hidden className="inline-block size-2 rounded-sm bg-success" />}
        value={formatCost(totals.verified_micros, 'verified')}
        hint={COST_KIND_HELP.verified}
      />
      <Tile
        label="Estimated"
        swatch={<span aria-hidden className="cos-hatch inline-block size-2 rounded-sm text-warning" />}
        value={formatCost(totals.estimated_micros, 'estimated')}
        valueClass="text-fg-2"
        hint="Not a charge. Computed from token counts and list prices; the real bill may differ."
      />
      {totals.unknown_micros > 0 ? (
        <Tile label={COST_KIND_LABEL.unknown} swatch={<span aria-hidden className="inline-block size-2 rounded-sm bg-neutral" />} value={formatCost(totals.unknown_micros, 'unknown')} valueClass="text-fg-2" hint={COST_KIND_HELP.unknown} />
      ) : (
        <Tile label="Runs and tokens" value={`${totals.runs.toLocaleString()} runs`} hint={`${totals.input_tokens.toLocaleString()} in / ${totals.output_tokens.toLocaleString()} out`} />
      )}
      <Tile
        label="External expenses"
        value={formatUsd(summary.external_expenses_micros)}
        hint="Recorded by a human. Separate from model usage."
      />
      <div className="sm:col-span-2 xl:col-span-4">
        <Card>
          <CardBody className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
            <div className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Wasted on failed, cancelled and timed-out runs</div>
            {wastedAny ? (
              <>
                <div className="text-[15px] font-semibold tabular-nums text-fg">{formatCost(wasted.verified_micros, 'verified')} charged</div>
                {wasted.estimated_micros > 0 && <div className="text-[13px] tabular-nums text-fg-2">{formatCost(wasted.estimated_micros, 'estimated')}</div>}
                <div className="text-[12.5px] text-fg-3">
                  {wasted.runs} run{wasted.runs === 1 ? '' : 's'}
                  {totals.verified_micros > 0 ? ` · ${Math.round((wasted.verified_micros / totals.verified_micros) * 100)}% of charged` : ''}
                </div>
              </>
            ) : (
              <div className="text-[13px] text-fg-2">None in this period.</div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
