import clsx from 'clsx'
import { ArrowUpRight, ChevronDown, ChevronUp, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, isNotDeployed, useOpenAlerts, useResolveAlert } from '@/api'
import type { Alert, AlertSeverity } from '@/api/types'
import { Badge, Button, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative } from '@/lib/format'
import { SEVERITY_LABEL, SEVERITY_TONE, alertCountText, alertPanel, stripModel } from '@/lib/alerts'
import { refTypeLabel } from '@/lib/refs'

const FRAME: Record<AlertSeverity, string> = {
  critical: 'border-danger/40 bg-danger-subtle',
  warning: 'border-warning/40 bg-warning-subtle',
  info: 'border-info/40 bg-info-subtle',
}
const ICON: Record<AlertSeverity, string> = { critical: 'text-danger', warning: 'text-warning', info: 'text-info' }

/**
 * Open alerts across the top of the canvas. Up to three are listed with their related record and a
 * resolve action; more collapse to one count line the Founder can expand. Renders nothing when there is
 * nothing open (or the alert system is not built yet), and says so plainly if the list cannot be loaded.
 */
export function AlertsStrip() {
  const { companyId, openPanel } = useCanvas()
  const q = useOpenAlerts(companyId)
  const resolve = useResolveAlert(companyId)
  const toast = useToast()
  const [expanded, setExpanded] = useState(false)

  if (q.isPending || isNotDeployed(q.error)) return null
  if (q.isError) {
    return (
      <div role="alert" className="mb-3 flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-[12.5px] text-fg-2">
        <TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden />
        <span className="min-w-0 flex-1">Could not load alerts: {errorMessage(q.error)}</span>
        <Button size="sm" loading={q.isFetching} onClick={() => void q.refetch()}>
          Retry
        </Button>
      </div>
    )
  }

  const model = stripModel(q.data, expanded)
  if (model.mode === 'hidden') return null

  const onResolve = (a: Alert) =>
    resolve.mutate(a.id, { onSuccess: () => toast.success('Alert resolved'), onError: (e) => toast.error(errorMessage(e)) })

  return (
    <section aria-label="Open alerts" className={clsx('mb-3 overflow-hidden rounded-md border', FRAME[model.worst])}>
      {(model.mode === 'collapsed' || model.mode === 'expanded') && (
        <div className="flex items-center gap-2 px-3 py-2">
          <TriangleAlert className={clsx('size-3.5 shrink-0', ICON[model.worst])} aria-hidden />
          <span className="min-w-0 flex-1 text-[13px] font-medium text-fg">{alertCountText(q.data ?? [])}</span>
          <Button size="sm" variant="ghost" aria-expanded={model.mode === 'expanded'} icon={model.mode === 'expanded' ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />} onClick={() => setExpanded((e) => !e)}>
            {model.mode === 'expanded' ? 'Collapse' : 'Show all'}
          </Button>
        </div>
      )}
      {model.items.length > 0 && (
        <ul className={clsx('divide-y divide-line/60', model.mode === 'expanded' && 'max-h-72 overflow-y-auto border-t border-line/60')}>
          {model.items.map((a) => {
            const panel = alertPanel(a)
            return (
              <li key={a.id} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-3 py-2">
                <Badge tone={SEVERITY_TONE[a.severity]}>{SEVERITY_LABEL[a.severity] ?? a.severity}</Badge>
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block truncate text-[13px] font-medium text-fg" title={a.body ?? a.title}>
                    {a.title}
                  </span>
                  {a.body && <span className="block truncate text-[12px] text-fg-2">{a.body}</span>}
                </span>
                <time dateTime={a.created_at} className="shrink-0 text-[11.5px] text-fg-3">
                  {formatRelative(a.created_at)}
                </time>
                {panel && (
                  <Button size="sm" variant="ghost" icon={<ArrowUpRight className="size-3.5" />} onClick={() => openPanel(panel)} aria-label={`Open related ${a.related_type ? refTypeLabel(a.related_type).toLowerCase() : 'record'}: ${a.title}`}>
                    {a.related_type ? refTypeLabel(a.related_type) : 'Open'}
                  </Button>
                )}
                <Button size="sm" loading={resolve.isPending && resolve.variables === a.id} onClick={() => onResolve(a)} aria-label={`Resolve alert: ${a.title}`}>
                  Resolve
                </Button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
