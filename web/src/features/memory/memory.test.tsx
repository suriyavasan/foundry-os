import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeMemory, mockApi, page } from '@/test/factories'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const entries = [
  makeMemory({ id: 'm1', kind: 'requirement', status: 'verified', title: 'No cold calls', body: 'Never phone prospects.', source: { type: 'note', id: 'n1' }, verified_by_type: 'human', verified_at: '2026-10-24T09:00:00Z', created_at: '2026-10-24T08:00:00Z' }),
  makeMemory({ id: 'm2', kind: 'lesson', status: 'proposed', title: 'Retry less', body: 'Five retries burn budget.', source: { type: 'run', id: 'r1', task_id: 't1' }, proposed_by_type: 'agent', proposed_by_id: 'a2', created_at: '2026-10-24T07:00:00Z' }),
  makeMemory({ id: 'm3', kind: 'fact', status: 'rejected', body: 'The moon is cheese.', rejection_reason: 'Not true', created_at: '2026-10-24T06:00:00Z' }),
]
const routes = { ...baseRoutes, '/companies/c1/notes': page([]), '/companies/c1/memory': page(entries) }
const panel = () => screen.findByRole('dialog', { name: 'Memory' })
const rows = (d: HTMLElement) => within(d).queryAllByTestId('memory-row')

describe('memory panel', () => {
  it('lists entries with kind, status and provenance links; proposed entries come first', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=memory')
    const user = userEvent.setup()
    const dialog = await panel()
    await waitFor(() => expect(rows(dialog)).toHaveLength(3))
    expect(rows(dialog)[0]).toHaveAttribute('data-status', 'proposed')
    expect(within(dialog).getByText(/1 proposed entry is waiting for a decision/)).toBeInTheDocument()

    const verified = rows(dialog).find((r) => r.textContent?.includes('No cold calls'))!
    expect(within(verified).getByText('Requirement')).toBeInTheDocument()
    expect(within(verified).getByText('Verified')).toBeInTheDocument()
    await user.click(within(verified).getByRole('button', { name: 'Founder’s note' }))
    await waitFor(() => expect(loc()).toBe('?panel=note:n1'))
  })

  it('links a run source to the task’s run detail', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=memory')
    const dialog = await panel()
    await waitFor(() => expect(rows(dialog)).toHaveLength(3))
    await userEvent.setup().click(within(rows(dialog)[0]!).getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(loc()).toBe('?panel=task:t1&run=r1'))
  })

  it('filters by kind, status and search', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=memory')
    const user = userEvent.setup()
    const dialog = await panel()
    await waitFor(() => expect(rows(dialog)).toHaveLength(3))
    const kinds = within(dialog).getByRole('group', { name: 'Filter by kind' })
    await user.click(within(kinds).getByRole('button', { name: /^Lesson/ }))
    expect(rows(dialog)).toHaveLength(1)
    expect(within(kinds).getByRole('button', { name: /^Lesson/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(kinds).getByRole('button', { name: /^All/ }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by status' }), 'rejected')
    expect(rows(dialog)).toHaveLength(1)
    expect(rows(dialog)[0]).toHaveTextContent('Reason: Not true')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by status' }), 'all')
    await user.type(within(dialog).getByLabelText('Search memory'), 'phone')
    expect(rows(dialog)).toHaveLength(1)
    await user.clear(within(dialog).getByLabelText('Search memory'))
    await user.type(within(dialog).getByLabelText('Search memory'), 'zzz')
    expect(await within(dialog).findByText('No entries match')).toBeInTheDocument()
  })

  it('lets a human verify a proposed entry; verified entries have no decision buttons', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/memory/m2/verify': { ...entries[1], status: 'verified' } })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=memory')
    const user = userEvent.setup()
    const dialog = await panel()
    await waitFor(() => expect(rows(dialog)).toHaveLength(3))
    const verifiedRow = rows(dialog).find((r) => r.textContent?.includes('No cold calls'))!
    expect(within(verifiedRow).queryByRole('button', { name: /Verify|Reject/ })).not.toBeInTheDocument()
    await user.click(within(rows(dialog)[0]!).getByRole('button', { name: 'Verify Retry less' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/memory/m2/verify')).toBe(true))
  })

  it('rejects with an optional reason', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/memory/m2/reject': { ...entries[1], status: 'rejected' } })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=memory')
    const user = userEvent.setup()
    const dialog = await panel()
    await waitFor(() => expect(rows(dialog)).toHaveLength(3))
    await user.click(within(rows(dialog)[0]!).getByRole('button', { name: 'Reject Retry less' }))
    await user.type(within(dialog).getByLabelText('Reason for rejecting Retry less'), 'Too generic')
    await user.click(within(dialog).getByRole('button', { name: 'Confirm reject' }))
    await waitFor(() => expect(api.calls.find((c) => c.path.endsWith('/m2/reject'))?.body).toEqual({ reason: 'Too generic' }))
  })

  it('creates a verified entry from the form, validating first', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/memory': (_u: URL, init?: RequestInit) => jsonResponse(makeMemory({ id: 'new', ...JSON.parse(String(init?.body)) }), 201) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=memory')
    const user = userEvent.setup()
    const dialog = await panel()
    const open = await within(dialog).findByRole('button', { name: 'New entry' })
    await waitFor(() => expect(open).toBeEnabled())
    await user.click(open)
    const form = await screen.findByRole('dialog', { name: 'New memory entry' })
    await user.click(within(form).getByRole('button', { name: 'Save verified entry' }))
    expect(await within(form).findByText('Write what should be remembered.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await user.selectOptions(within(form).getByLabelText('Kind'), 'decision')
    await user.type(within(form).getByLabelText('Title (optional)'), 'Pricing')
    await user.type(within(form).getByLabelText(/^Entry/), 'Use per-seat pricing')
    await user.click(within(form).getByRole('button', { name: 'Save verified entry' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ kind: 'decision', title: 'Pricing', body: 'Use per-seat pricing' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New memory entry' })).not.toBeInTheDocument())
  })

  it('shows empty and not-built-yet states', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/memory': page([]) }).impl)
    const first = renderCanvas('/c/c1?panel=memory')
    expect(await within(await panel()).findByText('Nothing remembered yet')).toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=memory')
    const dialog = await panel()
    expect(await within(dialog).findByText('Company memory is not built yet')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'New entry' })).not.toBeInTheDocument()
  })
})
