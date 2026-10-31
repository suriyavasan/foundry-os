import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import clsx from 'clsx'
import { Check, Copy, FileText, MoreHorizontal } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import type { Agent } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'

/** Width of the face column: the avatar (26 px) and its gap. Replies that continue a group are indented by the same amount. */
export const GUTTER = 'w-[26px]'

/** The Co-founder's face, or the founder glyph when this company has no Co-founder to show. */
export function CofounderFace({ cofounder }: { cofounder: Agent | null }) {
  return cofounder ? <AgentAvatar agent={cofounder} size="sm" /> : <Avatar family="founder" size="sm" />
}

/**
 * A message from someone else: their face on the left (the first message of a group only; later ones are indented), their name
 * above it, then the bubbles. `name` is also the accessible name of the group.
 */
export function LeftMessage({ name, showAuthor, face, tight, children }: { name: string; showAuthor: boolean; face: ReactNode; tight?: boolean; children: ReactNode }) {
  return (
    <div role="group" aria-label={name} className={clsx('flex items-end gap-2', tight && '-mt-1')}>
      <div className={clsx('shrink-0 self-end', GUTTER)}>{showAuthor && face}</div>
      <div className="min-w-0 max-w-[88%] flex-1">
        {showAuthor && <p className="mb-0.5 pl-1 text-[11.5px] font-medium text-fg-3">{name}</p>}
        <div className="group relative w-fit max-w-full">{children}</div>
      </div>
    </div>
  )
}

/** A bubble of the Founder's own words, right-aligned. Slash commands keep the command text in mono, in a slimmer bubble. */
export function HumanBubble({ text, command, title }: { text: string; command: boolean; title?: string }) {
  return (
    <div role="group" aria-label="You" className="flex justify-end">
      <p
        title={title}
        className={clsx(
          'max-w-[80%] rounded-2xl rounded-br-md bg-accent-subtle break-words whitespace-pre-wrap text-fg',
          command ? 'px-2.5 py-1 font-mono text-[12.5px] leading-5' : 'px-3 py-1.5 text-[13.5px] leading-[1.45]',
        )}
      >
        {text}
      </p>
    </div>
  )
}

export const replyBubbleClass = 'min-w-0 rounded-2xl rounded-bl-md border border-line bg-raised px-3 py-2 text-[13.5px] leading-[1.45] text-fg'

/** Three quiet dots: the Co-founder is on it. Still under reduced motion (the dots just do not pulse). */
export function TypingDots() {
  return (
    <span aria-hidden className="inline-flex items-center gap-1">
      {[0, 0.25, 0.5].map((d) => (
        <span key={d} className="cos-live-dot size-1.5 rounded-full bg-fg-3" data-live="true" style={{ animationDelay: `${d}s` }} />
      ))}
    </span>
  )
}

/** The small ⋯ on a reply: copy its text, open the note it came from. Visible on hover or focus (always on touch screens). */
export function MessageMenu({ text, onOpenNote }: { text: string; onOpenNote?: () => void }) {
  const [copied, setCopied] = useState(false)
  const item = 'flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-hover'
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable: nothing to confirm */
    }
  }
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label="Message options"
        className="absolute top-1 -right-7 inline-flex size-6 items-center justify-center rounded-md text-fg-3 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-hover hover:text-fg focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100"
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={4} className="z-[60] min-w-40 rounded-md bg-surface p-1 shadow-pop">
          <DropdownMenu.Item className={item} disabled={!text} onSelect={() => void copy()}>
            {copied ? <Check className="size-4 text-success" aria-hidden /> : <Copy className="size-4 text-fg-2" aria-hidden />}
            {copied ? 'Copied' : 'Copy text'}
          </DropdownMenu.Item>
          {onOpenNote && (
            <DropdownMenu.Item className={item} onSelect={onOpenNote}>
              <FileText className="size-4 text-fg-2" aria-hidden /> Open the note
            </DropdownMenu.Item>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

