import { BookMarked } from 'lucide-react'
import { useMemo, useState } from 'react'
import { isNotDeployed, useReflections } from '@/api'
import type { ReflectionKind, ReflectionStatus } from '@/api/types'
import { Button, Card, EmptyState, ErrorState, LoadingState, NotAvailable, Select } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useCompanyRole, useTick } from '@/features/canvas/hooks'
import { REFLECTION_KINDS, REFLECTION_KIND_LABEL, REFLECTION_STATUSES, REFLECTION_STATUS_LABEL } from '@/lib/reflections'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { ReflectionCard } from './ReflectionCard'

/** `?panel=reflections`: every reflection, filtered by status (on the server) and kind. */
export function ReflectionsPanel() {
  const { companyId, openPanel } = useCanvas()
  const [status, setStatus] = useState<ReflectionStatus | 'all'>('all')
  const [kind, setKind] = useState<ReflectionKind | 'all'>('all')
  const reflections = useReflections(companyId, status === 'all' ? undefined : status)
  const role = useCompanyRole(companyId)
  const now = useTick(30_000)
  const unavailable = isNotDeployed(reflections.error)
  const shown = useMemo(() => (reflections.data ?? []).filter((r) => kind === 'all' || r.kind === kind), [reflections.data, kind])
  const filtered = status !== 'all' || kind !== 'all'

  return (
    <PanelFrame
      title="Reflections"
      subtitle={`What the ${AI_COFOUNDER_LABEL} noticed, and what you decided about it`}
      actions={
        <Button size="sm" icon={<BookMarked className="size-3.5" />} onClick={() => openPanel({ kind: 'standards' })}>
          Standards
        </Button>
      }
    >
      <Card>
        {!unavailable && (
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
            <Select aria-label="Filter by status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value as ReflectionStatus | 'all')}>
              <option value="all">All statuses</option>
              {REFLECTION_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {REFLECTION_STATUS_LABEL[s]}
                </option>
              ))}
            </Select>
            <Select aria-label="Filter by kind" className="w-auto" value={kind} onChange={(e) => setKind(e.target.value as ReflectionKind | 'all')}>
              <option value="all">All kinds</option>
              {REFLECTION_KINDS.map((k) => (
                <option key={k} value={k}>
                  {REFLECTION_KIND_LABEL[k]}
                </option>
              ))}
            </Select>
            <p className="ml-auto text-[12px] text-fg-3">
              {shown.length} reflection{shown.length === 1 ? '' : 's'}
            </p>
          </div>
        )}
        {reflections.isPending ? (
          <LoadingState rows={5} label="Loading reflections" />
        ) : unavailable ? (
          <NotAvailable milestone="M10" title="Reflections are not built yet" description={`The ${AI_COFOUNDER_LABEL} shares reflections once this backend has the reflections feature.`} />
        ) : reflections.isError ? (
          <ErrorState error={reflections.error} onRetry={() => void reflections.refetch()} retrying={reflections.isFetching} />
        ) : shown.length === 0 ? (
          <EmptyState title={filtered ? 'No reflections match' : 'No reflections yet'} description={filtered ? 'Change the filters.' : `The ${AI_COFOUNDER_LABEL} reflects daily and after each project. They will appear here.`} />
        ) : (
          <ul className="divide-y divide-line">
            {shown.map((r) => (
              <ReflectionCard key={r.id} reflection={r} companyId={companyId} canRespond={role !== 'viewer'} onOpenPanel={openPanel} excerptLength={320} now={now} />
            ))}
          </ul>
        )}
      </Card>
    </PanelFrame>
  )
}
