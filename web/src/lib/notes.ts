import type { ClientNote, FounderNote, NoteAction, NoteCategory, NoteReply } from '@/api/types'
import { panelForRef, refTypeLabel } from './refs'
import type { Panel } from './panels'
import type { Tone } from '@/components/ui/Badge'

/* -------------------------------- composing -------------------------------- */

/** The server accepts a note body of at most 10 KB (docs/specs/m5 §2). */
export const NOTE_MAX_BYTES = 10 * 1024

const encoder = new TextEncoder()

/** Size on the wire (UTF-8), which is what the 10 KB limit is measured in. */
export function noteBytes(body: string): number {
  return encoder.encode(body).length
}

export type NoteBodyProblem = 'empty' | 'too_long'

export function validateNoteBody(body: string): { ok: true; body: string } | { ok: false; problem: NoteBodyProblem; message: string } {
  const trimmed = body.trim()
  if (!trimmed) return { ok: false, problem: 'empty', message: 'Write something first. Anything relevant or not is welcome.' }
  if (noteBytes(trimmed) > NOTE_MAX_BYTES) {
    return { ok: false, problem: 'too_long', message: `A note can be at most ${NOTE_MAX_BYTES.toLocaleString()} bytes. Split it into two notes.` }
  }
  return { ok: true, body: trimmed }
}

export type CounterTone = 'normal' | 'warning' | 'danger'
export function counterTone(bytes: number): CounterTone {
  if (bytes > NOTE_MAX_BYTES) return 'danger'
  return bytes >= NOTE_MAX_BYTES * 0.9 ? 'warning' : 'normal'
}

/** ⌘/Ctrl+Enter submits. Plain Enter and Shift+Enter keep writing. */
export function isSubmitShortcut(e: { key: string; metaKey: boolean; ctrlKey: boolean }): boolean {
  return e.key === 'Enter' && (e.metaKey || e.ctrlKey)
}

/**
 * After a failed send the text goes back into the composer so nothing the Founder wrote is lost.
 * If they already started typing something else, the failed text is put in front of it.
 */
export function restoreDraft(current: string, failed: string): string {
  if (!current.trim()) return failed
  return current.includes(failed) ? current : `${failed}\n\n${current}`
}

/* -------------------------------- chips ------------------------------------ */

export const NOTE_CATEGORY_LABEL: Record<NoteCategory, string> = {
  directive: 'Directive',
  goal: 'Goal',
  requirement: 'Requirement',
  idea: 'Idea',
  feedback: 'Feedback',
  question: 'Question',
  context: 'Context',
  noise: 'Noise',
}

export const NOTE_CATEGORY_TONE: Record<NoteCategory, Tone> = {
  directive: 'accent',
  goal: 'info',
  requirement: 'warning',
  idea: 'neutral',
  feedback: 'info',
  question: 'accent',
  context: 'neutral',
  noise: 'neutral',
}

export const NOTE_CATEGORY_HELP: Record<NoteCategory, string> = {
  directive: 'Do this.',
  goal: 'A change of direction.',
  requirement: 'A constraint or spec. Kept as verified memory.',
  idea: 'Worth considering.',
  feedback: 'About work that was done.',
  question: 'The Founder is asking something.',
  context: 'Background information. Kept as verified memory.',
  noise: 'Nothing actionable. Acknowledged and kept.',
}

export const NOTE_CATEGORIES = Object.keys(NOTE_CATEGORY_LABEL) as NoteCategory[]

export interface NoteChip {
  label: string
  tone: Tone
  /** Extra explanation for a tooltip / title. */
  help?: string
}

/** Status chip (New -> Triaged) plus, once triaged, the category chip. Archived notes say so. */
export function noteChips(note: Pick<FounderNote, 'status' | 'triage'> & { pending?: boolean }): { status: NoteChip; category: NoteChip | null } {
  if (note.pending) return { status: { label: 'Sending', tone: 'neutral' }, category: null }
  const category: NoteChip | null = note.triage ? { label: NOTE_CATEGORY_LABEL[note.triage.category] ?? note.triage.category, tone: NOTE_CATEGORY_TONE[note.triage.category] ?? 'neutral', help: NOTE_CATEGORY_HELP[note.triage.category] } : null
  switch (note.status) {
    case 'new':
      return { status: { label: 'New', tone: 'info', help: 'Waiting for the AI Co-founder to triage it.' }, category: null }
    case 'archived':
      return { status: { label: 'Archived', tone: 'neutral' }, category }
    case 'triaged':
    default:
      return { status: { label: 'Triaged', tone: 'success' }, category }
  }
}

/* ------------------------- actions linking to records ----------------------- */

export interface ActionChip {
  key: string
  type: string
  typeLabel: string
  label: string
  panel: Panel | null
}

export type RefTitles = Partial<Record<string, ReadonlyMap<string, string>>>

/**
 * One chip per record the AI Co-founder created or changed. The label is the record's own name when it
 * is known (from the server or a cached list), else the type. `panel` is where the chip links to;
 * `null` means "no dedicated view", which the UI renders as plain text.
 */
export function actionChips(actions: NoteAction[] | null | undefined, titles: RefTitles = {}): ActionChip[] {
  return (actions ?? []).map((a, i) => ({
    key: `${a.type}:${a.ref_id}:${i}`,
    type: a.type,
    typeLabel: refTypeLabel(a.type),
    label: a.label?.trim() || titles[a.type]?.get(a.ref_id) || refTypeLabel(a.type),
    panel: panelForRef(a.type, a.ref_id),
  }))
}

/* -------------------------------- listing ---------------------------------- */

export type NoteStatusFilter = 'active' | 'new' | 'triaged' | 'archived' | 'all'

export interface NoteFilters {
  status: NoteStatusFilter
  category: NoteCategory | 'all'
  pinnedOnly: boolean
  q: string
}

export const DEFAULT_NOTE_FILTERS: NoteFilters = { status: 'active', category: 'all', pinnedOnly: false, q: '' }

/** Pinned first, then newest first. Stable for equal timestamps. */
export function sortNotes<T extends Pick<FounderNote, 'pinned' | 'created_at'>>(notes: T[]): T[] {
  return [...notes].sort((a, b) => Number(b.pinned) - Number(a.pinned) || (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
}

export function filterNotes<T extends FounderNote>(notes: T[], f: NoteFilters): T[] {
  const q = f.q.trim().toLowerCase()
  return sortNotes(
    notes.filter((n) => {
      if (f.status === 'active' ? n.status === 'archived' : f.status !== 'all' && n.status !== f.status) return false
      if (f.category !== 'all' && n.triage?.category !== f.category) return false
      if (f.pinnedOnly && !n.pinned) return false
      if (q) {
        const hay = [n.body, n.triage?.summary ?? '', n.last_reply?.body ?? ''].join('\n').toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    }),
  )
}

/** Plain-text excerpt of a markdown body for list rows. */
export function noteExcerpt(body: string, max = 180): string {
  const text = body
    .replace(/```[\s\S]*?```/g, ' [code] ')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text
}

/* ------------------------------- unread replies ----------------------------- */

/** noteId -> ISO time of the newest AI reply the Founder has seen (kept per browser). */
export type SeenReplies = Readonly<Record<string, string>>

/** An AI Co-founder reply the Founder has not opened yet. Human replies are never "unread". */
export function hasUnreadReply(note: Pick<FounderNote, 'id' | 'last_reply'>, seen: SeenReplies): boolean {
  const r = note.last_reply
  if (!r || r.author_type !== 'agent') return false
  const at = seen[note.id]
  return !at || r.created_at > at
}

export function countUnread(notes: Array<Pick<FounderNote, 'id' | 'last_reply'>>, seen: SeenReplies): number {
  return notes.filter((n) => hasUnreadReply(n, seen)).length
}

/* --------------------------- optimistic cache helpers ----------------------- */

let optimisticCounter = 0

export function optimisticNote(body: string, attachments: string[], companyId: string, now: string = new Date().toISOString()): ClientNote {
  optimisticCounter += 1
  return {
    id: `pending-${optimisticCounter}`,
    company_id: companyId,
    author_user_id: null,
    body,
    attachments,
    status: 'new',
    triage: null,
    triaged_by_run_id: null,
    pinned: false,
    created_at: now,
    updated_at: now,
    pending: true,
  }
}

export const isPendingNote = (n: Pick<ClientNote, 'pending'>) => !!n.pending

/** Put the optimistic note on top. */
export function withOptimistic(list: ClientNote[] | undefined, note: ClientNote): ClientNote[] {
  return [note, ...(list ?? [])]
}

/** The server confirmed: swap the optimistic row for the real one (and never duplicate it if the stream got there first). */
export function confirmOptimistic(list: ClientNote[] | undefined, pendingId: string, real: FounderNote): ClientNote[] {
  const rest = (list ?? []).filter((n) => n.id !== pendingId && n.id !== real.id)
  return [real, ...rest]
}

/** The send failed: the optimistic row disappears and the list is exactly what it was. */
export function rollbackOptimistic(list: ClientNote[] | undefined, pendingId: string): ClientNote[] {
  return (list ?? []).filter((n) => n.id !== pendingId)
}

/** Reflect a reply in the list row (preview + count) without waiting for a refetch. */
export function withReply(list: ClientNote[] | undefined, reply: NoteReply): ClientNote[] | undefined {
  if (!list) return list
  return list.map((n) => (n.id === reply.note_id ? { ...n, last_reply: reply, reply_count: (n.reply_count ?? 0) + 1 } : n))
}

export function patchNote(list: ClientNote[] | undefined, id: string, patch: Partial<FounderNote>): ClientNote[] | undefined {
  if (!list) return list
  return list.map((n) => (n.id === id ? { ...n, ...patch } : n))
}
