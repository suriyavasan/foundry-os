import clsx from 'clsx'
import { AlertTriangle, Clock, Inbox, RefreshCw, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { errorMessage } from '@/api/client'
import { Button } from './Button'

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx('cos-skeleton h-4 w-full', className)} />
}

/** Generic loading placeholder: a few skeleton rows. */
export function LoadingState({ rows = 4, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" aria-label={label} className="space-y-2.5 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={i % 3 === 2 ? 'w-2/3' : i % 3 === 1 ? 'w-5/6' : 'w-full'} />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  )
}

interface StateShellProps {
  icon: LucideIcon
  title: string
  description?: ReactNode
  action?: ReactNode
  tone?: 'neutral' | 'danger' | 'info'
  className?: string
  role?: 'alert' | 'status'
  /** Tighter padding for use inside a widget. */
  compact?: boolean
}

function StateShell({ icon: Icon, title, description, action, tone = 'neutral', className, role, compact }: StateShellProps) {
  return (
    <div
      role={role}
      className={clsx(
        'flex flex-col items-center justify-center gap-1 text-center',
        compact ? 'px-4 py-6' : 'px-6 py-10',
        className,
      )}
    >
      <div
        className={clsx(
          'mb-2 flex size-9 items-center justify-center rounded-md',
          tone === 'danger' && 'bg-danger-subtle text-danger',
          tone === 'info' && 'bg-info-subtle text-info',
          tone === 'neutral' && 'bg-neutral-subtle text-fg-2',
        )}
      >
        <Icon className="size-4.5" aria-hidden />
      </div>
      <h3 className="text-[13.5px] font-semibold text-fg">{title}</h3>
      {description && <p className="max-w-md text-[13px] text-fg-3">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function EmptyState(props: Omit<StateShellProps, 'icon' | 'tone'> & { icon?: LucideIcon }) {
  return <StateShell icon={Inbox} {...props} />
}

export function ErrorState({
  error,
  title = 'Could not load this data',
  onRetry,
  retrying,
  className,
  compact,
}: {
  error?: unknown
  title?: string
  onRetry?: () => void
  retrying?: boolean
  className?: string
  compact?: boolean
}) {
  return (
    <StateShell
      role="alert"
      compact={compact}
      icon={AlertTriangle}
      tone="danger"
      title={title}
      className={className}
      description={error ? errorMessage(error) : undefined}
      action={
        onRetry && (
          <Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={retrying} onClick={onRetry}>
            Retry
          </Button>
        )
      }
    />
  )
}

/**
 * A subsystem that the API reports as `{available: false, milestone}`.
 * Always says plainly that it is not built; never shows placeholder numbers.
 */
export function NotAvailable({
  milestone,
  title,
  description,
  compact = false,
  className,
}: {
  milestone: string
  title?: string
  description?: ReactNode
  compact?: boolean
  className?: string
}) {
  if (compact) {
    return (
      <div role="status" className={clsx('flex items-start gap-2.5 text-[13px]', className)}>
        <Clock className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
        <div className="min-w-0">
          <p className="text-fg-2">
            Not built yet · <span className="font-medium text-fg">Arrives in {milestone}</span>
          </p>
          {description && <p className="mt-0.5 text-[12px] text-fg-3">{description}</p>}
        </div>
      </div>
    )
  }
  return (
    <StateShell
      role="status"
      icon={Clock}
      tone="info"
      className={className}
      title={title ?? 'Not built yet'}
      description={
        <>
          {description && <span className="block">{description}</span>}
          <span className="mt-1 block font-medium text-fg-2">Arrives in {milestone}</span>
        </>
      }
    />
  )
}
