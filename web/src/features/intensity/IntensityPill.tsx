import * as Popover from '@radix-ui/react-popover'
import { Gauge } from 'lucide-react'
import { isNotDeployed, useIntensity } from '@/api'
import { Skeleton } from '@/components/ui'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import type { Intensity } from '@/api/types'
import { IntensityDetails, IntensitySteps, useIntensityChooser } from './IntensityControl'

/** Top-bar intensity control: the level name opens a popover with the steps and what the level means. */
export function IntensityPill({ companyId }: { companyId: string }) {
  const q = useIntensity(companyId)
  const role = useCompanyRole(companyId)
  if (q.isPending) return <Skeleton className="hidden h-8 w-24 sm:block" />
  if (q.error && isNotDeployed(q.error)) return null // a backend without M23: nothing to control
  if (q.error || !q.data) {
    return (
      <button
        type="button"
        disabled
        title="The company intensity could not be loaded"
        className="hidden h-8 items-center gap-1.5 rounded-md border border-line-strong px-2 text-[12.5px] text-fg-3 sm:inline-flex"
      >
        <Gauge className="size-3.5" aria-hidden />
        Intensity unavailable
      </button>
    )
  }
  return <PillBody companyId={companyId} intensity={q.data} canEdit={canEditPolicy(role)} />
}

function PillBody({ companyId, intensity, canEdit }: { companyId: string; intensity: Intensity; canEdit: boolean }) {
  const chooser = useIntensityChooser(companyId, intensity, canEdit)
  return (
    <>
      <Popover.Root>
        <Popover.Trigger asChild>
          <button
            type="button"
            aria-label={`Company intensity: ${intensity.name}${intensity.custom ? ' (custom)' : ''}`}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-raised px-2 text-[12.5px] text-fg-2 transition-colors hover:bg-hover hover:text-fg"
          >
            <Gauge className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">{intensity.name}</span>
            <span className="sr-only sm:hidden">{intensity.name}</span>
            <span aria-hidden className="inline-flex items-end gap-px">
              {[1, 2, 3, 4, 5].map((n) => (
                <span key={n} className={n <= intensity.level ? 'w-0.5 rounded-full bg-accent' : 'w-0.5 rounded-full bg-line-strong'} style={{ height: 4 + n * 2 }} />
              ))}
            </span>
            {intensity.custom && <span className="text-[11px] text-warning">Custom</span>}
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            align="end"
            sideOffset={6}
            aria-label="Company intensity"
            onInteractOutside={(e) => chooser.pending !== null && e.preventDefault()}
            className="z-[60] w-80 max-w-[calc(100vw-1.5rem)] space-y-3 rounded-md bg-surface p-3 shadow-pop"
          >
            <div>
              <h2 className="text-[13px] font-semibold text-fg">How hard should the company work?</h2>
              <p className="text-[12px] text-fg-3">One dial for pace. It never changes budgets.</p>
            </div>
            <IntensitySteps intensity={intensity} canEdit={canEdit} chooser={chooser} />
            <IntensityDetails intensity={intensity} />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {chooser.dialog}
    </>
  )
}
