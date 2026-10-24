import { BookCheck, Flag, PencilLine, ThumbsUp, type LucideIcon } from 'lucide-react'
import { useId, useState } from 'react'
import { errorMessage, useRespondToReflection } from '@/api'
import type { Reflection, ReflectionReaction } from '@/api/types'
import { Button, useToast } from '@/components/ui'
import { isSubmitShortcut } from '@/lib/notes'
import { ADOPTION_NOTICE, REACTION_LABEL, canReact, endorsementAdopts, reactionPlaceholder, reactionResultText, sendLabel, validateReaction } from '@/lib/reflections'

const ICON: Partial<Record<ReflectionReaction, LucideIcon>> = { endorse: ThumbsUp, challenge: Flag, refine: PencilLine }
const MAIN: ReflectionReaction[] = ['endorse', 'challenge', 'refine']

/**
 * Endorse / Challenge / Refine with an inline composer. Endorsing a standard proposal adopts it as a company standard, and the
 * composer says so, with the exact wording of the rule, before the button that does it. Challenge and refine need words.
 */
export function ReactionBar({ companyId, reflection, canRespond, compact = false }: { companyId: string; reflection: Reflection; canRespond: boolean; compact?: boolean }) {
  const respond = useRespondToReflection(companyId)
  const toast = useToast()
  const [open, setOpen] = useState<ReflectionReaction | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const boxId = useId()
  const adopts = endorsementAdopts(reflection)

  if (!canRespond || !canReact(reflection)) return null

  function toggle(r: ReflectionReaction) {
    setError(null)
    setOpen((cur) => (cur === r ? null : r))
  }

  function send() {
    if (!open) return
    const check = validateReaction(open, text)
    if (!check.ok) {
      setError(check.message)
      return
    }
    setError(null)
    const reaction = open
    respond.mutate(
      { id: reflection.id, body: { reaction, ...(check.body ? { body: check.body } : null) } },
      {
        onSuccess: () => {
          toast.success(reactionResultText(reaction, reaction === 'endorse' && adopts))
          setText('')
          setOpen(null)
        },
        onError: (e) => setError(errorMessage(e)),
      },
    )
  }

  return (
    <div>
      <div role="group" aria-label="React to this reflection" className="flex flex-wrap items-center gap-1.5">
        {MAIN.map((r) => {
          const Icon = ICON[r]!
          return (
            <Button key={r} size="sm" variant={open === r ? 'primary' : 'secondary'} aria-expanded={open === r} aria-controls={open === r ? boxId : undefined} icon={<Icon className="size-3.5" />} onClick={() => toggle(r)}>
              {REACTION_LABEL[r]}
            </Button>
          )
        })}
      </div>
      {open && (
        <form
          id={boxId}
          className="mt-2 space-y-2 rounded-md border border-line bg-raised p-2.5"
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          {open === 'endorse' && adopts && (
            <div role="note" className="flex items-start gap-2 rounded-md border border-accent/40 bg-accent-subtle px-2.5 py-2 text-[12.5px] text-fg">
              <BookCheck className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
              <div className="min-w-0">
                <p>{ADOPTION_NOTICE}</p>
                {reflection.proposed_standard && <blockquote className="mt-1.5 border-l-2 border-accent pl-2 text-fg-2">{reflection.proposed_standard}</blockquote>}
              </div>
            </div>
          )}
          <label htmlFor={`${boxId}-text`} className="sr-only">
            {REACTION_LABEL[open]} note
          </label>
          <textarea
            id={`${boxId}-text`}
            autoFocus
            rows={compact ? 2 : 3}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              if (error) setError(null)
            }}
            onKeyDown={(e) => {
              if (isSubmitShortcut(e)) {
                e.preventDefault()
                send()
              }
            }}
            placeholder={reactionPlaceholder(open)}
            aria-invalid={!!error || undefined}
            className="block w-full resize-y rounded-md border border-line-strong bg-canvas px-2.5 py-1.5 text-[13px] leading-snug text-fg placeholder:text-fg-3 focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none aria-[invalid=true]:border-danger"
          />
          {error && (
            <p role="alert" className="text-[12px] text-danger">
              {error}
            </p>
          )}
          <div className="flex items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setOpen(null)} disabled={respond.isPending}>
              Cancel
            </Button>
            <Button size="sm" type="submit" variant="primary" loading={respond.isPending}>
              {sendLabel(open, open === 'endorse' && adopts)}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
