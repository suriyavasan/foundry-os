import { useMemo } from 'react'
import { errorMessage, isNotDeployed, useNotes, useUpdateNote } from '@/api'
import { EmptyState, ErrorState, LoadingState, NotAvailable, Widget, useToast } from '@/components/ui'
import { NoteCard } from '@/features/notes/NoteCard'
import { NoteComposer } from '@/features/notes/NoteComposer'
import { useRefTitles, useSeenReplies } from '@/features/notes/hooks'
import { DEFAULT_NOTE_FILTERS, countUnread, filterNotes, hasUnreadReply } from '@/lib/notes'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { useCanvas } from '../CanvasContext'
import { useAgentMap, useTick } from '../hooks'

/** How many notes the widget lists; the rest is one click away in the Notes panel. */
const WIDGET_NOTES = 30

/** The Founder's thought stream: write a note, watch the AI Co-founder triage and answer it. */
export function NotesWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const notes = useNotes(companyId)
  const { map, agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const { seen } = useSeenReplies(companyId)
  const update = useUpdateNote(companyId)
  const toast = useToast()
  useTick(30_000)

  const cofounder = findCofounder(agents)
  const visible = useMemo(() => filterNotes(notes.data ?? [], DEFAULT_NOTE_FILTERS), [notes.data])
  const unread = useMemo(() => countUnread(visible, seen), [visible, seen])
  const unavailable = isNotDeployed(notes.error)

  const subtitle = notes.data
    ? [`${visible.length} note${visible.length === 1 ? '' : 's'}`, unread > 0 ? `${unread} unread repl${unread === 1 ? 'y' : 'ies'}` : null].filter(Boolean).join(' · ')
    : undefined

  function togglePin(id: string, pinned: boolean) {
    update.mutate({ id, pinned: !pinned }, { onError: (e) => toast.error(errorMessage(e)) })
  }

  return (
    <Widget
      title="Founder's notes"
      subtitle={subtitle}
      tall
      onExpand={() => openPanel({ kind: 'notes' })}
      expandLabel="Open all notes"
      className={className}
      bodyClassName="flex flex-col overflow-hidden"
    >
      {!unavailable && (
        <div className="shrink-0 border-b border-line p-3">
          <NoteComposer companyId={companyId} />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {notes.isPending ? (
          <LoadingState rows={4} label="Loading notes" />
        ) : unavailable ? (
          <NotAvailable compact className="p-4" milestone="M5" description={`Founder's notes and the ${AI_COFOUNDER_LABEL} arrive with the autonomous operating model.`} />
        ) : notes.isError ? (
          <ErrorState compact error={notes.error} onRetry={() => void notes.refetch()} retrying={notes.isFetching} />
        ) : visible.length === 0 ? (
          <EmptyState compact title="No notes yet" description={`Write the first thought above. The ${AI_COFOUNDER_LABEL} reads every note, sorts it, acts on it and answers here.`} />
        ) : (
          <ul className="divide-y divide-line">
            {visible.slice(0, WIDGET_NOTES).map((n) => (
              <NoteCard
                key={n.id}
                note={n}
                agents={map}
                cofounder={cofounder}
                titles={titles}
                unread={hasUnreadReply(n, seen)}
                onOpen={() => openPanel({ kind: 'note', id: n.id })}
                onOpenPanel={openPanel}
                onTogglePin={() => togglePin(n.id, n.pinned)}
                pinBusy={update.isPending && update.variables?.id === n.id}
              />
            ))}
            {visible.length > WIDGET_NOTES && (
              <li className="px-3.5 py-2 text-[12px] text-fg-3">
                {visible.length - WIDGET_NOTES} older notes are in the{' '}
                <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'notes' })}>
                  Notes panel
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
