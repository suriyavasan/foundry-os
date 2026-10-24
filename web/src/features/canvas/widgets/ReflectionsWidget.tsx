import { BookMarked } from 'lucide-react'
import { isNotDeployed, useReflections, useStandards } from '@/api'
import { Button, EmptyState, ErrorState, LoadingState, NotAvailable, Widget } from '@/components/ui'
import { ReflectionCard } from '@/features/reflections/ReflectionCard'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { useCanvas } from '../CanvasContext'
import { useCompanyRole, useTick } from '../hooks'

/** How many reflections the widget lists; the rest is one click away in the Reflections panel. */
const WIDGET_REFLECTIONS = 8

/**
 * Reflections: what the AI Co-founder noticed about the company's work, for the Founder to react to. Endorsing, challenging
 * or refining here is how the company's standards get set. Next to the organization chart on purpose.
 */
export function ReflectionsWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const reflections = useReflections(companyId)
  const standards = useStandards(companyId)
  const role = useCompanyRole(companyId)
  const now = useTick(30_000)
  const unavailable = isNotDeployed(reflections.error)
  const list = reflections.data ?? []
  const open = list.filter((r) => r.status === 'open' || r.status === 'revised').length
  const activeStandards = standards.data?.filter((s) => s.status === 'active').length

  return (
    <Widget
      title="Reflections"
      subtitle={reflections.data ? (open > 0 ? `${open} waiting for you` : `${list.length} so far`) : undefined}
      tall
      onExpand={() => openPanel({ kind: 'reflections' })}
      expandLabel="Open all reflections"
      className={className}
      bodyClassName="flex flex-col overflow-hidden"
      actions={
        !unavailable ? (
          <Button size="sm" variant="ghost" icon={<BookMarked className="size-3.5" />} onClick={() => openPanel({ kind: 'standards' })}>
            Standards{activeStandards !== undefined ? ` (${activeStandards})` : ''}
          </Button>
        ) : undefined
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {reflections.isPending ? (
          <LoadingState rows={5} label="Loading reflections" />
        ) : unavailable ? (
          <NotAvailable compact className="p-4" milestone="M10" description={`The ${AI_COFOUNDER_LABEL} shares reflections here once this backend has the reflections feature.`} />
        ) : reflections.isError ? (
          <ErrorState compact error={reflections.error} onRetry={() => void reflections.refetch()} retrying={reflections.isFetching} />
        ) : list.length === 0 ? (
          <EmptyState compact title="No reflections yet" description={`The ${AI_COFOUNDER_LABEL} reflects on the company's work every day and after each project. What it learns will show up here, and your reactions set the company's standards.`} />
        ) : (
          <ul className="divide-y divide-line">
            {list.slice(0, WIDGET_REFLECTIONS).map((r) => (
              <ReflectionCard key={r.id} reflection={r} companyId={companyId} canRespond={role !== 'viewer'} onOpenPanel={openPanel} now={now} />
            ))}
            {list.length > WIDGET_REFLECTIONS && (
              <li className="px-3.5 py-2 text-[12px] text-fg-3">
                {list.length - WIDGET_REFLECTIONS} older reflections are in the{' '}
                <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'reflections' })}>
                  Reflections panel
                </button>
                .
              </li>
            )}
          </ul>
        )}
      </div>
    </Widget>
  )
}
