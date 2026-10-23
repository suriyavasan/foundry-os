import clsx from 'clsx'
import { useId } from 'react'

export interface BarSeries {
  id: string
  label: string
  /** Solid fill class (e.g. `fill-success`) or, with `hatch`, the colour class of the stripes (e.g. `text-warning`). */
  className: string
  hatch?: boolean
}
export interface Bar {
  key: string
  /** Short x-axis label. */
  label: string
  /** One value per series, in series order. */
  values: number[]
  /** Hover text for the whole bar. */
  title: string
}

const W = 720
const H = 200
const PAD = { left: 52, right: 8, top: 8, bottom: 22 }

/**
 * A small stacked-bar chart in plain SVG. It draws what it is given: bars are scaled to `max` (a "nice" number the caller
 * computed), so zero-spend days are visible gaps. The picture is decorative for assistive tech (`role="img"` with a
 * summary); the caller offers the same numbers as a table. No animation, so reduced motion needs no special case.
 */
export function StackedBars({
  bars,
  series,
  max,
  formatTick,
  ariaLabel,
  className,
}: {
  bars: Bar[]
  series: BarSeries[]
  max: number
  formatTick: (value: number) => string
  ariaLabel: string
  className?: string
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const slot = bars.length > 0 ? plotW / bars.length : plotW
  const barW = Math.max(2, Math.min(28, slot * 0.7))
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  const labelEvery = Math.max(1, Math.ceil(bars.length / 10))
  const y = (v: number) => PAD.top + plotH - (max > 0 ? Math.min(1, v / max) : 0) * plotH

  return (
    <svg role="img" aria-label={ariaLabel} viewBox={`0 0 ${W} ${H}`} className={clsx('h-auto w-full', className)}>
      <defs>
        {series
          .filter((s) => s.hatch)
          .map((s) => (
            <pattern key={s.id} id={`${uid}-${s.id}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)" className={s.className}>
              <rect width="5" height="5" fill="currentColor" fillOpacity="0.12" />
              <line x1="0" y1="0" x2="0" y2="5" stroke="currentColor" strokeWidth="2" />
            </pattern>
          ))}
      </defs>
      {ticks.map((t) => {
        const v = max * t
        return (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="stroke-line" strokeWidth="1" />
            <text x={PAD.left - 6} y={y(v) + 3.5} textAnchor="end" className="fill-fg-3" fontSize="10">
              {formatTick(v)}
            </text>
          </g>
        )
      })}
      {bars.map((b, i) => {
        const x = PAD.left + slot * i + (slot - barW) / 2
        let acc = 0
        return (
          <g key={b.key}>
            <title>{b.title}</title>
            {/* wider invisible target so a hover over a zero bar still shows its title */}
            <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={plotH} fill="transparent" />
            {series.map((s, si) => {
              const v = b.values[si] ?? 0
              if (v <= 0) return null
              const y1 = y(acc + v)
              const h = y(acc) - y1
              acc += v
              return s.hatch ? (
                <rect key={s.id} x={x} y={y1} width={barW} height={Math.max(h, 1)} fill={`url(#${uid}-${s.id})`} className={clsx(s.className, 'stroke-current')} strokeWidth="1" />
              ) : (
                <rect key={s.id} x={x} y={y1} width={barW} height={Math.max(h, 1)} className={s.className} />
              )
            })}
            {i % labelEvery === 0 && (
              <text x={x + barW / 2} y={H - 6} textAnchor="middle" className="fill-fg-3" fontSize="10">
                {b.label}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
