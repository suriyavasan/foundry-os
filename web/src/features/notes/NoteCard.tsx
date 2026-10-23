import clsx from 'clsx'
import { Pin, PinOff } from 'lucide-react'
import type { Agent, ClientNote } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { IconButton } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'
import { actionChips, noteExcerpt, type RefTitles } from '@/lib/notes'
import type { Panel } from '@/lib/panels'
import { AI_COFOUNDER_LABEL, YOU_FOUNDER_LABEL } from '@/lib/terms'
import { ActionChips, NoteChips } from './NoteBadges'

/**
 * One note in a list: status and category chips, the Founder's text, the AI Co-founder's newest reply,
 * and the records it created. The text opens the thread; chips open the records; the pin pins.
 */
export function NoteCard({
  note,
  agents,
  cofounder,
  titles,
  unread,
  onOpen,
  onOpenPanel,
  onTogglePin,
  pinBusy,
  excerptLength = 180,
}: {
  note: ClientNote
  agents: Map<string, Agent>
  cofounder: Agent | undefined
  titles: RefTitles
  unread: boolean
  onOpen: () => void
  onOpenPanel: (p: Panel) => void
  onTogglePin: () => void
  pinBusy?: boolean
  excerptLength?: number
}) {
  const reply = note.last_reply ?? null
  const replyAgent = reply?.author_type === 'agent' ? ((reply.author_id ? agents.get(reply.author_id) : undefined) ?? cofounder) : undefined
  const chips = actionChips(note.triage?.actions, titles)
  const showSummary = !reply && note.triage?.summary

  return (
    <li className={clsx('px-3.5 py-2.5', note.pending && 'opacity-70')} data-testid="note-row">
      <div className="flex items-center gap-2">
        <NoteChips note={note} />
        {unread && (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent-subtle px-1.5 py-0.5 text-[11px] font-medium text-accent-fg" aria-label={`Unread reply from the ${AI_COFOUNDER_LABEL}`}>
            <span aria-hidden className="size-1.5 rounded-full bg-accent" />
            Reply
          </span>
        )}
        <time dateTime={note.created_at} title={formatDateTime(note.created_at)} className="ml-auto shrink-0 text-[11.5px] text-fg-3">
          {note.pending ? 'sending…' : formatRelative(note.created_at)}
        </time>
        {!note.pending && (
          <IconButton label={note.pinned ? 'Unpin note' : 'Pin note'} aria-pressed={note.pinned} disabled={pinBusy} onClick={onTogglePin} className={clsx('-my-1 -mr-1.5 size-7', note.pinned && 'text-accent-fg')}>
            {note.pinned ? <PinOff className="size-3.5" aria-hidden /> : <Pin className="size-3.5" aria-hidden />}
          </IconButton>
        )}
      </div>

      <button
        type="button"
        disabled={note.pending}
        onClick={onOpen}
        aria-label={`Open note: ${noteExcerpt(note.body, 60)}`}
        className="group mt-1.5 block w-full rounded-sm text-left"
      >
        <p className="line-clamp-3 text-[13px] leading-snug break-words whitespace-pre-line text-fg-2 group-hover:text-fg">{noteExcerpt(note.body, excerptLength)}</p>
        {reply && (
          <span className="mt-2 flex items-start gap-2 rounded-md border border-line bg-raised px-2 py-1.5">
            <span className="mt-0.5 shrink-0">{replyAgent ? <AgentAvatar agent={replyAgent} size="xs" /> : <Avatar family={reply.author_type === 'agent' ? 'founder' : 'human'} size="xs" />}</span>
            <span className="min-w-0">
              <span className="block text-[11.5px] font-medium text-fg-3">{reply.author_type === 'agent' ? (replyAgent ? `${replyAgent.name} · ${AI_COFOUNDER_LABEL}` : AI_COFOUNDER_LABEL) : YOU_FOUNDER_LABEL}</span>
              <span className="line-clamp-2 text-[12.5px] leading-snug break-words text-fg">{noteExcerpt(reply.body, 220)}</span>
            </span>
          </span>
        )}
        {showSummary && <span className="mt-1.5 block text-[12px] leading-snug text-fg-3 italic">{note.triage!.summary}</span>}
      </button>
      {chips.length > 0 && <ActionChips chips={chips} onOpen={onOpenPanel} className="mt-2 flex flex-wrap gap-1" />}
    </li>
  )
}
