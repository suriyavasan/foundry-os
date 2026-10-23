import { Plus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, isNotDeployed, useCreateMemory, useMemory, useMemoryDecision } from '@/api'
import type { Agent, MemoryEntry, MemoryKind } from '@/api/types'
import { Badge, Button, Card, Chip, Dialog, EmptyState, ErrorState, Field, FormError, Input, LoadingState, Markdown, NotAvailable, Select, Textarea, Tooltip, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { CreatorChip } from '@/features/shared/AgentChip'
import { formatDateTime, formatRelative } from '@/lib/format'
import {
  DEFAULT_MEMORY_FILTERS,
  MEMORY_KINDS,
  MEMORY_KIND_HELP,
  MEMORY_KIND_LABEL,
  MEMORY_STATUSES,
  MEMORY_STATUS_LABEL,
  MEMORY_STATUS_TONE,
  countByKind,
  filterMemory,
  isDecidable,
  provenanceOf,
  type MemoryFilters,
} from '@/lib/memory'
import { NOTE_MAX_BYTES, noteBytes } from '@/lib/notes'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'

/** `?panel=memory`: what the company knows. Only verified entries reach agents; humans verify or reject the rest. */
export function MemoryPanel() {
  const { companyId } = useCanvas()
  const q = useMemory(companyId)
  const { map } = useAgentMap(companyId)
  const [filters, setFilters] = useState<MemoryFilters>(DEFAULT_MEMORY_FILTERS)
  const [creating, setCreating] = useState(false)
  const unavailable = isNotDeployed(q.error)

  const counts = useMemo(() => countByKind(q.data ?? []), [q.data])
  const shown = useMemo(() => filterMemory(q.data ?? [], filters), [q.data, filters])
  const proposed = (q.data ?? []).filter(isDecidable).length

  return (
    <PanelFrame
      title="Memory"
      subtitle="What the company knows. Only verified entries are shown to agents."
      actions={
        !unavailable && (
          <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} disabled={!q.data} onClick={() => setCreating(true)}>
            New entry
          </Button>
        )
      }
    >
      <div className="space-y-4">
        {proposed > 0 && (
          <p role="status" className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[13px] text-fg">
            {proposed} proposed {proposed === 1 ? 'entry is' : 'entries are'} waiting for a decision. Agents do not see them until they are verified.
          </p>
        )}
        <Card>
          {!unavailable && (
            <div className="space-y-2.5 border-b border-line px-4 py-3">
              <div role="group" aria-label="Filter by kind" className="flex flex-wrap gap-1.5">
                {(['all', ...MEMORY_KINDS] as const).map((k) => {
                  const on = filters.kind === k
                  return (
                    <button
                      key={k}
                      type="button"
                      aria-pressed={on}
                      title={k === 'all' ? undefined : MEMORY_KIND_HELP[k]}
                      onClick={() => setFilters((f) => ({ ...f, kind: k }))}
                      className={'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12.5px] transition-colors ' + (on ? 'border-accent bg-accent-subtle text-accent-fg' : 'border-line-strong bg-raised text-fg-2 hover:bg-hover')}
                    >
                      {k === 'all' ? 'All' : MEMORY_KIND_LABEL[k]}
                      <span className="text-[11.5px] text-fg-3 tabular-nums">{q.data ? counts[k] : '…'}</span>
                    </button>
                  )
                })}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-48 flex-1">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-3" aria-hidden />
                  <Input aria-label="Search memory" placeholder="Search memory" className="pl-8" value={filters.q} onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))} />
                </div>
                <Select aria-label="Filter by status" className="w-auto" value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as MemoryFilters['status'] }))}>
                  <option value="all">All statuses</option>
                  {MEMORY_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {MEMORY_STATUS_LABEL[s]}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          )}
          {q.isPending ? (
            <LoadingState rows={5} label="Loading memory" />
          ) : unavailable ? (
            <NotAvailable milestone="M5" title="Company memory is not built yet" description="Verified facts, decisions, requirements and lessons arrive with the autonomous operating model." />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          ) : shown.length === 0 ? (
            <EmptyState
              title={(q.data?.length ?? 0) === 0 ? 'Nothing remembered yet' : 'No entries match'}
              description={(q.data?.length ?? 0) === 0 ? `Requirement and context notes are kept here automatically, and the ${AI_COFOUNDER_LABEL} proposes lessons. You can add an entry yourself.` : 'Adjust the filters or the search.'}
            />
          ) : (
            <ul className="divide-y divide-line">
              {shown.map((e) => (
                <MemoryRow key={e.id} entry={e} agents={map} />
              ))}
            </ul>
          )}
        </Card>
      </div>
      <NewEntryDialog open={creating} onOpenChange={setCreating} />
    </PanelFrame>
  )
}

function MemoryRow({ entry: e, agents }: { entry: MemoryEntry; agents: Map<string, Agent> }) {
  const { companyId, openPanel } = useCanvas()
  const decide = useMemoryDecision(companyId)
  const toast = useToast()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const prov = provenanceOf(e.source)

  function run(action: 'verify' | 'reject') {
    decide.mutate(
      { id: e.id, action, ...(action === 'reject' && reason.trim() ? { reason: reason.trim() } : null) },
      {
        onSuccess: () => {
          toast.success(action === 'verify' ? 'Entry verified. Agents can use it now.' : 'Entry rejected')
          setRejecting(false)
          setReason('')
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <li className="px-4 py-3" data-testid="memory-row" data-status={e.status}>
      <div className="flex flex-wrap items-center gap-2">
        <Tooltip content={MEMORY_KIND_HELP[e.kind]}>
          <Badge tone="accent">{MEMORY_KIND_LABEL[e.kind] ?? e.kind}</Badge>
        </Tooltip>
        <Badge tone={MEMORY_STATUS_TONE[e.status] ?? 'neutral'}>{MEMORY_STATUS_LABEL[e.status] ?? e.status}</Badge>
        {e.title && <h3 className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-fg">{e.title}</h3>}
        <time dateTime={e.created_at} title={formatDateTime(e.created_at)} className="ml-auto shrink-0 text-[12px] text-fg-3">
          {formatRelative(e.created_at)}
        </time>
      </div>
      <Markdown source={e.body} className="mt-1.5 text-[13px] text-fg-2" />
      {e.tags?.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {e.tags.map((t) => (
            <Chip key={t}>{t}</Chip>
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-fg-3">
        <span className="inline-flex items-center gap-1.5">
          Source:{' '}
          {prov ? (
            prov.panel ? (
              <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel(prov.panel!)}>
                {prov.text}
              </button>
            ) : (
              <span className="text-fg-2">{prov.text}</span>
            )
          ) : (
            <span>not recorded</span>
          )}
        </span>
        {e.proposed_by_type && (
          <span className="inline-flex items-center gap-1.5">
            Proposed by <CreatorChip type={e.proposed_by_type} id={e.proposed_by_id} agents={agents} />
          </span>
        )}
        {e.verified_at && e.verified_by_type && (
          <span className="inline-flex items-center gap-1.5">
            Verified by <CreatorChip type={e.verified_by_type} id={e.verified_by_id} agents={agents} /> {formatRelative(e.verified_at)}
          </span>
        )}
        {e.status === 'rejected' && e.rejection_reason && <span>Reason: {e.rejection_reason}</span>}
      </div>
      {isDecidable(e) && (
        <div className="mt-2.5">
          {rejecting ? (
            <div className="flex flex-wrap items-center gap-2">
              <Input aria-label={`Reason for rejecting ${e.title ?? 'this entry'}`} placeholder="Reason (optional)" className="max-w-xs flex-1" value={reason} onChange={(ev) => setReason(ev.target.value)} />
              <Button size="sm" variant="danger" loading={decide.isPending} onClick={() => run('reject')}>
                Confirm reject
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button size="sm" variant="primary" loading={decide.isPending} onClick={() => run('verify')} aria-label={`Verify ${e.title ?? 'entry'}`}>
                Verify
              </Button>
              <Button size="sm" onClick={() => setRejecting(true)} aria-label={`Reject ${e.title ?? 'entry'}`}>
                Reject
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function NewEntryDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { companyId } = useCanvas()
  const create = useCreateMemory(companyId)
  const toast = useToast()
  const [kind, setKind] = useState<MemoryKind>('fact')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setKind('fact')
    setTitle('')
    setBody('')
    setError(null)
  }

  async function submit() {
    if (!body.trim()) return setError('Write what should be remembered.')
    if (noteBytes(body) > NOTE_MAX_BYTES) return setError(`An entry can be at most ${NOTE_MAX_BYTES.toLocaleString()} bytes.`)
    setError(null)
    try {
      await create.mutateAsync({ kind, body: body.trim(), ...(title.trim() ? { title: title.trim() } : null) })
      toast.success('Entry saved and verified')
      reset()
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset()
        onOpenChange(o)
      }}
      title="New memory entry"
      description="Entries you write are verified straight away and shown to every agent that needs them. Never paste secrets."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} onClick={() => void submit()}>
            Save verified entry
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label="Kind" hint={MEMORY_KIND_HELP[kind]}>
          {({ id }) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)}>
              {MEMORY_KINDS.map((k) => (
                <option key={k} value={k}>
                  {MEMORY_KIND_LABEL[k]}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Title (optional)">{({ id }) => <Input id={id} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Entry" required>
          {({ id, invalid }) => <Textarea id={id} rows={5} value={body} invalid={invalid} onChange={(e) => setBody(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
