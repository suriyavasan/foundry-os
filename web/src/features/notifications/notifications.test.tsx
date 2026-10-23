import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeNotification, mockApi } from '@/test/factories'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const authed = { state: 'authenticated', user: { id: 'u', username: 'sam', display_name: 'Sam', email: null } }
const n1 = makeNotification({ id: 'n1', kind: 'task_assigned', title: 'You were assigned “Write the email”', related_type: 'task', related_id: 't1', created_at: '2026-10-24T10:00:00Z' })
const n2 = makeNotification({ id: 'n2', kind: 'note_replied', title: 'Atlas replied to your note', related_type: 'note', related_id: 'n9', created_at: '2026-10-24T09:00:00Z' })
const n3 = makeNotification({ id: 'n3', kind: 'approval_needed', title: 'Approval needed: domain', related_type: 'approval', related_id: 'a1', read_at: '2026-10-24T09:30:00Z', created_at: '2026-10-24T08:00:00Z' })
const noContent = () => new Response(null, { status: 204 })

const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/auth/session': authed,
  '/notifications': { items: [n1, n2, n3], unread_count: 2 },
  ...over,
})

describe('notification bell', () => {
  it('shows the unread count and lists notifications, unread first by time', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1')
    const bell = await screen.findByRole('button', { name: 'Notifications, 2 unread' })
    expect(within(bell).getByTestId('unread-count')).toHaveTextContent('2')
    await userEvent.click(bell)
    const pop = await screen.findByRole('dialog', { name: 'Notifications' })
    const items = within(pop).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Assigned to you')
    expect(items[1]).toHaveTextContent('AI Co-founder replied')
    expect(items[2]).toHaveTextContent('Approval needed')
    expect(within(items[0]!).getByText('(unread)')).toBeInTheDocument()
    expect(within(items[2]!).queryByText('(unread)')).not.toBeInTheDocument()
  })

  it('opens the destination and marks that notification read', async () => {
    const api = mockApi(routes({ 'POST /notifications/read': noContent }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    await userEvent.click(await screen.findByRole('button', { name: /Notifications/ }))
    const pop = await screen.findByRole('dialog', { name: 'Notifications' })
    await userEvent.click(within(pop).getByRole('button', { name: /Atlas replied to your note/ }))
    await waitFor(() => expect(loc()).toBe('?panel=note:n9'))
    expect(api.calls.find((c) => c.path === '/notifications/read')!.body).toEqual({ ids: ['n2'] })
  })

  it('does not call the server to mark an already-read notification', async () => {
    const api = mockApi(routes({ 'POST /notifications/read': noContent }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    await userEvent.click(await screen.findByRole('button', { name: /Notifications/ }))
    const pop = await screen.findByRole('dialog', { name: 'Notifications' })
    await userEvent.click(within(pop).getByRole('button', { name: /Approval needed: domain/ }))
    await waitFor(() => expect(loc()).toBe('?panel=approvals'))
    expect(api.calls.some((c) => c.path === '/notifications/read')).toBe(false)
  })

  it('marks all read and drops the badge at once', async () => {
    let read = false
    const api = mockApi(
      routes({
        '/notifications': () => jsonResponse(read ? { items: [n1, n2, n3].map((n) => ({ ...n, read_at: '2026-10-24T11:00:00Z' })), unread_count: 0 } : { items: [n1, n2, n3], unread_count: 2 }),
        'POST /notifications/read': () => {
          read = true
          return noContent()
        },
      }),
    )
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }))
    const pop = await screen.findByRole('dialog', { name: 'Notifications' })
    await userEvent.click(within(pop).getByRole('button', { name: 'Mark all read' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/notifications/read')!.body).toEqual({ all: true }))
    expect(await screen.findByRole('button', { name: 'Notifications' })).toBeInTheDocument()
    expect(screen.queryByTestId('unread-count')).not.toBeInTheDocument()
    expect(within(pop).getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  })

  it('has an empty state', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/notifications': { items: [], unread_count: 0 } })).impl)
    renderCanvas('/c/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Notifications' }))
    expect(await screen.findByText('You are all caught up')).toBeInTheDocument()
  })

  it('picks up a new notification when the company stream says one was created', async () => {
    let items = [n2]
    vi.stubGlobal('fetch', mockApi(routes({ '/notifications': () => jsonResponse({ items, unread_count: items.length }) })).impl)
    const sources: Array<{ emit: (type: string, data: unknown) => void }> = []
    class FakeEventSource {
      readyState = 1
      onopen: ((e: Event) => void) | null = null
      onerror: ((e: Event) => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      listeners = new Map<string, (e: MessageEvent) => void>()
      constructor() {
        sources.push({ emit: (type, data) => this.listeners.get(type)?.({ data: JSON.stringify(data), lastEventId: '5' } as MessageEvent) })
        queueMicrotask(() => this.onopen?.(new Event('open')))
      }
      addEventListener(type: string, l: (e: MessageEvent) => void) {
        this.listeners.set(type, l)
      }
      close() {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    renderCanvas('/c/c1')
    expect(await screen.findByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument()
    items = [n1, n2]
    act(() => sources[0]!.emit('notification.created', { id: 5, type: 'notification.created', payload: { id: 'n1' } }))
    expect(await screen.findByRole('button', { name: 'Notifications, 2 unread' }, { timeout: 3000 })).toBeInTheDocument()
  })

  it('is not drawn at all on a backend without accounts or notifications', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1')
    await screen.findByRole('button', { name: /Account:/ })
    expect(screen.queryByRole('button', { name: /^Notifications/ })).not.toBeInTheDocument()
  })

  it('is hidden when only the notifications endpoint is missing', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/auth/session': authed }).impl)
    renderCanvas('/c/c1')
    await screen.findByRole('button', { name: /Account:/ })
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Notifications/ })).not.toBeInTheDocument())
  })
})
