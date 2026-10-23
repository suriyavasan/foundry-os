import clsx from 'clsx'
import type { ReactNode } from 'react'
import type { CostKind } from '@/api/types'
import { COST_KIND_HELP, COST_KIND_LABEL } from '@/lib/money'
import { Badge, type Tone } from './Badge'
import { Tooltip } from './Tooltip'

/** Slim progress bar. `value` and `max` are counts; renders nothing misleading for max=0. */
export function ProgressBar({ value, max, tone = 'accent', label, className }: { value: number; max: number; tone?: 'accent' | 'success' | 'warning' | 'danger'; label: string; className?: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0
  const fill = { accent: 'bg-accent', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' }[tone]
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(value, max)}
      className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-active', className)}
    >
      <div className={clsx('h-full rounded-full transition-[width] duration-150', fill)} style={{ width: `${pct}%` }} />
    </div>
  )
}

export function ToneBadge({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return <Badge tone={tone} className={className}>{children}</Badge>
}

/** A cost-kind label with an explanatory tooltip. Verified and estimated look different on purpose. */
export function CostKindBadge({ kind }: { kind: CostKind }) {
  const tone: Tone = kind === 'verified' ? 'success' : kind === 'estimated' ? 'warning' : 'neutral'
  return (
    <Tooltip content={COST_KIND_HELP[kind]}>
      <Badge tone={tone}>{COST_KIND_LABEL[kind]}</Badge>
    </Tooltip>
  )
}

/** Compact key/value line used in hover cards and rows. */
export function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[12px]">
      <span className="shrink-0 text-fg-3">{label}</span>
      <span className="min-w-0 truncate text-right text-fg-2">{children}</span>
    </div>
  )
}

/** Small uppercase section label. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('text-[11px] font-medium tracking-wide text-fg-3 uppercase', className)}>{children}</div>
}
