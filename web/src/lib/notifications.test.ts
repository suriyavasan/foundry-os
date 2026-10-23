import { describe, expect, it } from 'vitest'
import { makeNotification } from '@/test/factories'
import { effectsForEvent, KNOWN_EVENT_TYPES, parseCompanyEvent } from './streamEvents'
import { qk } from '@/api/keys'
import { markAllRead, markRead, normalizeNotification, normalizeNotifications, notificationKindLabel, panelForNotification, unreadCount, upsertNotification } from './notifications'

const NOW = '2026-10-24T12:00:00Z'

describe('normalizeNotifications', () => {
  it('reads {items, unread_count}, newest first, dropping unreadable rows', () => {
    const a = makeNotification({ id: 'a', created_at: '2026-10-24T08:00:00Z' })
    const b = makeNotification({ id: 'b', created_at: '2026-10-24T10:00:00Z', read_at: NOW })
    const out = normalizeNotifications({ items: [a, b, { nope: true }, null], unread_count: 7 })
    expect(out.items.map((n) => n.id)).toEqual(['b', 'a'])
    expect(out.unread_count).toBe(7)
  })
  it('counts unread itself when the server does not, and accepts a bare array', () => {
    const out = normalizeNotifications([makeNotification({ id: 'a' }), makeNotification({ id: 'b', read_at: NOW })])
    expect(out.unread_count).toBe(1)
  })
  it('is empty for garbage', () => {
    expect(normalizeNotifications(undefined)).toEqual({ items: [], unread_count: 0 })
    expect(normalizeNotification({ id: 'x' })).toBeNull()
  })
})

describe('read / unread reducer', () => {
  const list = normalizeNotifications({ items: [makeNotification({ id: 'a' }), makeNotification({ id: 'b' }), makeNotification({ id: 'c', read_at: '2026-10-24T09:30:00Z' })] })

  it('marks some read and lowers the count by exactly what changed', () => {
    const out = markRead(list, ['a', 'c', 'missing'], NOW)
    expect(out.unread_count).toBe(1)
    expect(out.items.find((n) => n.id === 'a')!.read_at).toBe(NOW)
    expect(out.items.find((n) => n.id === 'c')!.read_at).toBe('2026-10-24T09:30:00Z')
    expect(out.items.find((n) => n.id === 'b')!.read_at).toBeNull()
  })
  it('returns the same object when nothing changes', () => {
    expect(markRead(list, ['c'], NOW)).toBe(list)
    expect(markAllRead({ items: [], unread_count: 0 }, NOW).unread_count).toBe(0)
  })
  it('marks all read, including a server-side backlog beyond this page', () => {
    const out = markAllRead({ ...list, unread_count: 12 }, NOW)
    expect(out.unread_count).toBe(0)
    expect(unreadCount(out.items)).toBe(0)
  })
  it('never goes below zero', () => {
    expect(markRead({ items: list.items, unread_count: 0 }, ['a'], NOW).unread_count).toBe(0)
  })
  it('upserts newest first without double counting', () => {
    const fresh = makeNotification({ id: 'new', created_at: '2026-10-24T11:00:00Z' })
    const added = upsertNotification(list, fresh)
    expect(added.items[0]!.id).toBe('new')
    expect(added.unread_count).toBe(list.unread_count + 1)
    const again = upsertNotification(added, fresh)
    expect(again.items.filter((n) => n.id === 'new')).toHaveLength(1)
    expect(again.unread_count).toBe(added.unread_count)
    expect(upsertNotification(again, { ...fresh, read_at: NOW }).unread_count).toBe(added.unread_count - 1)
  })
})

describe('where a notification leads', () => {
  it('maps the five kinds from the spec', () => {
    expect(panelForNotification({ kind: 'task_assigned', related_type: 'task', related_id: 't1' })).toEqual({ kind: 'task', id: 't1' })
    expect(panelForNotification({ kind: 'review_requested', related_type: 'task', related_id: 't2' })).toEqual({ kind: 'task', id: 't2' })
    expect(panelForNotification({ kind: 'approval_needed', related_type: 'approval', related_id: 'a1' })).toEqual({ kind: 'approvals' })
    expect(panelForNotification({ kind: 'note_replied', related_type: 'note', related_id: 'n1' })).toEqual({ kind: 'note', id: 'n1' })
    expect(panelForNotification({ kind: 'alert', related_type: null, related_id: null })).toEqual({ kind: 'cofounder' })
  })
  it('falls back sensibly without a reference, and has no destination for unknown kinds', () => {
    expect(panelForNotification({ kind: 'task_assigned', related_type: null, related_id: null })).toEqual({ kind: 'my-work' })
    expect(panelForNotification({ kind: 'note_replied', related_type: null, related_id: null })).toEqual({ kind: 'notes' })
    expect(panelForNotification({ kind: 'something_new', related_type: null, related_id: null })).toBeNull()
    expect(panelForNotification({ kind: 'task_assigned', related_type: 'task', related_id: '../../x' })).toEqual({ kind: 'my-work' })
  })
  it('labels kinds and keeps unknown ones readable', () => {
    expect(notificationKindLabel('note_replied')).toBe('AI Co-founder replied')
    expect(notificationKindLabel('billing_notice')).toBe('Billing notice')
  })
})

describe('stream: notification.created', () => {
  it('is a registered event that tells the client to look at its notifications again', () => {
    expect(KNOWN_EVENT_TYPES).toContain('notification.created')
    const ev = parseCompanyEvent('notification.created', JSON.stringify({ id: 'n1' }), '41')!
    expect(effectsForEvent('c1', ev)).toEqual([{ kind: 'invalidate', key: qk.notificationsAll }])
  })
})
