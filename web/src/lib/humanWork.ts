import type { HumanCategory, HumanWorkItem, Task } from '@/api/types'
import type { Tone } from '@/components/ui'

/**
 * Pure helpers for M22 human work: things an AI employee asked a person to do. The title and instructions were written by an AI
 * employee, so they are always shown as plain text.
 */

export const HUMAN_CATEGORY_LABEL: Record<HumanCategory, string> = {
  signature: 'Signature',
  call: 'Phone call',
  identity: 'Identity check',
  payment: 'Payment',
  physical: 'Errand',
  account: 'Account step',
  legal: 'Legal',
  other: 'Other',
}

export const categoryLabel = (c: HumanCategory | string | null | undefined): string => (c && c in HUMAN_CATEGORY_LABEL ? HUMAN_CATEGORY_LABEL[c as HumanCategory] : 'Needs a person')

/** Overdue first (oldest due date first), then by due date, then newest. The server sorts the same way; this keeps the list right after a local change. */
export function sortHumanWork(items: readonly HumanWorkItem[]): HumanWorkItem[] {
  const due = (i: HumanWorkItem) => i.due_at ?? '9999'
  return [...items].sort((a, b) => Number(b.overdue) - Number(a.overdue) || due(a).localeCompare(due(b)) || b.created_at.localeCompare(a.created_at))
}

export type HumanWorkView = 'all' | 'mine' | 'pool'

export const HUMAN_WORK_VIEWS: { value: HumanWorkView; label: string }[] = [
  { value: 'all', label: 'All open' },
  { value: 'mine', label: 'Mine' },
  { value: 'pool', label: 'Pool' },
]

/** `mine`: assigned to this person; `pool`: nobody has taken it yet. */
export function filterHumanWork(items: readonly HumanWorkItem[], view: HumanWorkView, memberId: string | null): HumanWorkItem[] {
  if (view === 'mine') return items.filter((i) => !!memberId && i.assignee_ref === `member:${memberId}`)
  if (view === 'pool') return items.filter((i) => i.pool)
  return [...items]
}

/** The people who can take a pooled task: owners, admins and members. Viewers read only. */
export const canTakeHumanWork = (role: string | null | undefined): boolean => role === 'owner' || role === 'admin' || role === 'member'

/** Only owners and admins decline human work (the AI employee is told why). */
export const canDeclineHumanWork = (role: string | null | undefined): boolean => role === 'owner' || role === 'admin'

export interface DueText {
  text: string
  tone: Tone
}

/** "Overdue by 3 h", "Due in 2 days" ... from the due date; nothing when the task has none. */
export function dueText(dueAt: string | null | undefined, now: number = Date.now()): DueText | null {
  if (!dueAt) return null
  const t = new Date(dueAt).getTime()
  if (Number.isNaN(t)) return null
  const diff = t - now
  const abs = Math.abs(diff)
  const hours = Math.round(abs / 3_600_000)
  const span = abs < 3_600_000 ? 'less than an hour' : hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} days`
  return diff < 0 ? { text: `Overdue by ${span}`, tone: 'danger' } : { text: `Due in ${span}`, tone: hours <= 24 ? 'warning' : 'neutral' }
}

/** A task of the task API that is human work (the human fields are present on every task). */
export const isHumanWorkTask = (t: Pick<Task, 'requires_human'>): boolean => t.requires_human === true

/** What a pooled/assigned item shows as its holder. */
export const holderText = (i: Pick<HumanWorkItem, 'pool' | 'assignee_name'>): string => (i.pool ? 'In the pool: anyone can take it' : `Taken by ${i.assignee_name ?? 'someone'}`)
