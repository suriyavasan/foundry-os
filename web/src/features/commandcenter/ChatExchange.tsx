import clsx from 'clsx'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import type { Agent, CommandExchange } from '@/api/types'
import { ActionChips } from '@/features/notes/NoteBadges'
import { outputPlainText, statusLineOf, type ChatEntry } from '@/lib/chatFeed'
import { exchangeChips, isOptimisticExchange } from '@/lib/commandCenter'
import type { RefTitles } from '@/lib/notes'
import type { Panel } from '@/lib/panels'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { CofounderFace, HumanBubble, LeftMessage, MessageMenu, TypingDots, replyBubbleClass } from './ChatBubbles'
import { OutputView } from './OutputView'
import { ProgressLines, useProgressLines } from './ProgressLines'

type ExchangeEntry = Extract<ChatEntry, { kind: 'exchange' }>

/**
 * One exchange as a conversation: the Founder's bubble, one quiet line under it while it is not answered, and the reply
 * from the AI Co-founder. Every status still has something to show: a command's output, "waiting", a typing indicator
 * with the latest step, the reply with what it changed, or the reason it failed.
 */
export function ChatExchange({
  entry,
  companyId,
  cofounder,
  titles,
  onOpenPanel,
  onRetry,
  retrying,
}: {
  entry: ExchangeEntry
  companyId: string
  cofounder: Agent | null
  titles?: RefTitles
  onOpenPanel: (p: Panel) => void
  onRetry: (e: CommandExchange) => void
  retrying: boolean
}) {
  const { exchange: ex, view: v } = entry
  const status = statusLineOf(ex, v)
  const optimistic = isOptimisticExchange(ex)
  const chips = exchangeChips(ex, titles)
  return (
    <li data-testid="exchange" data-status={ex.status} data-kind={ex.input_kind} className={clsx('cos-msg-in space-y-1', entry.tight ? 'mt-1' : 'mt-3 first:mt-0')}>
      <HumanBubble text={ex.input} command={ex.input_kind === 'command'} title={ex.created_at} />
      {status && (
        <p role={status.alert ? 'alert' : 'status'} className={clsx('flex justify-end gap-1 pr-1 text-[11.5px]', status.tone === 'danger' ? 'text-danger' : status.tone === 'warning' ? 'text-warning' : 'text-fg-3')}>
          <span>{status.text}</span>
          {status.retry && (
            <>
              <span aria-hidden>·</span>
              <button type="button" disabled={retrying} onClick={() => onRetry(ex)} className="font-medium text-accent-fg hover:underline disabled:opacity-60">
                {status.retry}
              </button>
            </>
          )}
        </p>
      )}

      {!optimistic && (v.phase === 'output' || v.phase === 'answered') && (
        <LeftMessage name={AI_COFOUNDER_LABEL} showAuthor={entry.showAuthor} face={<CofounderFace cofounder={cofounder} />}>
          <div className={replyBubbleClass}>
            {ex.output ? <OutputView output={ex.output} onOpenPanel={onOpenPanel} compact /> : <p className="text-fg-3">{v.placeholder}</p>}
            <Changed chips={chips} onOpenPanel={onOpenPanel} />
          </div>
          <MessageMenu text={outputPlainText(ex.output)} onOpenNote={ex.note_id ? () => onOpenPanel({ kind: 'note', id: ex.note_id! }) : undefined} />
        </LeftMessage>
      )}

      {v.phase === 'working' && !optimistic && (
        <Working companyId={companyId} exchange={ex} cofounder={cofounder} />
      )}

      {v.phase === 'failed' && (
        <LeftMessage name={AI_COFOUNDER_LABEL} showAuthor={entry.showAuthor} face={<CofounderFace cofounder={cofounder} />}>
          {ex.output ? (
            <div role="alert">
              <OutputView output={{ ...ex.output, kind: 'error' }} onOpenPanel={onOpenPanel} compact />
            </div>
          ) : (
            <p role="alert" className="rounded-2xl rounded-bl-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-fg">
              {v.placeholder}
            </p>
          )}
        </LeftMessage>
      )}
    </li>
  )
}

/** "Changed 3 things": one quiet line that opens into the records the AI Co-founder touched. */
function Changed({ chips, onOpenPanel }: { chips: ReturnType<typeof exchangeChips>; onOpenPanel: (p: Panel) => void }) {
  const [open, setOpen] = useState(false)
  if (chips.length === 0) return null
  const Icon = open ? ChevronDown : ChevronRight
  return (
    <div className="mt-1.5">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-0.5 text-[12px] text-fg-3 transition-colors hover:text-fg">
        <Icon className="size-3.5" aria-hidden />
        Changed {chips.length} {chips.length === 1 ? 'thing' : 'things'}
      </button>
      {open && <ActionChips chips={chips} onOpen={onOpenPanel} label="Changed while answering" className="mt-1 flex flex-wrap gap-1" />}
    </div>
  )
}

/** The typing indicator: three dots and the one latest step, with "Show steps" for the last few. */
function Working({ companyId, exchange: ex, cofounder }: { companyId: string; exchange: CommandExchange; cofounder: Agent | null }) {
  const lines = useProgressLines(companyId, ex.run_id, cofounder?.id ?? null, ex.created_at)
  const [open, setOpen] = useState(false)
  const latest = lines[lines.length - 1]
  return (
    <LeftMessage name={AI_COFOUNDER_LABEL} showAuthor face={<CofounderFace cofounder={cofounder} />}>
      <div role="status" className={clsx(replyBubbleClass, 'py-2')}>
        <p className="flex items-center gap-2 text-fg-2">
          <TypingDots />
          <span className="sr-only">{AI_COFOUNDER_LABEL} is working on it.</span>
          <span aria-hidden={!latest} className="min-w-0 truncate text-[12.5px] text-fg-3">
            {latest ? latest.text : 'Working on it…'}
          </span>
        </p>
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="mt-1 text-[12px] text-fg-3 transition-colors hover:text-fg">
          {open ? 'Hide steps' : 'Show steps'}
        </button>
        {open && <ProgressLines lines={lines} />}
      </div>
    </LeftMessage>
  )
}
