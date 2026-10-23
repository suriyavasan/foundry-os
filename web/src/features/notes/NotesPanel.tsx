import { Pin, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, isNotDeployed, useNotes, useUpdateNote } from '@/api'
import { Card, CardBody, CardHeader, EmptyState, ErrorState, Input, LoadingState, NotAvailable, Select, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { DEFAULT_NOTE_FILTERS, NOTE_CATEGORIES, NOTE_CATEGORY_LABEL, countUnread, filterNotes, hasUnreadReply, type NoteFilters, type NoteStatusFilter } from '@/lib/notes'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { NoteCard } from './NoteCard'
import { NoteComposer } from './NoteComposer'
import { useRefTitles, useSeenReplies } from './hooks'

const STATUS_OPTIONS: Array<{ value: NoteStatusFilter; label: string }> = [
  { value: 'active', label: 'Active (not archived)' },
  { value: 'new', label: 'New' },
  { value: 'triaged', label: 'Triaged' },
  { value: 'archived', label: 'Archived' },
  { value: 'all', label: 'Everything' },
]

/** `?panel=notes`: every note, filtered by status, category and pin, with search. */
export function NotesPanel() {
  const { companyId, openPanel } = useCanvas()
  const notes = useNotes(companyId)
  const { map, agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const { seen } = useSeenReplies(companyId)
  const update = useUpdateNote(companyId)
  const toast = useToast()
  const [filters, setFilters] = useState<NoteFilters>(DEFAULT_NOTE_FILTERS)
  const set = <K extends keyof NoteFilters>(k: K, v: NoteFilters[K]) => setFilters((f) => ({ ...f, [k]: v }))

  const cofounder = findCofounder(agents)
  const shown = useMemo(() => filterNotes(notes.data ?? [], filters), [notes.data, filters])
  const unavailable = isNotDeployed(notes.error)
  const filtered = filters.status !== 'active' || filters.category !== 'all' || filters.pinnedOnly || filters.q.trim() !== ''
  const unread = countUnread(notes.data ?? [], seen)

  return (
    <PanelFrame title="Founder's notes" subtitle={`Everything you told the ${AI_COFOUNDER_LABEL}, and what it did about it`}>
      <div className="space-y-4">
        {!unavailable && (
          <Card>
            <CardHeader title="Write a note" description="Any thought, relevant or not. Markdown works." />
            <CardBody>
              <NoteComposer companyId={companyId} textareaId="founder-note-composer-panel" rows={4} />
            </CardBody>
          </Card>
        )}

        <Card>
          <CardHeader title="All notes" description={notes.data ? `${shown.length} of ${notes.data.length}${unread ? ` · ${unread} unread repl${unread === 1 ? 'y' : 'ies'}` : ''}` : undefined} />
          {!unavailable && (
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
              <div className="relative min-w-48 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-3" aria-hidden />
                <Input aria-label="Search notes" placeholder="Search notes and replies" className="pl-8" value={filters.q} onChange={(e) => set('q', e.target.value)} />
              </div>
              <Select aria-label="Filter by status" className="w-auto" value={filters.status} onChange={(e) => set('status', e.target.value as NoteStatusFilter)}>
                {STATUS_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
              <Select aria-label="Filter by category" className="w-auto" value={filters.category} onChange={(e) => set('category', e.target.value as NoteFilters['category'])}>
                <option value="all">All categories</option>
                {NOTE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {NOTE_CATEGORY_LABEL[c]}
                  </option>
                ))}
              </Select>
              <button
                type="button"
                aria-pressed={filters.pinnedOnly}
                onClick={() => set('pinnedOnly', !filters.pinnedOnly)}
                className={'inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] transition-colors ' + (filters.pinnedOnly ? 'border-accent bg-accent-subtle text-accent-fg' : 'border-line-strong bg-raised text-fg-2 hover:bg-hover')}
              >
                <Pin className="size-3.5" aria-hidden />
                Pinned
              </button>
            </div>
          )}
          {notes.isPending ? (
            <LoadingState rows={5} label="Loading notes" />
          ) : unavailable ? (
            <NotAvailable milestone="M5" title="Founder's notes are not built yet" description={`Notes, triage and replies from the ${AI_COFOUNDER_LABEL} arrive with the autonomous operating model.`} />
          ) : notes.isError ? (
            <ErrorState error={notes.error} onRetry={() => void notes.refetch()} retrying={notes.isFetching} />
          ) : shown.length === 0 ? (
            <EmptyState
              title={filtered ? 'No notes match' : 'No notes yet'}
              description={filtered ? 'Adjust the filters or the search.' : `Write the first thought above. The ${AI_COFOUNDER_LABEL} reads every note and answers here.`}
            />
          ) : (
            <ul className="divide-y divide-line">
              {shown.map((n) => (
                <NoteCard
                  key={n.id}
                  note={n}
                  agents={map}
                  cofounder={cofounder}
                  titles={titles}
                  unread={hasUnreadReply(n, seen)}
                  excerptLength={400}
                  onOpen={() => openPanel({ kind: 'note', id: n.id })}
                  onOpenPanel={openPanel}
                  onTogglePin={() => update.mutate({ id: n.id, pinned: !n.pinned }, { onError: (e) => toast.error(errorMessage(e)) })}
                  pinBusy={update.isPending && update.variables?.id === n.id}
                />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </PanelFrame>
  )
}
