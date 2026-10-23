import * as RadixDialog from '@radix-ui/react-dialog'
import clsx from 'clsx'
import { ArrowLeft, Maximize2, Minimize2, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { IconButton } from '@/components/ui'
import { useCanvas } from './CanvasContext'

/**
 * Right-side drawer that holds every detail view. Its open state is the URL (`?panel=`), so Esc / the
 * close button / the browser back button all just change the URL. "Expand" turns it into a
 * full-screen overlay.
 */
export function PanelFrame({
  title,
  subtitle,
  leading,
  actions,
  onBack,
  children,
}: {
  title: ReactNode
  subtitle?: ReactNode
  leading?: ReactNode
  actions?: ReactNode
  /** Shows a back arrow (e.g. run detail -> task). */
  onBack?: () => void
  children: ReactNode
}) {
  const { closePanel } = useCanvas()
  const [full, setFull] = useState(false)
  return (
    // Non-modal on purpose: the top bar (kill switch, command palette) and the org chart behind the
    // drawer stay usable. Esc and the close button dismiss it; clicking elsewhere does not.
    <RadixDialog.Root modal={false} open onOpenChange={(o) => !o && closePanel()}>
      <RadixDialog.Portal>
        <RadixDialog.Content
          aria-describedby={undefined}
          onInteractOutside={(e) => e.preventDefault()}
          className={clsx(
            'fixed inset-y-0 right-0 z-40 flex flex-col border-l border-line bg-canvas shadow-pop focus:outline-none',
            'animate-[cos-slide-in-right_160ms_ease-out]',
            full ? 'w-screen' : 'w-[min(100vw,920px)]',
          )}
        >
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
            {onBack && (
              <IconButton label="Back" className="size-8" onClick={onBack}>
                <ArrowLeft className="size-4" aria-hidden />
              </IconButton>
            )}
            {leading}
            <div className="flex min-w-0 flex-1 items-baseline gap-2">
              <RadixDialog.Title className="truncate text-[14px] font-semibold text-fg">{title}</RadixDialog.Title>
              {subtitle && <span className="hidden truncate text-[12px] text-fg-3 sm:block">{subtitle}</span>}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">{actions}</div>
            <IconButton label={full ? 'Exit full screen' : 'Full screen'} className="hidden sm:inline-flex" onClick={() => setFull((f) => !f)}>
              {full ? <Minimize2 className="size-4" aria-hidden /> : <Maximize2 className="size-4" aria-hidden />}
            </IconButton>
            <RadixDialog.Close asChild>
              <IconButton label="Close panel">
                <X className="size-4" aria-hidden />
              </IconButton>
            </RadixDialog.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1100px] p-4">{children}</div>
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}
