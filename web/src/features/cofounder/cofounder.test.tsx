import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CofounderSummary } from '@/api/types'
import { jsonResponse, makeAlert, makeSummary, mockApi, page } from '@/test/factories'
import { baseRoutes, deferred, loc, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const summary: CofounderSummary = makeSummary({
  goals: [{ id: 'g1', title: 'Launch in Lisbon', status: 'active', priority: 'high' }],
  plan: {
    summary: 'Focus on the Lisbon launch; hire a designer first.',
    updated_at: '2026-10-24T09:00:00Z',
    projects: [
      {
        id: 'p1',
        goal_id: 'g1',
        name: 'Landing page',
        status: 'active',
        lead_agent_id: 'a2',
        tasks_done: 2,
        tasks_total: 5,
        open_tasks: [{ id: 't1', title: 'Write hero copy', status: 'in_progress', assignee_agent_id: 'a2' }],
      },
    ],
  },
  decisions: [{ id: 'd1', company_id: 'c1', made_by_type: 'agent', made_by_id: 'f1', title: 'Start with Lisbon', rationale: 'Lowest cost to test the market.', related_task_id: null, related_project_id: 'p1', created_at: '2026-10-24T08:00:00Z' }],
  blockers: [{ task_id: 't2', title: 'Get API access', reason: 'Waiting for the Founder to connect an account', agent_id: 'a2', since: '2026-10-24T07:00:00Z' }],
  alerts: [makeAlert({ id: 'al1', severity: 'warning', title: 'Budget at 80%', related_type: 'project', related_id: 'p1' })],
  last_run: { run_id: 'r1', task_id: 'tp1', status: 'succeeded', started_at: '2026-10-24T08:30:00Z', finished_at: '2026-10-24T08:40:00Z', summary: 'Reviewed 2 notes' },
  unprocessed_notes: 2,
})

const routes = { ...baseRoutes, '/companies/c1/notes': page([]), '/companies/c1/cofounder/summary': summary }
const panel = () => screen.findByRole('dialog', { name: 'AI Co-founder' })

describe('AI Co-founder panel', () => {
  it('shows plan summary, the plan tree, decisions with rationale, blockers, alerts and the last run', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=cofounder')
    const user = userEvent.setup()
    const dialog = await panel()
    expect(await within(dialog).findByText('Focus on the Lisbon launch; hire a designer first.')).toBeInTheDocument()

    const tree = within(dialog).getByRole('list', { name: 'Active plan' })
    expect(within(tree).getByText('Launch in Lisbon')).toBeInTheDocument()
    expect(within(tree).getByText('Landing page')).toBeInTheDocument()
    expect(within(tree).getByText('2/5 done')).toBeInTheDocument()
    expect(within(tree).getByRole('progressbar', { name: 'Landing page progress' })).toHaveAttribute('aria-valuenow', '2')
    expect(within(tree).getAllByText('Iris').length).toBeGreaterThanOrEqual(2) // project lead and task assignee

    expect(within(dialog).getByText('Start with Lisbon')).toBeInTheDocument()
    expect(within(dialog).getByText('Lowest cost to test the market.')).toBeInTheDocument()
    expect(within(dialog).getByText('Get API access')).toBeInTheDocument()
    expect(within(dialog).getByText('Waiting for the Founder to connect an account')).toBeInTheDocument()
    expect(within(dialog).getByText('Budget at 80%')).toBeInTheDocument()
    expect(within(dialog).getByText('Reviewed 2 notes')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '2 notes waiting to be read' })).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Open run detail' }))
    expect(loc()).toBe('?panel=task:tp1&run=r1')
  })

  it('wake now: posts once, is disabled while in flight and reports what the server did', async () => {
    const gate = deferred<Response>()
    const api = mockApi({ ...routes, 'POST /companies/c1/cofounder/wake': () => gate.promise as unknown as Response })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=cofounder')
    const user = userEvent.setup()
    const dialog = await panel()
    const wake = await within(dialog).findByRole('button', { name: 'Wake now' })
    await waitFor(() => expect(wake).toBeEnabled())
    await user.click(wake)
    await waitFor(() => expect(wake).toBeDisabled())
    await user.click(wake)
    expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(1)

    gate.resolve(jsonResponse({ status: 'queued', task_id: 'tp2' }))
    expect(await within(dialog).findByText(/Wake queued/)).toBeInTheDocument()
    await waitFor(() => expect(wake).toBeEnabled())
  })

  it('reports a coalesced wake honestly', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, 'POST /companies/c1/cofounder/wake': { status: 'coalesced', task_id: 'tp1' } }).impl)
    renderCanvas('/c/c1?panel=cofounder')
    const dialog = await panel()
    const wake = await within(dialog).findByRole('button', { name: 'Wake now' })
    await waitFor(() => expect(wake).toBeEnabled())
    await userEvent.setup().click(wake)
    expect(await within(dialog).findByText(/already waiting, so nothing new was queued/)).toBeInTheDocument()
  })

  it('disables wake while the server says one is already pending', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/cofounder/summary': { ...summary, wake_pending: true } }).impl)
    renderCanvas('/c/c1?panel=cofounder')
    const dialog = await panel()
    expect(await within(dialog).findByRole('button', { name: 'Wake pending' })).toBeDisabled()
  })

  it('shows the server’s refusal when waking fails', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, 'POST /companies/c1/cofounder/wake': () => jsonResponse({ error: { code: 'conflict', message: 'The company is paused', details: null } }, 409) }).impl)
    renderCanvas('/c/c1?panel=cofounder')
    const dialog = await panel()
    const wake = await within(dialog).findByRole('button', { name: 'Wake now' })
    await waitFor(() => expect(wake).toBeEnabled())
    await userEvent.setup().click(wake)
    expect((await within(dialog).findAllByText('The company is paused')).length).toBeGreaterThan(0)
  })

  it('has empty states and a not-built-yet state', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/cofounder/summary': makeSummary() }).impl)
    const first = renderCanvas('/c/c1?panel=cofounder')
    const dialog = await panel()
    expect(await within(dialog).findByText('No goals yet')).toBeInTheDocument()
    expect(within(dialog).getByText('Nothing is blocked')).toBeInTheDocument()
    expect(within(dialog).getByText('No open alerts')).toBeInTheDocument()
    expect(within(dialog).getByText('No decisions recorded yet')).toBeInTheDocument()
    expect(within(dialog).getByText(/has not run yet/)).toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=cofounder')
    const d2 = await panel()
    expect(await within(d2).findByText('Arrives in M5')).toBeInTheDocument()
    expect(within(d2).getByRole('button', { name: 'Wake now' })).toBeDisabled()
  })
})

describe('goals & plan widget with the AI Co-founder', () => {
  const widget = () => screen.findByRole('region', { name: 'Goals & plan' })

  it('shows the AI Co-founder’s plan summary when it has one, and opens its view', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas()
    const user = userEvent.setup()
    const w = await widget()
    const plan = await within(w).findByTestId('cofounder-plan')
    expect(plan).toHaveTextContent('Focus on the Lisbon launch; hire a designer first.')
    expect(plan).toHaveTextContent('AI Co-founder’s plan')
    await user.click(within(plan).getByRole('button', { name: 'Open co-founder view' }))
    expect(loc()).toBe('?panel=cofounder')
  })

  it('keeps the current behaviour (no plan block) when there is no summary or it has no text', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    const first = renderCanvas()
    const w = await widget()
    expect(within(w).queryByTestId('cofounder-plan')).not.toBeInTheDocument()
    expect(within(w).getByText('Ship the thing')).toBeInTheDocument() // strategic goal still shown
    first.unmount()

    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/cofounder/summary': makeSummary() }).impl)
    renderCanvas()
    const w2 = await widget()
    expect(within(w2).queryByTestId('cofounder-plan')).not.toBeInTheDocument()
  })

  it('has a button to the AI Co-founder view even without a plan', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas()
    const w = await widget()
    await userEvent.setup().click(within(w).getByRole('button', { name: 'Open AI Co-founder view' }))
    expect(loc()).toBe('?panel=cofounder')
  })
})
