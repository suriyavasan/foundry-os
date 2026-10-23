import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { NoteDetail } from '@/api/types'
import { jsonResponse, makeNote, makeReply, mockApi, page } from '@/test/factories'
import { apiError, baseRoutes, deferred, loc, renderCanvas } from '@/test/m5'
import { resetSeenReplies } from './hooks'

beforeEach(() => {
  localStorage.clear()
  resetSeenReplies()
})
afterEach(() => vi.unstubAllGlobals())

const widget = () => screen.getByRole('region', { name: "Founder's notes" })
/** Render the canvas and wait until the notes widget is on screen (the company loads first). */
async function renderNotes(route?: string) {
  const r = renderCanvas(route)
  await screen.findByRole('region', { name: "Founder's notes" })
  return r
}
const composer = () => screen.getByRole('textbox', { name: 'Write a note to the AI Co-founder' })

const triaged = makeNote({
  id: 'n1',
  body: 'We should **launch** in Lisbon first',
  status: 'triaged',
  created_at: '2026-10-24T09:00:00Z',
  triage: {
    category: 'directive',
    summary: 'Create a Lisbon launch goal',
    confidence: 0.82,
    actions: [
      { type: 'goal', ref_id: 'g1' },
      { type: 'task', ref_id: 't1', label: 'Draft the launch plan' },
      { type: 'schedule', ref_id: 's1' },
    ],
  },
  last_reply: makeReply({ note_id: 'n1', body: 'Created the goal and a weekly review.', created_at: '2026-10-24T09:10:00Z' }),
  reply_count: 1,
})
const fresh = makeNote({ id: 'n2', body: 'Pricing feels low', created_at: '2026-10-24T10:00:00Z' })

describe('Founder’s notes widget: listing', () => {
  const routes = { ...baseRoutes, '/companies/c1/notes': page([triaged, fresh]), '/companies/c1/goals': page([{ id: 'g1', company_id: 'c1', title: 'Launch in Lisbon', description: null, status: 'active', priority: 'high', owner_agent_id: null, target_date: null, created_by_type: 'agent', created_by_id: 'f1', created_at: '2026-10-24T09:05:00Z', updated_at: '2026-10-24T09:05:00Z' }]) }

  it('lists notes newest first with status and category chips', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    await renderNotes()
    const rows = await within(widget()).findAllByTestId('note-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByText('Pricing feels low')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('New')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Triaged')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Directive')).toBeInTheDocument()
  })

  it('previews the AI Co-founder’s reply with its avatar label, and badges it as unread', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    await renderNotes()
    const row = (await within(widget()).findAllByTestId('note-row'))[1]!
    expect(within(row).getByText('Atlas · AI Co-founder')).toBeInTheDocument()
    expect(within(row).getByText('Created the goal and a weekly review.')).toBeInTheDocument()
    expect(within(row).getByLabelText('Unread reply from the AI Co-founder')).toBeInTheDocument()
    expect(within(widget()).getByText(/1 unread reply/)).toBeInTheDocument()
  })

  it('shows created records as chips that open their panels', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    await renderNotes()
    const user = userEvent.setup()
    const chips = await within(widget()).findByRole('list', { name: 'Created from this note' })
    expect(within(chips).getByRole('button', { name: 'Open goal: Launch in Lisbon' })).toBeInTheDocument() // name resolved from the goals list
    await user.click(within(chips).getByRole('button', { name: 'Open task: Draft the launch plan' }))
    expect(loc()).toBe('?panel=task:t1')
    await user.click(within(chips).getByRole('button', { name: 'Open goal: Launch in Lisbon' }))
    expect(loc()).toBe('?panel=goals')
    await user.click(within(chips).getByRole('button', { name: 'Open schedule' }))
    expect(loc()).toBe('?panel=routines')
  })

  it('opens the thread panel from the note text, and reading the reply clears the unread badge', async () => {
    const detail: NoteDetail = { ...triaged, replies: [triaged.last_reply!] }
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/notes/n1': detail }).impl)
    await renderNotes()
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('note-row'))[1]!
    await user.click(within(row).getByRole('button', { name: /Open note: We should launch/ }))
    expect(loc()).toBe('?panel=note:n1')
    await screen.findByRole('dialog', { name: 'Note' })
    await waitFor(() => expect(within(widget()).queryByLabelText('Unread reply from the AI Co-founder')).not.toBeInTheDocument())
    expect(within(widget()).queryByText(/unread repl/)).not.toBeInTheDocument()
  })

  it('pins a note (PATCH) from the list', async () => {
    const api = mockApi({ ...routes, 'PATCH /companies/c1/notes/n2': { ...fresh, pinned: true } })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('note-row'))[0]!
    await user.click(within(row).getByRole('button', { name: 'Pin note' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ pinned: true }))
  })

  it('has an empty state', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': page([]) }).impl)
    await renderNotes()
    expect(await within(widget()).findByText('No notes yet')).toBeInTheDocument()
  })

  it('has an error state with retry (the query retries twice with backoff first)', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': () => apiError(500, 'Database is down') }).impl)
    await renderNotes()
    expect(await within(widget()).findByText('Database is down', {}, { timeout: 6000 })).toBeInTheDocument()
    expect(within(widget()).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  }, 10_000)

  it('says it is not built yet, without a composer, when the endpoint is missing', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    await renderNotes()
    expect(await within(widget()).findByText('Arrives in M5')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Write a note to the AI Co-founder' })).not.toBeInTheDocument()
  })
})

describe('note composer', () => {
  const routes = { ...baseRoutes, '/companies/c1/notes': page([]) }

  it('validates: an empty note is refused with a reason and nothing is sent', async () => {
    const api = mockApi(routes)
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    await user.click(within(widget()).getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/Write something first/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('refuses a note over 10 KB and shows the counter in danger', async () => {
    const api = mockApi(routes)
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    const box = composer()
    // typing 10 241 characters one by one is slow; set the value in one go
    fireEvent.change(box, { target: { value: 'a'.repeat(10 * 1024 + 1) } })
    expect(within(widget()).getByText(/10,241 of 10,240 bytes used/)).toBeInTheDocument()
    await userEvent.setup().click(within(widget()).getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/at most 10,240 bytes/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('sends with Ctrl+Enter, clears the box, and shows the note at once', async () => {
    const real = makeNote({ id: 'n9', body: 'Ship the pricing page', created_at: '2026-10-24T11:00:00Z' })
    const gate = deferred<Response>()
    const api = mockApi({ ...routes, 'POST /companies/c1/notes': () => gate.promise as unknown as Response })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    await user.type(composer(), '  Ship the pricing page ')
    await user.keyboard('{Control>}{Enter}{/Control}')
    // optimistic row while the request is in flight
    const row = await within(widget()).findByTestId('note-row')
    expect(within(row).getByText('Ship the pricing page')).toBeInTheDocument()
    expect(within(row).getByText('Sending')).toBeInTheDocument()
    expect(composer()).toHaveValue('')
    gate.resolve(jsonResponse(real, 201))
    await waitFor(() => expect(within(widget()).queryByText('Sending')).not.toBeInTheDocument())
    expect(within(widget()).getAllByTestId('note-row')).toHaveLength(1)
    expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ body: 'Ship the pricing page' })
  })

  it('also sends with Cmd+Enter', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/notes': makeNote({ id: 'n9', body: 'Hello' }) })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    await user.type(composer(), 'Hello')
    await user.keyboard('{Meta>}{Enter}{/Meta}')
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(1))
  })

  it('plain Enter only adds a line', async () => {
    const api = mockApi(routes)
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    await user.type(composer(), 'one{Enter}two')
    expect(composer()).toHaveValue('one\ntwo')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('rolls the optimistic row back when the server refuses, and puts the text back', async () => {
    const gate = deferred<Response>()
    const api = mockApi({ ...routes, 'POST /companies/c1/notes': () => gate.promise as unknown as Response })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    await user.type(composer(), 'This will fail')
    await user.click(within(widget()).getByRole('button', { name: 'Send' }))
    expect(await within(widget()).findByText('Sending')).toBeInTheDocument()

    gate.resolve(apiError(500, 'Notes are read-only right now'))
    await waitFor(() => expect(within(widget()).queryByTestId('note-row')).not.toBeInTheDocument())
    expect(within(widget()).getByText('No notes yet')).toBeInTheDocument()
    expect(composer()).toHaveValue('This will fail')
    expect(await within(widget()).findByText(/Notes are read-only right now/)).toBeInTheDocument()
  })

  it('documents the keyboard shortcut for assistive tech and in the tooltip', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    await renderNotes()
    await screen.findByRole('textbox', { name: 'Write a note to the AI Co-founder' })
    expect(composer()).toHaveAttribute('aria-keyshortcuts', 'Meta+Enter Control+Enter')
    expect(composer().getAttribute('aria-describedby')).toBeTruthy()
    const user = userEvent.setup()
    await user.hover(within(widget()).getByRole('button', { name: 'Send' }))
    expect((await screen.findAllByText(/Send note \((⌘|Ctrl) \+ Enter\)/)).length).toBeGreaterThan(0)
  })

  it('attaches an existing artifact', async () => {
    const art = { id: 'ar1', company_id: 'c1', title: 'Brand brief', kind: 'document', mime_type: 'text/markdown', status: 'draft', current_version: 1, project_id: null, task_id: null, run_id: null, created_by_type: 'human', created_by_id: null, created_at: 'x', updated_at: 'x' }
    const api = mockApi({ ...routes, '/companies/c1/artifacts': page([art]), 'POST /companies/c1/notes': makeNote({ id: 'n9' }) })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes()
    const user = userEvent.setup()
    const select = await within(widget()).findByRole('combobox', { name: 'Attach an existing artifact' })
    await user.selectOptions(select, 'Brand brief')
    expect(within(widget()).getByRole('list', { name: 'Attached artifacts' })).toHaveTextContent('Brand brief')
    await user.type(composer(), 'See the brief')
    await user.click(within(widget()).getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ body: 'See the brief', attachments: ['ar1'] }))
  })
})

describe('note thread panel', () => {
  const agentReply = makeReply({ id: 'r1', note_id: 'n1', author_type: 'agent', author_id: 'f1', body: 'Created **goal** G1.', created_at: '2026-10-24T09:10:00Z' })
  const base: NoteDetail = { ...triaged, last_reply: agentReply, replies: [agentReply] }

  it('shows the note, its triage with confidence and actions, and the threaded replies', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': page([triaged]), '/companies/c1/notes/n1': base }).impl)
    await renderNotes('/c/c1?panel=note:n1')
    const dialog = await screen.findByRole('dialog', { name: 'Note' })
    expect(await within(dialog).findByText('launch', { selector: 'strong' })).toBeInTheDocument() // rendered markdown
    const triage = within(dialog).getByRole('region', { name: 'Triage' })
    expect(within(triage).getByText('Create a Lisbon launch goal')).toBeInTheDocument()
    expect(within(triage).getByText('Confidence 82%')).toBeInTheDocument()
    expect(within(triage).getByText('Directive')).toBeInTheDocument()
    expect(within(triage).getByRole('button', { name: 'Open task: Draft the launch plan' })).toBeInTheDocument()
    const conversation = within(dialog).getByRole('region', { name: 'Conversation' })
    expect(within(conversation).getByText('Atlas · AI Co-founder')).toBeInTheDocument()
    expect(within(conversation).getByText('goal', { selector: 'strong' })).toBeInTheDocument()
  })

  it('reply flow: validates, posts, shows the human reply as You · Founder and says it re-opens triage', async () => {
    let replies = [agentReply]
    const api = mockApi({
      ...baseRoutes,
      '/companies/c1/notes': page([triaged]),
      '/companies/c1/notes/n1': () => jsonResponse({ ...base, status: replies.length > 1 ? 'new' : 'triaged', replies }),
      'POST /companies/c1/notes/n1/replies': (_u: URL, init?: RequestInit) => {
        const r = makeReply({ id: 'r2', note_id: 'n1', author_type: 'human', author_id: 'u', body: JSON.parse(String(init?.body)).body, created_at: '2026-10-24T09:20:00Z' })
        replies = [...replies, r]
        return jsonResponse(r, 201)
      },
    })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes('/c/c1?panel=note:n1')
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Note' })
    const box = await within(dialog).findByRole('textbox', { name: 'Reply to this note' })
    expect(within(dialog).getByText(/Replying re-opens triage/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Reply' }))
    expect(await within(dialog).findByText(/Write something first/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await user.type(box, 'Actually start with Porto')
    await user.keyboard('{Meta>}{Enter}{/Meta}')
    const conversation = within(dialog).getByRole('region', { name: 'Conversation' })
    expect(await within(conversation).findByText('Actually start with Porto')).toBeInTheDocument()
    expect(within(conversation).getByText('You · Founder')).toBeInTheDocument()
    expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ body: 'Actually start with Porto' })
    expect(box).toHaveValue('')
    // the server re-opened triage: the status chip is back to New
    await waitFor(() => expect(within(dialog).getAllByText('New').length).toBeGreaterThan(0))
  })

  it('keeps the reply text when the server refuses it', async () => {
    const api = mockApi({
      ...baseRoutes,
      '/companies/c1/notes': page([triaged]),
      '/companies/c1/notes/n1': base,
      'POST /companies/c1/notes/n1/replies': () => apiError(500, 'Could not store the reply'),
    })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes('/c/c1?panel=note:n1')
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Note' })
    const box = await within(dialog).findByRole('textbox', { name: 'Reply to this note' })
    await user.type(box, 'Keep me')
    await user.click(within(dialog).getByRole('button', { name: 'Reply' }))
    expect(await within(dialog).findByText(/Could not store the reply/)).toBeInTheDocument()
    expect(box).toHaveValue('Keep me')
  })

  it('pins and archives from the panel header', async () => {
    const api = mockApi({ ...baseRoutes, '/companies/c1/notes': page([triaged]), '/companies/c1/notes/n1': base, 'PATCH /companies/c1/notes/n1': base })
    vi.stubGlobal('fetch', api.impl)
    await renderNotes('/c/c1?panel=note:n1')
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Note' })
    await user.click(await within(dialog).findByRole('button', { name: 'Pin' }))
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'PATCH').at(-1)?.body).toEqual({ pinned: true }))
    await user.click(within(dialog).getByRole('button', { name: 'Archive' }))
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'PATCH').at(-1)?.body).toEqual({ status: 'archived' }))
  })

  it('says so when the note does not exist, and when notes are not built yet', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': page([]) }).impl)
    const first = await renderNotes('/c/c1?panel=note:nope')
    expect(await screen.findByText('Note not found')).toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    await renderNotes('/c/c1?panel=note:nope')
    expect(await screen.findByText("Founder's notes are not built yet")).toBeInTheDocument()
  })
})

describe('notes panel', () => {
  const list = [
    makeNote({ id: 'a', body: 'Pricing idea', status: 'triaged', created_at: '2026-10-24T08:00:00Z', triage: { category: 'idea', summary: 'Consider tiers', actions: [], confidence: 0.5 } }),
    makeNote({ id: 'b', body: 'Hire a designer', status: 'triaged', created_at: '2026-10-24T09:00:00Z', triage: { category: 'directive', summary: 'Hire', actions: [], confidence: 0.9 } }),
    makeNote({ id: 'c', body: 'Old thought', status: 'archived', created_at: '2026-10-24T07:00:00Z', pinned: true }),
    makeNote({ id: 'd', body: 'Brand new', created_at: '2026-10-24T10:00:00Z' }),
  ]

  it('filters by status, category, pinned and search', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/notes': page(list) }).impl)
    await renderNotes('/c/c1?panel=notes')
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: "Founder's notes" })
    const rows = () => within(dialog).queryAllByTestId('note-row').map((r) => r.textContent ?? '')
    await waitFor(() => expect(rows()).toHaveLength(3)) // archived hidden by default
    expect(rows().some((t) => t.includes('Old thought'))).toBe(false)

    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by status' }), 'archived')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toContain('Old thought')

    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by status' }), 'all')
    await user.click(within(dialog).getByRole('button', { name: 'Pinned' }))
    expect(rows()).toHaveLength(1)
    await user.click(within(dialog).getByRole('button', { name: 'Pinned' }))

    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by category' }), 'directive')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toContain('Hire a designer')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by category' }), 'all')

    await user.type(within(dialog).getByLabelText('Search notes'), 'brand')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toContain('Brand new')

    await user.clear(within(dialog).getByLabelText('Search notes'))
    await user.type(within(dialog).getByLabelText('Search notes'), 'zzz')
    expect(await within(dialog).findByText('No notes match')).toBeInTheDocument()
  })

  it('shows the not-built-yet state', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    await renderNotes('/c/c1?panel=notes')
    const dialog = await screen.findByRole('dialog', { name: "Founder's notes" })
    expect(await within(dialog).findByText("Founder's notes are not built yet")).toBeInTheDocument()
  })
})
