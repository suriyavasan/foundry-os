import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeMember, makeTask, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const me = makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'owner' })
const mine = makeTask({ id: 't1', title: 'Write the launch email', assignee_ref: 'member:m-me', assignee_agent_id: null, status: 'in_progress', priority: 'high', due_at: '2026-10-30T09:00:00Z' })
const review = makeTask({ id: 't2', title: 'Review pricing page', kind: 'review', assignee_ref: 'member:m-me', assignee_agent_id: null, status: 'in_review' })
const others = makeTask({ id: 't3', title: 'Not mine', assignee_ref: 'member:m-other', assignee_agent_id: null })
const approval = { id: 'ap1', company_id: 'c1', kind: 'spend', status: 'pending', approver: 'human', requested_by_agent_id: null, task_id: null, title: 'Buy the domain', reason: 'Needed for launch', amount_micros: 12_000_000, vendor: 'Registrar', recurrence: 'yearly', risk: 'medium', decided_by_type: null, decided_by_id: null, decided_at: null, decision_note: null, consumed_at: null, created_at: '2026-10-24T09:00:00Z', updated_at: '2026-10-24T09:00:00Z' }

const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/members': page([me]),
  '/companies/c1/tasks': page([mine, review, others]),
  '/companies/c1/approvals': page([approval, { ...approval, id: 'ap2', title: 'Scoped to someone else', approver_ref: 'member:m-other' }]),
  ...over,
})
const open = () => screen.findByRole('dialog', { name: 'My work' })

/** mockApi parses bodies as JSON; multipart bodies are intercepted here. */
function withUploads(api: ReturnType<typeof mockApi>) {
  const uploads: FormData[] = []
  const impl = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.body instanceof FormData) {
      uploads.push(init.body)
      api.calls.push({ method: init.method ?? 'GET', path: new URL(String(input), 'http://x').pathname.replace(/^\/api/, ''), body: '[multipart]' })
      return jsonResponse({ id: 'art1' }, 201)
    }
    return api.impl(input, init)
  }
  return { impl, uploads }
}

describe('my work panel', () => {
  it('shows tasks, reviews and approvals that are mine, and nothing else', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    const assigned = await within(d).findByRole('region', { name: 'Assigned to me' })
    expect(within(assigned).getByText('Write the launch email')).toBeInTheDocument()
    const reviews = within(d).getByRole('region', { name: 'Reviews requested' })
    expect(within(reviews).getByText('Review pricing page')).toBeInTheDocument()
    expect(within(d).queryByText('Not mine')).not.toBeInTheDocument()
    expect(within(d).getByText('Buy the domain')).toBeInTheDocument()
    expect(within(d).queryByText('Scoped to someone else')).not.toBeInTheDocument()
  })

  it('opens the task when its title is clicked', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Write the launch email' }))
    expect(await screen.findByRole('dialog', { name: /Write the launch email|Task/ })).toBeInTheDocument()
  })

  it('completes a task with a comment and an uploaded artifact', async () => {
    const base = mockApi(routes({ 'POST /companies/c1/tasks/t1/complete': () => jsonResponse({ ...mine, status: 'done' }) }))
    const up = withUploads(base)
    vi.stubGlobal('fetch', up.impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Complete Write the launch email' }))
    const dialog = await screen.findByRole('dialog', { name: 'Complete task' })
    await userEvent.type(within(dialog).getByLabelText(/Comment/), '  Sent to the list  ')
    const file = new File(['hello'], 'launch.md', { type: 'text/markdown' })
    await userEvent.upload(within(dialog).getByLabelText(/Attach a file/), file)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark complete' }))

    await waitFor(() => expect(base.calls.some((c) => c.path === '/companies/c1/tasks/t1/complete')).toBe(true))
    expect(up.uploads).toHaveLength(1)
    expect(up.uploads[0]!.get('task_id')).toBe('t1')
    expect(up.uploads[0]!.get('title')).toBe('launch.md')
    expect((up.uploads[0]!.get('file') as File).name).toBe('launch.md')
    const order = base.calls.filter((c) => c.method === 'POST').map((c) => c.path)
    expect(order).toEqual(['/companies/c1/artifacts', '/companies/c1/tasks/t1/complete'])
    expect(base.calls.find((c) => c.path.endsWith('/complete'))!.body).toEqual({ comment: 'Sent to the list', artifact_ids: ['art1'] })
  })

  it('completes without a file or comment', async () => {
    const api = mockApi(routes({ 'POST /companies/c1/tasks/t2/complete': () => jsonResponse({ ...review, status: 'done' }) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Complete Review pricing page' }))
    const dialog = await screen.findByRole('dialog', { name: 'Complete task' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark complete' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/tasks/t2/complete')!.body).toEqual({}))
    expect(api.calls.some((c) => c.method === 'POST' && c.path.endsWith('/artifacts'))).toBe(false)
  })

  it('refuses a file over 25 MB before uploading anything', async () => {
    const api = mockApi(routes())
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Complete Write the launch email' }))
    const dialog = await screen.findByRole('dialog', { name: 'Complete task' })
    const big = new File(['x'], 'big.bin')
    Object.defineProperty(big, 'size', { value: 26 * 1024 * 1024 })
    await userEvent.upload(within(dialog).getByLabelText(/Attach a file/), big)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark complete' }))
    expect(await within(dialog).findByText('Files can be at most 25 MB')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('blocking needs a reason, and sends it', async () => {
    const api = mockApi(routes({ 'POST /companies/c1/tasks/t1/block': () => jsonResponse({ ...mine, status: 'blocked' }) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Block Write the launch email' }))
    const dialog = await screen.findByRole('dialog', { name: 'Block task' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Block task' }))
    expect(await within(dialog).findByText(/Say why it is blocked/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await userEvent.type(within(dialog).getByLabelText(/Reason/), 'Waiting for copy')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Block task' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/tasks/t1/block')!.body).toEqual({ reason: 'Waiting for copy' }))
  })

  it('says plainly when the backend cannot complete tasks for people yet', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Complete Write the launch email' }))
    const dialog = await screen.findByRole('dialog', { name: 'Complete task' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark complete' }))
    expect(await within(dialog).findByText(/cannot complete tasks for people yet/)).toBeInTheDocument()
  })

  it('has an empty state', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/tasks': page([others]), '/companies/c1/approvals': page([]) })).impl)
    renderCanvas('/c/c1?panel=my-work')
    expect(await within(await open()).findByText('Nothing waiting on you')).toBeInTheDocument()
  })

  it('degrades on a backend without accounts', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/tasks': page([]) }).impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await open()
    expect(await within(d).findByText('Team accounts are not built yet')).toBeInTheDocument()
    expect(within(d).getByText(/Arrives in M7/)).toBeInTheDocument()
  })
})
