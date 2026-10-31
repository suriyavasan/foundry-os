import { ArrowDown } from 'lucide-react'
import { useMemo, useRef } from 'react'
import type { Agent, HumanWorkItem } from '@/api/types'
import { isNotDeployed, useHumanWork, useQuestions, type FounderQuestion } from '@/api'
import { ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap, useCompanyRole, useTick } from '@/features/canvas/hooks'
import { useRefTitles } from '@/features/notes/hooks'
import { buildChat } from '@/lib/chatFeed'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { ChatExchange } from './ChatExchange'
import { CofounderFace, LeftMessage, replyBubbleClass } from './ChatBubbles'
import { CommandInput, type CommandInputHandle } from './CommandInput'
import { HumanWorkBubble } from './HumanWorkBubble'
import { QuestionBubble } from './QuestionBubble'
import type { useCommandCenter } from './useCommandCenter'
import { useChatScroll } from './useChatScroll'

const NO_QUESTIONS: FounderQuestion[] = []
const NO_HUMAN_WORK: HumanWorkItem[] = []
const SUGGESTIONS = ["What's the plan this week?", '/status', 'Hire a designer']

/**
 * The Command Center as a chat: the conversation (oldest at the top, newest at the bottom, older history loads when you scroll up)
 * and the composer pinned under it. The widget and the full panel both render this, so they behave the same. It fills the height
 * its parent gives it.
 */
export function CommandChat({
  companyId,
  cc,
  inputId,
  autoFocus,
  compact,
}: {
  companyId: string
  cc: ReturnType<typeof useCommandCenter>
  inputId?: string
  autoFocus?: boolean
  compact?: boolean
}) {
  const { openPanel } = useCanvas()
  const role = useCompanyRole(companyId)
  const { agents } = useAgentMap(companyId)
  const titles = useRefTitles(companyId)
  const now = useTick(15_000)
  const questions = useQuestions(companyId)
  const humanWork = useHumanWork(companyId, { status: 'open' })
  const input = useRef<CommandInputHandle>(null)
  const cofounder = findCofounder(agents) ?? null
  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents])
  const unavailable = isNotDeployed(cc.feed.error)
  const canWrite = role !== 'viewer'
  const hasMore = !!cc.feed.hasNextPage

  // Founder questions are an optional feature: a backend without them (404), or a person who may not read them (403), shows none.
  const shownQuestions = questions.data ?? NO_QUESTIONS
  // Human-work requests (M22) are optional too: a backend without them shows none.
  const shownHumanWork = humanWork.data ?? NO_HUMAN_WORK
  const entries = useMemo(() => buildChat(cc.exchanges, shownQuestions, { now, hasMore, humanWork: shownHumanWork }), [cc.exchanges, shownQuestions, now, hasMore, shownHumanWork])
  const { ref: scrollRef, onScroll, toBottom, showNewPill } = useChatScroll({
    firstKey: entries[0]?.key,
    lastKey: entries[entries.length - 1]?.key,
    onReachTop: () => {
      if (cc.feed.hasNextPage && !cc.feed.isFetchingNextPage) void cc.feed.fetchNextPage()
    },
  })

  const empty = entries.length === 0
  const loading = cc.feed.isPending
  const failed = cc.feed.isError && !unavailable

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions" aria-label="Command Center conversation" className="h-full overflow-x-hidden overflow-y-auto px-3.5 py-3">
          {loading ? (
            <LoadingState rows={4} label="Loading the Command Center" />
          ) : unavailable ? (
            <NotAvailable compact={compact} className={compact ? 'p-1' : undefined} milestone="M10" title="The Command Center is not built yet" description={`Commands and the ${AI_COFOUNDER_LABEL}'s answers arrive with the Command Center.`} />
          ) : failed ? (
            <ErrorState compact={compact} error={cc.feed.error} onRetry={() => void cc.feed.refetch()} retrying={cc.feed.isFetching} />
          ) : empty ? (
            <Greeting cofounder={cofounder} canWrite={canWrite} onPick={(text) => input.current?.fill(text)} />
          ) : (
            <>
              {hasMore && (
                <div className="mb-2 text-center">
                  <button type="button" disabled={cc.feed.isFetchingNextPage} onClick={() => void cc.feed.fetchNextPage()} className="rounded-full px-3 py-1 text-[12px] text-fg-3 transition-colors hover:bg-hover hover:text-fg disabled:opacity-60">
                    {cc.feed.isFetchingNextPage ? 'Loading older messages…' : 'Load older messages'}
                  </button>
                </div>
              )}
              <ol className="flex flex-col">
                {entries.map((e) =>
                  e.kind === 'day' ? (
                    <li key={e.key} className="my-3 flex items-center gap-3 text-[11px] font-medium text-fg-3 first:mt-0" role="separator" aria-label={e.label}>
                      <span aria-hidden className="h-px flex-1 bg-line" />
                      {e.label}
                      <span aria-hidden className="h-px flex-1 bg-line" />
                    </li>
                  ) : e.kind === 'exchange' ? (
                    <ChatExchange key={e.key} entry={e} companyId={companyId} cofounder={cofounder} titles={titles} onOpenPanel={openPanel} onRetry={cc.retryExchange} retrying={cc.retryingId === e.exchange.id} />
                  ) : e.kind === 'human_work' ? (
                    <HumanWorkBubble key={e.key} item={e.item} agent={e.item.requested_by_agent_id ? agentById.get(e.item.requested_by_agent_id) : undefined} companyId={companyId} role={role} showAuthor={e.showAuthor} tight={e.tight} />
                  ) : (
                    <QuestionBubble key={e.key} question={e.question} agent={agentById.get(e.question.agent_id)} companyId={companyId} canAnswer={canWrite} showAuthor={e.showAuthor} tight={e.tight} />
                  ),
                )}
              </ol>
            </>
          )}
        </div>
        {showNewPill && (
          <button
            type="button"
            onClick={toBottom}
            className="cos-msg-in absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border border-line-strong bg-surface px-3 py-1 text-[12px] font-medium text-fg shadow-pop transition-colors hover:bg-hover"
          >
            New messages <ArrowDown className="size-3.5" aria-hidden />
          </button>
        )}
      </div>
      {!unavailable &&
        (canWrite ? (
          <div className="shrink-0 border-t border-line p-3">
            <CommandInput companyId={companyId} onSubmit={cc.submit} id={inputId} autoFocus={autoFocus} compact={compact} handle={input} />
          </div>
        ) : (
          <p className="shrink-0 border-t border-line px-3.5 py-2.5 text-[12px] text-fg-3">You can read the conversation. Viewers cannot send commands.</p>
        ))}
    </div>
  )
}

/** The empty conversation: a short hello from the AI Co-founder and three things to try. */
function Greeting({ cofounder, canWrite, onPick }: { cofounder: Agent | null; canWrite: boolean; onPick: (text: string) => void }) {
  return (
    <div className="space-y-3">
      <LeftMessage name={AI_COFOUNDER_LABEL} showAuthor face={<CofounderFace cofounder={cofounder} />}>
        <p className={replyBubbleClass}>
          Hi{cofounder ? `, I'm ${cofounder.name}` : ''}. I'm your {AI_COFOUNDER_LABEL}. Tell me what to work on, ask where things stand, or type / to see what you can run.
        </p>
      </LeftMessage>
      {canWrite && (
        <ul aria-label="Things to try" className="flex flex-wrap justify-end gap-1.5">
          {SUGGESTIONS.map((s) => (
            <li key={s}>
              <button type="button" onClick={() => onPick(s)} className="rounded-full border border-line-strong bg-surface px-3 py-1 text-[12.5px] text-fg-2 transition-colors hover:bg-hover hover:text-fg">
                {s}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
