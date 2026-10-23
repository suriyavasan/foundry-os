import clsx from 'clsx'
import type { ComponentProps, ReactNode } from 'react'

export function Card({ className, ...rest }: ComponentProps<'section'>) {
  return <section className={clsx('rounded-md border border-line bg-surface', className)} {...rest} />
}

export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={clsx('flex items-start justify-between gap-3 border-b border-line px-4 py-3', className)}>
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold text-fg">{title}</h2>
        {description && <p className="mt-0.5 text-[12px] text-fg-3">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function CardBody({ className, ...rest }: ComponentProps<'div'>) {
  return <div className={clsx('p-4', className)} {...rest} />
}

/** Label/value stat used in dense summaries. */
export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">{label}</div>
      <div className="mt-0.5 text-[18px] leading-6 font-semibold text-fg tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-[12px] text-fg-3">{hint}</div>}
    </div>
  )
}

/** Key/value rows (definition list). */
export function DefinitionList({ items, className }: { items: Array<{ label: string; value: ReactNode }>; className?: string }) {
  return (
    <dl className={clsx('grid grid-cols-[minmax(7rem,10rem)_1fr] gap-x-4 gap-y-2.5 text-[13px]', className)}>
      {items.map((it) => (
        <div key={it.label} className="contents">
          <dt className="text-fg-3">{it.label}</dt>
          <dd className="min-w-0 break-words text-fg">{it.value}</dd>
        </div>
      ))}
    </dl>
  )
}
