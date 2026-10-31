import clsx from 'clsx'
import type { ReactNode } from 'react'

export interface SegmentOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
}

/** A small segmented control (two or three choices, exactly one pressed). Each segment is a real button with `aria-pressed`. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string
  value: T
  options: ReadonlyArray<SegmentOption<T>>
  onChange: (v: T) => void
  className?: string
}) {
  return (
    <div role="group" aria-label={label} className={clsx('inline-flex rounded-md border border-line-strong p-0.5', className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            'inline-flex h-6 items-center gap-1.5 rounded-sm px-2 text-[12.5px] font-medium transition-colors',
            value === o.value ? 'bg-active text-fg' : 'text-fg-3 hover:text-fg',
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  )
}
