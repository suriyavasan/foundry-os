import type { FounderQuestion } from '@/api/questions'
import type { CommandExchange, HumanWorkItem } from '@/api/types'
import { exchangeView, isLocalExchange, isOptimisticExchange, normalizeCommandOutput, type ExchangeView } from './commandCenter'
import type { CommandOutput } from '@/api/types'

/**
 * The Command Center as a conversation (docs/specs/m20 §2). The server's feed is newest first; the chat reads oldest first with the
 * newest at the bottom. This turns exchanges and founder questions into one ordered list of things to draw: day separators, an
 * exchange (what the Founder sent and what came back), and a question an agent asked. Pure, so ordering and grouping are tested
 * without a browser.
 */

export type ChatAuthor = 'you' | 'cofounder' | `agent:${string}`

export type ChatEntry =
  | { kind: 'day'; key: string; label: string }
  | {
      kind: 'exchange'
      key: string
      exchange: CommandExchange
      view: ExchangeView
      /** The reply is the first of its group, so it carries the Co-founder's face and name. */
      showAuthor: boolean
      /** The Founder's bubble follows another of the Founder's: less space above it. */
      tight: boolean
    }
  | { kind: 'question'; key: string; question: FounderQuestion; showAuthor: boolean; tight: boolean }
  /** M22: an AI employee asked for a person (a signature, a call ...). Only open requests are drawn. */
  | { kind: 'human_work'; key: string; item: HumanWorkItem; showAuthor: boolean; tight: boolean }

const startOfDay = (d: Date): number => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()

/** "Today", "Yesterday", else a short date ("Tue, Oct 28", with the year when it is not this year). */
export function dayLabel(iso: string, now: number = Date.now()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffDays = Math.round((startOfDay(new Date(now)) - startOfDay(d)) / 86_400_000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  const sameYear = d.getFullYear() === new Date(now).getFullYear()
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

const dayKey = (iso: string): string => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

/** Whether the exchange has anything from the Co-founder to show yet (a waiting one only has a status line under the Founder's bubble). */
export const hasReply = (view: ExchangeView): boolean => view.phase !== 'waiting'

const byTime = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

export interface ChatOptions {
  now?: number
  /** More history exists on the server: resolved questions older than the oldest loaded exchange are left out (their exchange is not here). */
  hasMore?: boolean
  /** Open human-work requests (M22): drawn as a bubble from the employee that asked. */
  humanWork?: readonly HumanWorkItem[]
}

export function buildChat(exchanges: readonly CommandExchange[], questions: readonly FounderQuestion[], opts: ChatOptions = {}): ChatEntry[] {
  const now = opts.now ?? Date.now()
  const sortedEx = [...exchanges].sort((a, b) => byTime(a.created_at, b.created_at))
  const oldest = sortedEx[0]?.created_at ?? null
  const qs = questions.filter((q) => q.status === 'open' || !opts.hasMore || oldest === null || q.created_at >= oldest).sort((a, b) => byTime(a.created_at, b.created_at))

  const hw = (opts.humanWork ?? []).filter((h) => h.status !== 'done' && h.status !== 'cancelled' && h.status !== 'failed').sort((a, b) => byTime(a.created_at, b.created_at))

  type Item = { at: string; ex?: CommandExchange; q?: FounderQuestion; h?: HumanWorkItem }
  const items: Item[] = [...sortedEx.map((ex) => ({ at: ex.created_at, ex })), ...qs.map((q) => ({ at: q.created_at, q })), ...hw.map((h) => ({ at: h.created_at, h }))]
  // Stable by time; on a tie the Founder's message comes before the question it may have caused.
  items.sort((a, b) => byTime(a.at, b.at) || (a.ex ? 0 : 1) - (b.ex ? 0 : 1))

  const out: ChatEntry[] = []
  let lastDay = ''
  let last: ChatAuthor | null = null
  for (const it of items) {
    const dk = dayKey(it.at)
    if (dk !== lastDay) {
      out.push({ kind: 'day', key: `day:${dk}`, label: dayLabel(it.at, now) })
      lastDay = dk
      last = null
    }
    if (it.ex) {
      const view = exchangeView(it.ex, now)
      // A message still on its way has no reply (and no typing indicator) yet: only the quiet "Sending…" line.
      const reply = hasReply(view) && !isOptimisticExchange(it.ex)
      out.push({ kind: 'exchange', key: `ex:${it.ex.id}`, exchange: it.ex, view, tight: last === 'you', showAuthor: reply && last !== 'cofounder' })
      // After a reply the Co-founder spoke last; with no reply yet the Founder did. (A reply always follows the Founder's bubble.)
      last = reply ? 'cofounder' : 'you'
    } else if (it.q) {
      const author: ChatAuthor = `agent:${it.q.agent_id}`
      out.push({ kind: 'question', key: `q:${it.q.id}`, question: it.q, showAuthor: last !== author, tight: last === author })
      last = author
    } else if (it.h) {
      const author: ChatAuthor = it.h.requested_by_agent_id ? `agent:${it.h.requested_by_agent_id}` : 'cofounder'
      out.push({ kind: 'human_work', key: `h:${it.h.id}`, item: it.h, showAuthor: last !== author, tight: last === author })
      last = author
    }
  }
  return out
}

/* --------------------------------- under the Founder's bubble --------------------------------- */

export interface StatusLine {
  tone: 'muted' | 'warning' | 'danger'
  text: string
  /** Label of the retry button, when a retry makes sense. */
  retry?: 'Retry' | 'Retry now'
  /** Read out as an alert. */
  alert?: boolean
}

/**
 * The one quiet line under the Founder's message, only while it is not yet answered: "Sending…", "Waiting for the AI Co-founder",
 * a stale warning, or "Failed · Retry". Answered messages show nothing (the time is in the bubble's tooltip).
 */
export function statusLineOf(ex: CommandExchange, view: ExchangeView): StatusLine | null {
  if (isOptimisticExchange(ex)) return { tone: 'muted', text: 'Sending…' }
  if (view.stale) return { tone: 'warning', text: 'Taking longer than usual. The system will retry on its own', retry: 'Retry now', alert: true }
  switch (ex.status) {
    case 'pending':
      return { tone: 'muted', text: 'Waiting for the AI Co-founder' }
    case 'failed':
      return { tone: 'danger', text: isLocalExchange(ex) ? 'Failed, it did not reach the server' : 'Failed', retry: 'Retry' }
    default:
      return null
  }
}

/** A reply as plain text, for "Copy text": title, text, then table or list rows one per line. */
export function outputPlainText(output: CommandOutput | null | undefined): string {
  const o = normalizeCommandOutput(output)
  if (!o) return ''
  const rows = o.rows.map((r) => r.join(o.kind === 'table' ? '\t' : ' · '))
  return [o.title, o.text, o.columns.length > 0 && o.kind === 'table' ? o.columns.join('\t') : null, ...rows].filter((x): x is string => !!x).join('\n')
}
