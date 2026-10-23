import clsx from 'clsx'
import { Paperclip, Send, X } from 'lucide-react'
import { useId, useState } from 'react'
import { errorMessage, useArtifacts, useCreateNote } from '@/api'
import { Button, Select, Tooltip, useToast } from '@/components/ui'
import { isMac } from '@/features/canvas/TopBar'
import { NOTE_MAX_BYTES, counterTone, isSubmitShortcut, noteBytes, restoreDraft, validateNoteBody } from '@/lib/notes'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'

/** DOM id of the widget's textarea: the command palette focuses it ("Write a note to the AI Co-founder"). */
export const WIDGET_COMPOSER_ID = 'founder-note-composer'

const TONE_CLASS = { normal: 'text-fg-3', warning: 'text-warning', danger: 'text-danger' } as const

/**
 * Multiline composer for the Founder's notes. ⌘/Ctrl+Enter sends. The note appears in the list at once
 * (optimistic); if the server refuses it the row disappears and the text returns here.
 */
export function NoteComposer({
  companyId,
  textareaId = WIDGET_COMPOSER_ID,
  rows = 3,
  autoFocus,
  className,
}: {
  companyId: string
  textareaId?: string
  rows?: number
  autoFocus?: boolean
  className?: string
}) {
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const create = useCreateNote(companyId)
  const artifacts = useArtifacts(companyId)
  const toast = useToast()
  const hintId = useId()
  const errorId = useId()

  const bytes = noteBytes(body)
  const tone = counterTone(bytes)
  const mod = isMac() ? '⌘' : 'Ctrl'
  const available = (artifacts.data?.items ?? []).filter((a) => !attachments.includes(a.id))
  const titles = new Map((artifacts.data?.items ?? []).map((a) => [a.id, a.title]))

  function send() {
    const v = validateNoteBody(body)
    if (!v.ok) {
      setError(v.message)
      return
    }
    setError(null)
    const sent = { text: body, files: attachments }
    // Optimistic: the composer clears now; a refusal puts everything back.
    setBody('')
    setAttachments([])
    create.mutate(
      { body: v.body, ...(sent.files.length ? { attachments: sent.files } : null) },
      {
        onError: (e) => {
          setBody((cur) => restoreDraft(cur, sent.text))
          setAttachments((cur) => [...new Set([...sent.files, ...cur])])
          setError(`Could not send the note: ${errorMessage(e)}`)
          toast.error('The note was not sent. Your text is back in the box.')
        },
      },
    )
  }

  return (
    <form
      className={clsx('space-y-2', className)}
      onSubmit={(e) => {
        e.preventDefault()
        send()
      }}
    >
      <label htmlFor={textareaId} className="sr-only">
        Write a note to the {AI_COFOUNDER_LABEL}
      </label>
      <textarea
        id={textareaId}
        autoFocus={autoFocus}
        rows={rows}
        value={body}
        onChange={(e) => {
          setBody(e.target.value)
          if (error) setError(null)
        }}
        onKeyDown={(e) => {
          if (isSubmitShortcut(e)) {
            e.preventDefault()
            send()
          }
        }}
        aria-invalid={!!error || tone === 'danger' || undefined}
        aria-describedby={`${hintId}${error ? ` ${errorId}` : ''}`}
        aria-keyshortcuts="Meta+Enter Control+Enter"
        placeholder={`Pour out a thought for the ${AI_COFOUNDER_LABEL}. Relevant or not, it will be read, sorted and answered.`}
        className="block max-h-56 min-h-[4.5rem] w-full resize-y rounded-md border border-line-strong bg-canvas px-2.5 py-1.5 text-[13px] leading-snug text-fg transition-colors placeholder:text-fg-3 hover:border-fg-3 focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger"
      />
      {attachments.length > 0 && (
        <ul aria-label="Attached artifacts" className="flex flex-wrap gap-1">
          {attachments.map((id) => (
            <li key={id} className="inline-flex max-w-full items-center gap-1 rounded-sm border border-line bg-raised py-0.5 pr-0.5 pl-1.5 text-[11.5px] text-fg-2">
              <Paperclip className="size-3 shrink-0" aria-hidden />
              <span className="truncate">{titles.get(id) ?? 'Artifact'}</span>
              <button type="button" aria-label={`Remove attachment ${titles.get(id) ?? ''}`.trim()} onClick={() => setAttachments((a) => a.filter((x) => x !== id))} className="rounded-sm p-0.5 text-fg-3 hover:bg-hover hover:text-fg">
                <X className="size-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
        {artifacts.isSuccess && (artifacts.data?.items.length ?? 0) > 0 && (
          <Select
            aria-label="Attach an existing artifact"
            value=""
            onChange={(e) => {
              if (e.target.value) setAttachments((a) => [...a, e.target.value])
            }}
            className="h-7 max-w-[11rem] min-w-0 flex-1 text-[12px]"
          >
            <option value="">Attach artifact…</option>
            {available.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </Select>
        )}
        <span id={hintId} className={clsx('ml-auto text-[11.5px] tabular-nums', TONE_CLASS[tone])}>
          <span aria-hidden>
            {bytes.toLocaleString()} / {NOTE_MAX_BYTES.toLocaleString()}
          </span>
          <span className="sr-only">
            {bytes.toLocaleString()} of {NOTE_MAX_BYTES.toLocaleString()} bytes used. Press {mod} Enter to send.
          </span>
        </span>
        <Tooltip content={`Send note (${mod} + Enter)`}>
          <Button type="submit" size="sm" variant="primary" icon={<Send className="size-3.5" />} aria-keyshortcuts="Meta+Enter Control+Enter">
            Send
          </Button>
        </Tooltip>
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-[12px] text-danger">
          {error}
        </p>
      )}
    </form>
  )
}
