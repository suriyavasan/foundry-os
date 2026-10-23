import type { Notification, NotificationKind, NotificationList } from '@/api/types'
import { ID_RE, type Panel } from './panels'
import { panelForRef } from './refs'

/**
 * Pure core of the notification centre: reading the list, the read/unread reducer, and where a notification leads.
 * (docs/specs/m7 §5: assigned to you, review requested, approval needed, the AI Co-founder replied to your note,
 * alerts.) Unknown kinds are shown as they are, never dropped.
 */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

export function normalizeNotification(raw: unknown): Notification | null {
  const o = obj(raw)
  const id = str(o?.id)
  const title = str(o?.title) ?? str(o?.message)
  if (!o || !id || !title) return null
  return {
    id,
    company_id: str(o.company_id),
    kind: str(o.kind) ?? str(o.type) ?? 'notification',
    title,
    body: str(o.body),
    related_type: str(o.related_type),
    related_id: str(o.related_id),
    created_at: str(o.created_at) ?? new Date(0).toISOString(),
    read_at: str(o.read_at),
  }
}

const newestFirst = (a: Notification, b: Notification) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0)

export const unreadCount = (items: Notification[]): number => items.filter((n) => !n.read_at).length

/** Accepts `{items, unread_count?}` or a bare array. The server's `unread_count` wins (the page may be shorter than the backlog). */
export function normalizeNotifications(raw: unknown): NotificationList {
  const o = obj(raw)
  const list = Array.isArray(raw) ? raw : Array.isArray(o?.items) ? (o!.items as unknown[]) : []
  const items = list.map(normalizeNotification).filter((n): n is Notification => n !== null).sort(newestFirst)
  const reported = o?.unread_count
  return { items, unread_count: typeof reported === 'number' && reported >= 0 ? reported : unreadCount(items) }
}

/** Mark the given notifications read. Already-read ones keep their original time; the count never goes negative. */
export function markRead(list: NotificationList, ids: readonly string[], at: string): NotificationList {
  const target = new Set(ids)
  let flipped = 0
  const items = list.items.map((n) => {
    if (!target.has(n.id) || n.read_at) return n
    flipped++
    return { ...n, read_at: at }
  })
  if (flipped === 0) return list
  return { items, unread_count: Math.max(0, list.unread_count - flipped) }
}

export function markAllRead(list: NotificationList, at: string): NotificationList {
  if (list.unread_count === 0 && unreadCount(list.items) === 0) return list
  return { items: list.items.map((n) => (n.read_at ? n : { ...n, read_at: at })), unread_count: 0 }
}

/** Insert or replace by id, newest first (for a notification that arrives with its full record). */
export function upsertNotification(list: NotificationList, n: Notification): NotificationList {
  const existing = list.items.find((x) => x.id === n.id)
  const items = [n, ...list.items.filter((x) => x.id !== n.id)].sort(newestFirst)
  const delta = (n.read_at ? 0 : 1) - (existing ? (existing.read_at ? 0 : 1) : 0)
  return { items, unread_count: Math.max(0, list.unread_count + delta) }
}

export const NOTIFICATION_KIND_LABEL: Record<string, string> = {
  task_assigned: 'Assigned to you',
  review_requested: 'Review requested',
  approval_needed: 'Approval needed',
  note_replied: 'AI Co-founder replied',
  alert: 'Alert',
}

export function notificationKindLabel(kind: NotificationKind): string {
  return NOTIFICATION_KIND_LABEL[kind] ?? kind.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

/** Where clicking a notification goes; null when it has no destination (shown as plain text, not a dead button). */
export function panelForNotification(n: Pick<Notification, 'kind' | 'related_type' | 'related_id'>): Panel | null {
  const direct = panelForRef(n.related_type, n.related_id)
  if (direct) return direct
  switch (n.kind) {
    case 'approval_needed':
      return { kind: 'approvals' }
    case 'task_assigned':
    case 'review_requested':
      return n.related_id && ID_RE.test(n.related_id) ? { kind: 'task', id: n.related_id } : { kind: 'my-work' }
    case 'note_replied':
      return n.related_id && ID_RE.test(n.related_id) ? { kind: 'note', id: n.related_id } : { kind: 'notes' }
    case 'alert':
      return { kind: 'cofounder' }
    default:
      return null
  }
}
