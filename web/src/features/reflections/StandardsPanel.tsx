import { BookMarked, Pencil, Plus, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, isNotDeployed, useStandards, useUpdateStandard } from '@/api'
import type { CompanyStandard, StandardStatus } from '@/api/types'
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorState, LoadingState, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useCompanyRole } from '@/features/canvas/hooks'
import { formatDateTime } from '@/lib/format'
import { splitStandards, standardSourceNote } from '@/lib/reflections'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { StandardDialog } from './StandardDialog'

/**
 * `?panel=standards`: the rules every agent is given. Humans write them or adopt them by endorsing a proposal; the
 * AI Co-founder can propose, never adopt or retire. Editing and retiring are for people only.
 */
export function StandardsPanel() {
  const { companyId, openPanel } = useCanvas()
  const q = useStandards(companyId)
  const role = useCompanyRole(companyId)
  const update = useUpdateStandard(companyId)
  const toast = useToast()
  const [view, setView] = useState<StandardStatus>('active')
  const [dialog, setDialog] = useState<{ standard: CompanyStandard | null } | null>(null)
  const [retiring, setRetiring] = useState<CompanyStandard | null>(null)
  const [retireError, setRetireError] = useState<string | null>(null)
  const canEdit = role !== 'viewer'
  const unavailable = isNotDeployed(q.error)
  const { active, retired } = splitStandards(q.data ?? [])
  const shown = view === 'active' ? active : retired

  async function retire() {
    if (!retiring) return
    setRetireError(null)
    try {
      await update.mutateAsync({ id: retiring.id, body: { status: 'retired' } })
      toast.success(`Retired: ${retiring.title}`)
      setRetiring(null)
    } catch (e) {
      setRetireError(errorMessage(e))
    }
  }

  async function reinstate(s: CompanyStandard) {
    try {
      await update.mutateAsync({ id: s.id, body: { status: 'active' } })
      toast.success(`Active again: ${s.title}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <PanelFrame
      title="Standards"
      subtitle="The rules every agent follows"
      actions={
        canEdit && !unavailable ? (
          <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setDialog({ standard: null })}>
            New standard
          </Button>
        ) : undefined
      }
    >
      <Card>
        <CardHeader title="Company standards" description={`Written by you, or adopted when you endorse a proposal. The ${AI_COFOUNDER_LABEL} can propose standards but can never adopt, change or retire one.`} />
        {!unavailable && (
          <div role="group" aria-label="Show" className="flex gap-1.5 border-b border-line px-4 py-2.5">
            {(['active', 'retired'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={'inline-flex h-8 items-center rounded-md border px-2.5 text-[12.5px] transition-colors ' + (view === v ? 'border-accent bg-accent-subtle text-accent-fg' : 'border-line-strong bg-raised text-fg-2 hover:bg-hover')}
              >
                {v === 'active' ? `Active (${active.length})` : `Retired (${retired.length})`}
              </button>
            ))}
          </div>
        )}
        {q.isPending ? (
          <LoadingState rows={4} label="Loading standards" />
        ) : unavailable ? (
          <NotAvailable milestone="M10" title="Standards are not built yet" description="Company standards arrive with Reflections." />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : shown.length === 0 ? (
          <EmptyState
            icon={BookMarked}
            title={view === 'active' ? 'No standards yet' : 'No retired standards'}
            description={view === 'active' ? 'Write one yourself, or endorse a standard proposal in Reflections.' : 'Retired standards are kept here for the record.'}
            action={
              view === 'active' && canEdit ? (
                <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setDialog({ standard: null })}>
                  New standard
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {shown.map((s) => (
              <li key={s.id} data-testid="standard-row" className="space-y-2 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-[13.5px] font-semibold text-fg">{s.title}</h3>
                  <Badge tone={s.status === 'active' ? 'success' : 'neutral'}>{s.status === 'active' ? 'Active' : 'Retired'}</Badge>
                  {s.applies_to.map((t) => (
                    <Badge key={t} tone="info">
                      {t}
                    </Badge>
                  ))}
                </div>
                <p className="text-[13px] text-fg-2">{s.statement}</p>
                <p className="text-[12px] text-fg-3">
                  {standardSourceNote(s)} · {formatDateTime(s.adopted_at)}
                  {s.source_reflection_id && (
                    <>
                      {' · '}
                      <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'reflection', id: s.source_reflection_id! })}>
                        Open the reflection
                      </button>
                    </>
                  )}
                </p>
                {canEdit && (
                  <div className="flex gap-2">
                    <Button size="sm" icon={<Pencil className="size-3.5" />} onClick={() => setDialog({ standard: s })}>
                      Edit
                    </Button>
                    {s.status === 'active' ? (
                      <Button size="sm" variant="ghost" onClick={() => setRetiring(s)}>
                        Retire
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" icon={<Undo2 className="size-3.5" />} loading={update.isPending && update.variables?.id === s.id} onClick={() => void reinstate(s)}>
                        Make active again
                      </Button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {dialog && <StandardDialog companyId={companyId} standard={dialog.standard} onClose={() => setDialog(null)} />}
      <ConfirmDialog
        open={!!retiring}
        onOpenChange={(o) => {
          if (!o) {
            setRetiring(null)
            setRetireError(null)
          }
        }}
        title="Retire this standard?"
        description={retiring ? `Agents stop being given “${retiring.title}” from their next run. It stays in the list as retired.` : ''}
        confirmLabel="Retire"
        destructive={false}
        loading={update.isPending}
        error={retireError}
        onConfirm={() => void retire()}
      />
    </PanelFrame>
  )
}
