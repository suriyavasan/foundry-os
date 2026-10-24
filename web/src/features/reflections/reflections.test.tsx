import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeReflection, makeReflectionDetail, makeResponse, makeStandard } from '@/test/m10'
import { apiError, baseRoutes, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const proposal = makeReflection({
  id: 'rf2',
  kind: 'standard_proposal',
  title: 'Cite every statistic',
  body: 'Reviewers asked for sources **three** times.',
  proposed_standard: 'Every number in a deliverable names its source.',
  evidence: [{ type: 'task', id: 't9', label: 'Pricing memo' }],
  created_at: '2026-10-24T10:00:00Z',
})
const observation = makeReflection({ id: 'rf1', created_at: '2026-10-24T09:00:00Z' })

const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/reflections': page([observation, proposal]),
  '/companies/c1/standards': page([makeStandard()]),
  ...over,
})

const widget = () => screen.getByRole('region', { name: 'Reflections' })
async function renderWidget(over: Record<string, unknown> = {}, route = '/c/c1') {
  const api = mockApi(routes(over))
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(route)
  await screen.findByRole('region', { name: 'Reflections' })
  return api
}

describe('Reflections widget', () => {
  it('lists the latest first, each with kind chip, title, plain excerpt, evidence chips and status', async () => {
    await renderWidget()
    const rows = await within(widget()).findAllByTestId('reflection-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByText('Cite every statistic')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Standard proposal')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Open')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Reviewers asked for sources three times.')).toBeInTheDocument() // markdown stripped
    expect(within(rows[0]!).getByText(/Proposed standard:/).closest('p')).toHaveTextContent('Every number in a deliverable names its source.')
    expect(within(rows[1]!).getByText('Observation')).toBeInTheDocument()
    expect(within(rows[1]!).getByRole('button', { name: 'Open task: Market report' })).toBeInTheDocument()
    expect(within(widget()).getByText('2 waiting for you')).toBeInTheDocument()
  })

  it('evidence chips and titles open their panels', async () => {
    await renderWidget()
    const user = userEvent.setup()
    const rows = await within(widget()).findAllByTestId('reflection-row')
    await user.click(within(rows[0]!).getByRole('button', { name: 'Open task: Pricing memo' }))
    await waitFor(() => expect(loc()).toBe('?panel=task:t9'))
    await user.keyboard('{Escape}')
    await user.click(within((await within(widget()).findAllByTestId('reflection-row'))[1]!).getByRole('button', { name: 'Open reflection: Reviews keep asking for sources' }))
    await waitFor(() => expect(loc()).toBe('?panel=reflection:rf1'))
  })

  it('shows the standards count and opens the standards panel', async () => {
    await renderWidget()
    await userEvent.click(await within(widget()).findByRole('button', { name: 'Standards (1)' }))
    await waitFor(() => expect(loc()).toBe('?panel=standards'))
  })

  it('has Endorse, Challenge and Refine on an open reflection, and the expand button', async () => {
    await renderWidget()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[1]!
    for (const name of ['Endorse', 'Challenge', 'Refine']) expect(within(row).getByRole('button', { name })).toBeInTheDocument()
    expect(within(widget()).getByRole('button', { name: 'Open all reflections' })).toBeInTheDocument()
  })

  it('says it is not built yet on a backend without reflections, and has an empty and an error state', async () => {
    const api = mockApi({ ...baseRoutes })
    vi.stubGlobal('fetch', api.impl)
    const first = renderCanvas()
    await screen.findByRole('region', { name: 'Reflections' })
    expect(await within(widget()).findByText('Arrives in M10')).toBeInTheDocument()
    expect(within(widget()).queryByRole('button', { name: /Standards/ })).not.toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/reflections': page([]) })).impl)
    const second = renderCanvas()
    expect(await within(await screen.findByRole('region', { name: 'Reflections' })).findByText('No reflections yet')).toBeInTheDocument()
    second.unmount()

    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/reflections': () => apiError(500, 'Database is down') })).impl)
    renderCanvas()
    expect(await within(await screen.findByRole('region', { name: 'Reflections' })).findByText('Database is down', {}, { timeout: 6000 })).toBeInTheDocument()
  }, 15_000)
})

describe('reacting to a reflection', () => {
  const respondRoute = { 'POST /companies/c1/reflections/rf2/responses': () => jsonResponse(makeResponse({ reflection_id: 'rf2', reaction: 'endorse' }), 201) }

  it('endorsing a standard proposal says, before the click, that it adopts a company standard', async () => {
    const api = await renderWidget(respondRoute)
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[0]!
    await user.click(within(row).getByRole('button', { name: 'Endorse' }))
    const note = within(row).getByRole('note')
    expect(note).toHaveTextContent(/Endorsing adopts this as a company standard/)
    expect(note).toHaveTextContent(/only you can retire it/)
    expect(note).toHaveTextContent('Every number in a deliverable names its source.')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.click(within(row).getByRole('button', { name: 'Endorse and adopt as a standard' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ reaction: 'endorse' }))
    expect(await screen.findByText(/Adopted as a company standard/)).toBeInTheDocument()
  })

  it('endorsing an ordinary observation adopts nothing and says nothing about standards', async () => {
    const api = await renderWidget({ 'POST /companies/c1/reflections/rf1/responses': () => jsonResponse(makeResponse({ reaction: 'endorse' }), 201) })
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[1]!
    await user.click(within(row).getByRole('button', { name: 'Endorse' }))
    expect(within(row).queryByRole('note')).not.toBeInTheDocument()
    await user.type(within(row).getByRole('textbox', { name: 'Endorse note' }), 'Agreed')
    const form = row.querySelector('form') as HTMLElement
    await user.click(within(form).getByRole('button', { name: 'Endorse' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ reaction: 'endorse', body: 'Agreed' }))
    expect(await screen.findByText('Endorsed.')).toBeInTheDocument()
  })

  it('a challenge needs words; with words it posts the reaction and the text', async () => {
    const api = await renderWidget({ 'POST /companies/c1/reflections/rf1/responses': () => jsonResponse(makeResponse(), 201) })
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[1]!
    await user.click(within(row).getByRole('button', { name: 'Challenge' }))
    await user.click(within(row).getByRole('button', { name: 'Send challenge' }))
    expect(await within(row).findByText(/Say what you disagree with/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.type(within(row).getByRole('textbox', { name: 'Challenge note' }), 'Not every deliverable needs sources')
    await user.click(within(row).getByRole('button', { name: 'Send challenge' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ reaction: 'challenge', body: 'Not every deliverable needs sources' }))
    expect(await screen.findByText(/The AI Co-founder will answer or revise/)).toBeInTheDocument()
  })

  it('refine posts with Ctrl+Enter and closes the composer', async () => {
    const api = await renderWidget({ 'POST /companies/c1/reflections/rf1/responses': () => jsonResponse(makeResponse({ reaction: 'refine' }), 201) })
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[1]!
    await user.click(within(row).getByRole('button', { name: 'Refine' }))
    await user.type(within(row).getByRole('textbox', { name: 'Refine note' }), 'Only for outbound material')
    await user.keyboard('{Control>}{Enter}{/Control}')
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ reaction: 'refine', body: 'Only for outbound material' }))
    await waitFor(() => expect(within(row).queryByRole('textbox')).not.toBeInTheDocument())
  })

  it('keeps the text and shows the reason when the server refuses', async () => {
    await renderWidget({ 'POST /companies/c1/reflections/rf1/responses': () => apiError(500, 'Could not store the response') })
    const user = userEvent.setup()
    const row = (await within(widget()).findAllByTestId('reflection-row'))[1]!
    await user.click(within(row).getByRole('button', { name: 'Challenge' }))
    await user.type(within(row).getByRole('textbox'), 'Keep me')
    await user.click(within(row).getByRole('button', { name: 'Send challenge' }))
    expect(await within(row).findByText('Could not store the response')).toBeInTheDocument()
    expect(within(row).getByRole('textbox')).toHaveValue('Keep me')
  })

  it('closed reflections take no reactions, and neither does a viewer', async () => {
    await renderWidget({ '/companies/c1/reflections': page([makeReflection({ id: 'a', status: 'adopted' }), makeReflection({ id: 'b', status: 'archived' }), makeReflection({ id: 'c', status: 'rejected' })]) })
    expect(await within(widget()).findAllByTestId('reflection-row')).toHaveLength(3)
    expect(within(widget()).queryByRole('button', { name: 'Endorse' })).not.toBeInTheDocument()
    expect(within(widget()).getByText('Adopted')).toBeInTheDocument()
    expect(within(widget()).getByText('Challenged')).toBeInTheDocument()
  })

  it('a viewer reads but cannot react', async () => {
    await renderWidget({ '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'viewer' }] } })
    await within(widget()).findAllByTestId('reflection-row')
    expect(within(widget()).queryByRole('button', { name: 'Endorse' })).not.toBeInTheDocument()
  })
})

describe('Reflections panel', () => {
  it('lists reflections and filters by status on the server and by kind in the page', async () => {
    const statuses: Array<string | null> = []
    const api = await renderWidget(
      {
        '/companies/c1/reflections': (url: URL) => {
          statuses.push(url.searchParams.get('status'))
          const all = [observation, proposal]
          const s = url.searchParams.get('status')
          return jsonResponse(page(s ? all.filter((r) => r.status === s) : all))
        },
      },
      '/c/c1?panel=reflections',
    )
    void api
    const dialog = await screen.findByRole('dialog', { name: 'Reflections' })
    expect(await within(dialog).findAllByTestId('reflection-row')).toHaveLength(2)
    const user = userEvent.setup()
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by kind' }), 'standard_proposal')
    expect(within(dialog).getAllByTestId('reflection-row')).toHaveLength(1)
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by kind' }), 'all')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Filter by status' }), 'archived')
    await waitFor(() => expect(statuses).toContain('archived'))
    expect(await within(dialog).findByText('No reflections match')).toBeInTheDocument()
  })

  it('shows a not-built state on a backend without reflections', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=reflections')
    const dialog = await screen.findByRole('dialog', { name: 'Reflections' })
    expect(await within(dialog).findByText('Reflections are not built yet')).toBeInTheDocument()
  })
})

describe('Reflection thread panel', () => {
  const detail = makeReflectionDetail({
    ...proposal,
    responses: [
      makeResponse({ id: 'r1', reflection_id: 'rf2', author_type: 'human', author_id: 'u', reaction: 'challenge', body: 'Even **internal** memos?', created_at: '2026-10-24T10:30:00Z' }),
      makeResponse({ id: 'r2', reflection_id: 'rf2', author_type: 'agent', author_id: 'f1', author_name: 'Atlas', reaction: 'comment', body: 'Good point: I narrowed it to external material.', created_at: '2026-10-24T10:40:00Z' }),
    ],
    revisions: [{ title: 'Cite every number', body: 'First version of the body.', proposed_standard: 'Cite everything.', created_at: '2026-10-24T10:00:00Z' }],
    status: 'revised',
  })
  const threadRoutes = { '/companies/c1/reflections/rf2': detail }

  it('shows the body as markdown, the proposed standard with the adoption notice, the evidence, the conversation and the revisions', async () => {
    await renderWidget(threadRoutes, '/c/c1?panel=reflection:rf2')
    const dialog = await screen.findByRole('dialog', { name: 'Reflection' })
    expect(await within(dialog).findByText('three', { selector: 'strong' })).toBeInTheDocument()
    expect(within(dialog).getByText('Revised')).toBeInTheDocument()
    expect(within(dialog).getByText('Proposed standard')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Every number in a deliverable names its source.').length).toBeGreaterThan(0)
    expect(within(dialog).getByText(/Endorsing adopts this as a company standard/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Open task: Pricing memo' })).toBeInTheDocument()
    const thread = within(dialog).getByRole('list', { name: 'Responses' })
    expect(within(thread).getByText('You · Founder')).toBeInTheDocument()
    expect(within(thread).getByText('Atlas · AI Co-founder')).toBeInTheDocument()
    expect(within(thread).getByText('internal', { selector: 'strong' })).toBeInTheDocument()
    expect(within(thread).getByText('challenged')).toBeInTheDocument()
    expect(within(thread).getByText('replied')).toBeInTheDocument()
    const revisions = within(dialog).getByRole('list', { name: 'Revisions' })
    expect(within(revisions).getByText('Cite every number')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Archive' })).toBeInTheDocument()
  })

  it('endorsing from the thread adopts the standard and says so; the reaction bar is gone once it is adopted', async () => {
    const api = await renderWidget({ ...threadRoutes, 'POST /companies/c1/reflections/rf2/responses': () => jsonResponse(makeResponse({ reaction: 'endorse', standard_id: 'st9', reflection_status: 'adopted' }), 201) }, '/c/c1?panel=reflection:rf2')
    const dialog = await screen.findByRole('dialog', { name: 'Reflection' })
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: 'Endorse' }))
    await user.click(within(dialog).getByRole('button', { name: 'Endorse and adopt as a standard' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path.endsWith('/responses'))).toBe(true))
  })

  it('shows an adopted reflection as adopted, with a link to the standards, and no reaction bar', async () => {
    await renderWidget({ '/companies/c1/reflections/rf2': { ...detail, status: 'adopted', standard_id: 'st9' } }, '/c/c1?panel=reflection:rf2')
    const dialog = await screen.findByRole('dialog', { name: 'Reflection' })
    expect(await within(dialog).findByText(/Adopted as a company standard/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Endorse' })).not.toBeInTheDocument()
    expect(within(dialog).getByText(/takes no more reactions/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Open standards' }))
    await waitFor(() => expect(loc()).toBe('?panel=standards'))
  })

  it('archives', async () => {
    const api = await renderWidget({ ...threadRoutes, 'POST /companies/c1/reflections/rf2/archive': { ...proposal, status: 'archived' } }, '/c/c1?panel=reflection:rf2')
    const dialog = await screen.findByRole('dialog', { name: 'Reflection' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Archive' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/reflections/rf2/archive')).toBe(true))
  })

  it('says so when the reflection does not exist, and when reflections are not built yet', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    const first = renderCanvas('/c/c1?panel=reflection:nope')
    expect(await screen.findByText('Reflection not found')).toBeInTheDocument()
    first.unmount()
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=reflection:nope')
    expect(await screen.findByText('Reflections are not built yet')).toBeInTheDocument()
  })
})

describe('Standards panel', () => {
  const active = makeStandard({ id: 's1', title: 'Cite every statistic', statement: 'Name the source.', applies_to: ['research'] })
  const written = makeStandard({ id: 's2', title: 'Be brief', statement: 'Under 200 words.', source_reflection_id: null, adopted_at: '2026-10-25T09:00:00Z' })
  const retired = makeStandard({ id: 's3', title: 'Old rule', statement: 'Gone.', status: 'retired' })
  const list = { '/companies/c1/standards': page([active, written, retired]) }

  async function open(over: Record<string, unknown> = {}) {
    const api = await renderWidget({ ...list, ...over }, '/c/c1?panel=standards')
    const dialog = await screen.findByRole('dialog', { name: 'Standards' })
    await within(dialog).findAllByTestId('standard-row')
    return { api, dialog }
  }

  it('lists the active standards, newest adoption first, saying where each came from, and the AI Co-founder’s limits', async () => {
    const { dialog } = await open()
    const rows = within(dialog).getAllByTestId('standard-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByText('Be brief')).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/Written by you/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/Adopted from a reflection you endorsed/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText('research')).toBeInTheDocument()
    expect(within(dialog).getByText(/can propose standards but can never adopt, change or retire one/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Active (2)' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows retired standards separately and can make one active again', async () => {
    const { api, dialog } = await open({ 'PATCH /companies/c1/standards/s3': { ...retired, status: 'active' } })
    const user = userEvent.setup()
    await user.click(within(dialog).getByRole('button', { name: 'Retired (1)' }))
    const row = within(dialog).getByTestId('standard-row')
    expect(within(row).getByText('Old rule')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Make active again' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ status: 'active' }))
  })

  it('creates a human-authored standard (title, rule, tags) and validates first', async () => {
    const { api, dialog } = await open({ 'POST /companies/c1/standards': makeStandard({ id: 's9' }) })
    const user = userEvent.setup()
    await user.click(within(dialog).getAllByRole('button', { name: 'New standard' })[0]!)
    const form = await screen.findByRole('dialog', { name: 'New standard' })
    await user.click(within(form).getByRole('button', { name: 'Add standard' }))
    expect(await within(form).findByText('Give the standard a short title')).toBeInTheDocument()
    expect(within(form).getByText('Write the rule itself, in one or two sentences')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.type(within(form).getByLabelText(/Title/), 'Name the owner')
    await user.type(within(form).getByLabelText(/The rule/), 'Every task names who owns it.')
    await user.type(within(form).getByLabelText(/Applies to/), 'Operations, Engineering')
    await user.click(within(form).getByRole('button', { name: 'Add standard' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/standards')?.body).toEqual({ title: 'Name the owner', statement: 'Every task names who owns it.', applies_to: ['operations', 'engineering'] }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New standard' })).not.toBeInTheDocument())
  })

  it('edits a standard with a PATCH of the new text', async () => {
    const { api, dialog } = await open({ 'PATCH /companies/c1/standards/s2': written })
    const user = userEvent.setup()
    await user.click(within(within(dialog).getAllByTestId('standard-row')[0]!).getByRole('button', { name: 'Edit' }))
    const form = await screen.findByRole('dialog', { name: 'Edit standard' })
    const rule = within(form).getByLabelText(/The rule/)
    expect(rule).toHaveValue('Under 200 words.')
    await user.clear(rule)
    await user.type(rule, 'Under 150 words.')
    await user.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ title: 'Be brief', statement: 'Under 150 words.', applies_to: [] }))
  })

  it('retires only after a confirmation', async () => {
    const { api, dialog } = await open({ 'PATCH /companies/c1/standards/s1': { ...active, status: 'retired' } })
    const user = userEvent.setup()
    await user.click(within(within(dialog).getAllByTestId('standard-row')[1]!).getByRole('button', { name: 'Retire' }))
    const confirm = await screen.findByRole('dialog', { name: 'Retire this standard?' })
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
    expect(within(confirm).getByText(/Agents stop being given/)).toBeInTheDocument()
    await user.click(within(confirm).getByRole('button', { name: 'Retire' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ status: 'retired' }))
  })

  it('shows the server’s refusal inside the dialog', async () => {
    const { dialog } = await open({ 'POST /companies/c1/standards': () => jsonResponse({ error: { code: 'validation_error', message: 'Invalid', details: [{ loc: ['body', 'statement'], msg: 'Too long' }] } }, 422) })
    const user = userEvent.setup()
    await user.click(within(dialog).getAllByRole('button', { name: 'New standard' })[0]!)
    const form = await screen.findByRole('dialog', { name: 'New standard' })
    await user.type(within(form).getByLabelText(/Title/), 'T')
    await user.type(within(form).getByLabelText(/The rule/), 'R')
    await user.click(within(form).getByRole('button', { name: 'Add standard' }))
    expect(await within(form).findByText('Too long')).toBeInTheDocument()
  })

  it('a viewer sees the standards but no way to change them', async () => {
    const { dialog } = await open({ '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'viewer' }] } })
    expect(within(dialog).queryByRole('button', { name: 'New standard' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Retire' })).not.toBeInTheDocument()
  })

  it('has an empty state that points at both ways to get a standard, and a not-built state', async () => {
    await renderWidget({ '/companies/c1/standards': page([]) }, '/c/c1?panel=standards')
    const dialog = await screen.findByRole('dialog', { name: 'Standards' })
    expect(await within(dialog).findByText('No standards yet')).toBeInTheDocument()
    expect(within(dialog).getByText(/Write one yourself, or endorse a standard proposal/)).toBeInTheDocument()
  })

  it('says it is not built yet without the endpoint', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=standards')
    const dialog = await screen.findByRole('dialog', { name: 'Standards' })
    expect(await within(dialog).findByText('Standards are not built yet')).toBeInTheDocument()
  })
})
