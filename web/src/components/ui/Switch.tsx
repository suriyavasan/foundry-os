import clsx from 'clsx'
import { Loader2 } from 'lucide-react'

/**
 * An on/off setting that takes effect immediately. `label` is the accessible name; `describedBy` should point at
 * the text that explains the setting (or why it is disabled).
 */
export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled,
  loading,
  describedBy,
  id,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  label: string
  disabled?: boolean
  loading?: boolean
  describedBy?: string
  id?: string
}) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      onClick={() => onCheckedChange(!checked)}
      className={clsx(
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'border-transparent bg-accent' : 'border-line-strong bg-active',
      )}
    >
      <span aria-hidden className={clsx('inline-flex size-3.5 items-center justify-center rounded-full bg-fg-on-accent shadow-sm transition-transform', checked ? 'translate-x-[18px]' : 'translate-x-0.5')}>
        {loading && <Loader2 className="cos-spin size-2.5 text-accent" />}
      </span>
    </button>
  )
}
