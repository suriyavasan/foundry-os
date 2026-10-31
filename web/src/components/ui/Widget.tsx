import clsx from 'clsx'
import { Maximize2 } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { IconButton } from './Button'

/**
 * The one frame every canvas widget uses: same padding, header height, border and body scroll rules,
 * so everything on the canvas reads at the same scale.
 */
export function Widget({
  title,
  subtitle,
  actions,
  onExpand,
  expandLabel,
  tall = false,
  chat = false,
  className,
  bodyClassName,
  children,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  /** Opens the detail panel. Omit when the widget has no detail view. */
  onExpand?: () => void
  expandLabel?: string
  tall?: boolean
  /** The first-fold height of the canvas: the Command Center and the organization share it (about 70% of the window, at most 640 px). */
  chat?: boolean
  className?: string
  bodyClassName?: string
  children: ReactNode
}) {
  const titleId = useId()
  return (
    <section
      aria-labelledby={titleId}
      className={clsx(
        'flex min-h-0 min-w-0 flex-col overflow-hidden rounded-md border border-line bg-surface',
        chat ? 'h-[max(440px,min(640px,70vh))]' : tall ? 'h-[460px] md:h-(--widget-h-tall)' : 'h-(--widget-h)',
        className,
      )}
    >
      <header className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-line pr-1.5 pl-3.5">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 id={titleId} className="shrink-0 text-[13px] font-semibold text-fg">
            {title}
          </h2>
          {subtitle && <p className="truncate text-[12px] text-fg-3">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {actions}
          {onExpand && (
            <IconButton label={expandLabel ?? `Expand ${title}`} className="size-7" onClick={onExpand}>
              <Maximize2 className="size-3.5" aria-hidden />
            </IconButton>
          )}
        </div>
      </header>
      <div className={clsx('min-h-0 flex-1 overflow-y-auto', bodyClassName)}>{children}</div>
    </section>
  )
}
