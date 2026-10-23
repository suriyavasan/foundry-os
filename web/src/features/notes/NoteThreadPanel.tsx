import { Archive, ArchiveRestore, Paperclip, Pin, PinOff, Send } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, errorMessage, isNotDeployed, useArtifacts, useNote, useNotes, useReplyToNote, useUpdateNote } from '@/api'
import type { Agent, NoteReply } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, FormError, LoadingState, Markdown, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { isMac } from '@/features/canvas/TopBar'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { formatDateTime, formatRelative } from '@/lib/format'
import { NOTE_MAX_BYTES, actionChips, counterTone, isSubmitShortcut, noteBytes, restoreDraft, validateNoteBody } from '@/lib/notes'
import { AI_COFOUNDER_LABEL, YOU_FOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { ActionChips, NoteChips } from './NoteBadges'
import { useRefTitles, useSeenReplies } from './hooks'

/** `?panel=note:<id>`: the whole note, how it was triaged, what it caused, and the conversation under it. */
export function NoteThreadPanel({ noteId }: { noteId: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useNote(companyId, noteId)
  const list = useNotes(companyId)
  const { map, agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const artifacts = useArtifacts(companyId)
  const update = useUpdateNote(companyId)
  const { markSeen } = useSeenReplies(companyId)
  const toast = useToast()
  const cofounder = findCofounder(agents)

  const note = q.data
  // Opening the thread counts as reading the AI Co-founder's newest reply.
  const newestAgentReply = useMemo(() => [...(note?.replies ?? [])].reverse().find((r) => r.author_type === 'agent')?.created_at ?? null, [note?.replies])
  useEffect(() => {
    if (newestAgentReply) markSeen(noteId, newestAgentReply)
  }, [newestAgentReply, noteId, markSeen])

  const chips = actionChips(note?.triage?.actions, titles)
  const artifactTitles = new Map((artifacts.data?.items ?? []).map((a) => [a.id, a.title]))

  function patch(body: { pinned?: boolean; status?: 'archived' | 'new' | 'triaged' }, done?: string) {
    update.mutate({ id: noteId, ...body }, { onSuccess: () => done && toast.success(done), onError: (e) => toast.error(errorMessage(e)) })
  }

  const missing = q.error instanceof ApiError && q.error.status === 404

  return (
    <PanelFrame
      title="Note"
      subtitle={note ? `Written ${formatRelative(note.created_at)}` : undefined}
      actions={
        note && (
          <>
            <Button size="sm" icon={note.pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />} aria-pressed={note.pinned} disabled={update.isPending} onClick={() => patch({ pinned: !note.pinned })}>
              {note.pinned ? 'Unpin' : 'Pin'}
            </Button>
            <Button
              size="sm"
              icon={note.status === 'archived' ? <ArchiveRestore className="size-3.5" /> : <Archive className="size-3.5" />}
              disabled={update.isPending}
              onClick={() => patch({ status: note.status === 'archived' ? (note.triage ? 'triaged' : 'new') : 'archived' }, note.status === 'archived' ? 'Note restored' : 'Note archived')}
            >
              {note.status === 'archived' ? 'Restore' : 'Archive'}
            </Button>
          </>
        )
      }
    >
      {q.isPending ? (
        <Card>
          <LoadingState rows={6} label="Loading note" />
        </Card>
      ) : missing ? (
        <Card>
          {isNotDeployed(list.error) ? (
            <NotAvailable milestone="M5" title="Founder's notes are not built yet" description={`Notes and the ${AI_COFOUNDER_LABEL} arrive with the autonomous operating model.`} />
          ) : (
            <EmptyState title="Note not found" description="It may have been deleted, or it belongs to another company." />
          )}
        </Card>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      ) : (
        <div className="space-y-4">
          <Card>
            <CardHeader title="The note" description={formatDateTime(note!.created_at)} actions={<NoteChips note={note!} />} />
            <CardBody>
              <Markdown source={note!.body} className="text-[13.5px] text-fg" />
              {note!.attachments.length > 0 && (
                <ul aria-label="Attached artifacts" className="mt-3 flex flex-wrap gap-1.5">
                  {note!.attachments.map((id) => (
                    <li key={id}>
                      <button type="button" onClick={() => openPanel({ kind: 'artifact', id })} className="inline-flex max-w-full items-center gap-1 rounded-sm border border-line bg-raised px-1.5 py-0.5 text-[12px] text-fg-2 hover:bg-hover">
                        <Paperclip className="size-3 shrink-0" aria-hidden />
                        <span className="truncate">{artifactTitles.get(id) ?? 'Artifact'}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card aria-label="Triage">
            <CardHeader title="Triage" description={note!.triage ? `Sorted by the ${AI_COFOUNDER_LABEL}` : undefined} />
            <CardBody className="space-y-3">
              {note!.triage ? (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-2">
                    <NoteChips note={{ status: 'triaged', triage: note!.triage }} />
                    {note!.triage.confidence != null && <span title="How sure the AI Co-founder said it was">Confidence {Math.round(note!.triage.confidence * 100)}%</span>}
                  </div>
                  <p className="text-[13px] text-fg">{note!.triage.summary}</p>
                  <div>
                    <div className="mb-1 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">What it caused</div>
                    {chips.length === 0 ? <p className="text-[12.5px] text-fg-3">No records were created or changed.</p> : <ActionChips chips={chips} onOpen={openPanel} />}
                  </div>
                </>
              ) : (
                <p className="text-[13px] text-fg-3">
                  {note!.status === 'archived' ? 'This note was archived before it was triaged.' : `Waiting for the ${AI_COFOUNDER_LABEL}. New notes are read in the next planning run (notes sent close together are batched).`}
                </p>
              )}
            </CardBody>
          </Card>

          <Card aria-label="Conversation">
            <CardHeader title="Conversation" description={`${note!.replies.length} repl${note!.replies.length === 1 ? 'y' : 'ies'}`} />
            <CardBody className="space-y-4">
              {note!.replies.length === 0 ? (
                <p className="text-[13px] text-fg-3">No replies yet. The {AI_COFOUNDER_LABEL} answers every note once it has handled it.</p>
              ) : (
                <ol className="space-y-4">
                  {note!.replies.map((r) => (
                    <ReplyRow key={r.id} reply={r} agents={map} cofounder={cofounder} />
                  ))}
                </ol>
              )}
              <ReplyComposer companyId={companyId} noteId={noteId} />
            </CardBody>
          </Card>
        </div>
      )}
    </PanelFrame>
  )
}

function ReplyRow({ reply, agents, cofounder }: { reply: NoteReply; agents: Map<string, Agent>; cofounder: Agent | undefined }) {
  const byAgent = reply.author_type === 'agent'
  const agent = byAgent ? ((reply.author_id ? agents.get(reply.author_id) : undefined) ?? cofounder) : undefined
  return (
    <li className="flex items-start gap-3" data-author={reply.author_type}>
      <span className="mt-0.5 shrink-0">{agent ? <AgentAvatar agent={agent} size="md" /> : <Avatar family={byAgent ? 'founder' : 'human'} size="md" />}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[13px] font-medium text-fg">{byAgent ? (agent ? `${agent.name} · ${AI_COFOUNDER_LABEL}` : AI_COFOUNDER_LABEL) : YOU_FOUNDER_LABEL}</span>
          <time dateTime={reply.created_at} title={formatDateTime(reply.created_at)} className="text-[11.5px] text-fg-3">
            {formatRelative(reply.created_at)}
          </time>
        </div>
        <Markdown source={reply.body} className="mt-0.5 text-[13px] text-fg" />
      </div>
    </li>
  )
}

/** Reply box. Replying re-opens triage, so the AI Co-founder reads the note again with this reply. */
export function ReplyComposer({ companyId, noteId }: { companyId: string; noteId: string }) {
  const reply = useReplyToNote(companyId, noteId)
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)
  const mod = isMac() ? '⌘' : 'Ctrl'
  const bytes = noteBytes(body)
  const tone = counterTone(bytes)

  async function send() {
    const v = validateNoteBody(body)
    if (!v.ok) {
      setError(v.message)
      return
    }
    setError(null)
    const sent = body
    setBody('')
    try {
      await reply.mutateAsync({ body: v.body })
    } catch (e) {
      setBody((cur) => restoreDraft(cur, sent))
      setError(`Could not send the reply: ${errorMessage(e)}`)
    }
  }

  const id = `reply-${noteId}`
  return (
    <form
      className="space-y-2 border-t border-line pt-4"
      onSubmit={(e) => {
        e.preventDefault()
        void send()
      }}
    >
      <label htmlFor={id} className="block text-[12px] font-medium text-fg-2">
        Reply to this note
      </label>
      <textarea
        id={id}
        rows={3}
        value={body}
        aria-describedby={`${id}-hint`}
        aria-keyshortcuts="Meta+Enter Control+Enter"
        aria-invalid={!!error || tone === 'danger' || undefined}
        onChange={(e) => {
          setBody(e.target.value)
          if (error) setError(null)
        }}
        onKeyDown={(e) => {
          if (isSubmitShortcut(e)) {
            e.preventDefault()
            void send()
          }
        }}
        placeholder={`Add detail, correct course, or answer the ${AI_COFOUNDER_LABEL}…`}
        className="block min-h-16 w-full resize-y rounded-md border border-line-strong bg-canvas px-2.5 py-1.5 text-[13px] leading-snug text-fg placeholder:text-fg-3 hover:border-fg-3 focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger"
      />
      <div className="flex items-center gap-3">
        <p id={`${id}-hint`} className="min-w-0 flex-1 text-[12px] text-fg-3">
          Replying re-opens triage: the {AI_COFOUNDER_LABEL} reads the note again with your reply. {mod} + Enter sends.
        </p>
        <span className={tone === 'danger' ? 'text-[11.5px] text-danger tabular-nums' : 'text-[11.5px] text-fg-3 tabular-nums'}>
          {bytes.toLocaleString()} / {NOTE_MAX_BYTES.toLocaleString()}
        </span>
        <Button type="submit" size="sm" variant="primary" loading={reply.isPending} icon={<Send className="size-3.5" />}>
          Reply
        </Button>
      </div>
      <FormError message={error} />
    </form>
  )
}
