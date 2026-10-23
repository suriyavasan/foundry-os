import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Approval, Task } from '@/api/types'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { makeAgent, makeCompany, makeTask, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { CanvasPage } from './CanvasPage'

const founder = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
const iris = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Market Researcher', manager_agent_id: 'f1', status: 'paused' })

const base = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder, iris]),
  '/companies/c1/departments': page([]),
  '/companies/c1/activity': page([]),
  '/runtimes': { items: [] },
}

function renderCanvas(route = '/c/c1') {
  return renderWithProviders(
    <Routes>
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route },
  )
}

afterEach(() => vi.unstubAllGlobals())

const widget = (name: string) => screen.getByRole('region', { name })

describe('canvas on an M1 backend (work engine not deployed)', () => {
  it('shows the org tree and honest not-built states, never fake numbers', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    for (const name of ['Goals & plan', 'Work', 'Approvals', 'Spend', 'Artifacts', 'Routines']) {
      await waitFor(() => expect(within(widget(name)).getByText(/Not built yet/)).toBeInTheDocument())
      expect(within(widget(name)).getByText('Arrives in M2')).toBeInTheDocument()
    }
    expect(within(widget('Skills')).getByText('Arrives in M4')).toBeInTheDocument()
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument()
  })

  it('has one top bar and no sidebar navigation', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
    expect(document.querySelector('aside')).toBeNull()
    expect(document.querySelectorAll('header.sticky')).toHaveLength(1)
    expect(screen.getByRole('button', { name: /Pause company/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open command palette' })).toBeInTheDocument()
  })

  it('says live updates are unavailable when there is no event stream', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    expect(screen.getAllByText(/Live updates unavailable/).length).toBeGreaterThan(0)
  })

  it('opens the panel named in the URL and shows a not-built state for tasks', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas('/c/c1?panel=tasks')
    const dialog = await screen.findByRole('dialog', { name: 'Tasks' })
    expect(await within(dialog).findByText('Tasks are not built yet')).toBeInTheDocument()
  })

  it('opens an agent profile from the URL', async () => {
    vi.stubGlobal('fetch', mockApi({ ...base, '/companies/c1/agents/f1': founder }).impl)
    renderCanvas('/c/c1?panel=agent:f1')
    const dialog = await screen.findByRole('dialog', { name: /Atlas/ })
    expect(await within(dialog).findByRole('tab', { name: 'Runs' })).toBeInTheDocument()
  })

  it('opens the command palette with Ctrl+K and runs a command', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    await userEvent.keyboard('{Control>}k{/Control}')
    const input = await screen.findByRole('combobox', { name: 'Search commands' })
    await userEvent.type(input, 'finance')
    expect(screen.getByRole('option', { name: /Open Finance/ })).toBeInTheDocument()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('dialog', { name: 'Finance' })).toBeInTheDocument()
  })

  it('asks for confirmation before pausing and explains the consequences', async () => {
    const api = mockApi({ ...base, 'POST /companies/c1/pause': makeCompany({ id: 'c1', status: 'paused' }) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    await screen.findByText('Atlas')
    await userEvent.click(screen.getByRole('button', { name: /Pause company/ }))
    // Locate the confirmation by its content, whichever dialog role the primitive uses.
    const consequence = await screen.findByText(/cancels work that is running right now/)
    const dialog = consequence.closest<HTMLElement>('[role="dialog"], [role="alertdialog"]')!
    expect(dialog).toBeTruthy()
    expect(api.calls.some((c) => c.path.endsWith('/pause'))).toBe(false)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Pause company' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/pause')).toBe(true))
  })
})

const t = (over: Partial<Task>) => makeTask({ company_id: 'c1', ...over })
const tasks = [
  t({ id: 't1', title: 'Draft positioning', status: 'in_progress', assignee_agent_id: 'f1', project_id: 'p1', priority: 'high' }),
  t({ id: 't2', title: 'Interview customers', status: 'blocked', assignee_agent_id: 'a2', project_id: 'p1' }),
  t({ id: 't3', title: 'Ship landing page', status: 'done', project_id: 'p1' }),
  t({ id: 't4', title: 'Old', status: 'done', project_id: 'p1' }),
]
const approval = (over: Partial<Approval>): Approval => ({
  id: 'ap1', company_id: 'c1', kind: 'external_purchase', status: 'pending', approver: 'human', requested_by_agent_id: 'f1', task_id: null,
  title: 'Buy analytics seat', reason: 'Needed for the dashboard', amount_micros: 25_000_000, vendor: 'Acme Analytics', recurrence: 'monthly', risk: 'medium',
  decided_by_type: null, decided_by_id: null, decided_at: null, decision_note: null, consumed_at: null, created_at: '2026-10-23T12:00:00Z', updated_at: '2026-10-23T12:00:00Z', ...over,
})
const row = (key: string, v: number, e: number) => ({ key, input_tokens: 1000, output_tokens: 500, by_cost_kind: { verified: v, estimated: e, unknown: 0 } })

describe('canvas with the work engine', () => {
  const m2 = {
    ...base,
    '/companies/c1/tasks': page(tasks),
    '/companies/c1/goals': page([{ id: 'g1', company_id: 'c1', title: 'Launch v1', description: null, status: 'active', priority: 'high', owner_agent_id: null, target_date: null, created_by_type: 'human', created_by_id: null, created_at: '2026-10-23T12:00:00Z', updated_at: '2026-10-23T12:00:00Z' }]),
    '/companies/c1/projects': page([{ id: 'p1', company_id: 'c1', goal_id: 'g1', name: 'Landing page', description: null, status: 'active', lead_agent_id: null, budget_micros: null, created_by_type: 'human', created_by_id: null, created_at: '2026-10-23T12:00:00Z', updated_at: '2026-10-23T12:00:00Z' }]),
    '/companies/c1/approvals': page([approval({}), approval({ id: 'ap2', approver: 'founder', title: 'Hire a designer', kind: 'hire', amount_micros: null })]),
    '/companies/c1/artifacts': page([]),
    '/companies/c1/schedules': page([]),
    '/companies/c1/policy': { autonomy_mode: 'autonomous', human_approval_required_for: ['external_purchase'], daily_budget_micros: 10_000_000, monthly_budget_micros: 100_000_000, max_concurrent_runs: 2, max_delegation_depth: 3, max_tasks_per_planning_cycle: 10, max_attempts_per_task: 3, max_revisions: 2, max_run_seconds: 900, max_headcount: 12, heartbeat_interval_seconds: 900, default_run_reservation_micros: 50_000, allowed_model_providers: [] },
    '/companies/c1/usage': () => {
      const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC' }).format(new Date())
      const body: unknown = { items: [row(today, 2_000_000, 750_000)] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    },
  }

  it('shows task counts and the active work with assignees', async () => {
    vi.stubGlobal('fetch', mockApi(m2).impl)
    renderCanvas()
    const work = await screen.findByRole('region', { name: 'Work' })
    await within(work).findByText('Draft positioning')
    expect(within(work).getByRole('button', { name: '1 in progress' })).toBeInTheDocument()
    expect(within(work).getByRole('button', { name: '1 blocked' })).toBeInTheDocument()
    expect(within(work).getByRole('button', { name: '2 done' })).toBeInTheDocument()
    expect(within(work).getByText('Interview customers')).toBeInTheDocument()
    expect(within(work).queryByText('Ship landing page')).not.toBeInTheDocument() // done work is not "active"
  })

  it('shows goal progress as done over total tasks', async () => {
    vi.stubGlobal('fetch', mockApi(m2).impl)
    renderCanvas()
    const goals = await screen.findByRole('region', { name: 'Goals & plan' })
    await within(goals).findByText('Launch v1')
    await within(goals).findByText('Landing page')
    expect(await within(goals).findByText('2/4')).toBeInTheDocument()
  })

  it('splits approvals by approver and only offers actions on the human ones', async () => {
    vi.stubGlobal('fetch', mockApi(m2).impl)
    renderCanvas()
    const box = await screen.findByRole('region', { name: 'Approvals' })
    const human = await within(box).findByRole('region', { name: 'Waiting for you' })
    const founderSection = within(box).getByRole('region', { name: 'Decided by the AI Co-founder' })
    expect(within(human).getByText('Buy analytics seat')).toBeInTheDocument()
    expect(within(human).getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(within(founderSection).getByText('Hire a designer')).toBeInTheDocument()
    expect(within(founderSection).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(within(founderSection).getByText(/AI Co-founder decides this one/)).toBeInTheDocument()
  })

  it('approves with a note through the real endpoint', async () => {
    const api = mockApi({ ...m2, 'POST /companies/c1/approvals/ap1/approve': approval({ status: 'approved' }) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const human = await screen.findByRole('region', { name: 'Waiting for you' })
    await userEvent.click(await within(human).findByRole('button', { name: 'Approve' }))
    const dialog = await screen.findByRole('dialog', { name: /Approve: Buy analytics seat/ })
    await userEvent.type(within(dialog).getByRole('textbox'), 'ok within budget')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/approvals/ap1/approve')?.body).toEqual({ note: 'ok within budget' }))
  })

  it('requires a note to reject', async () => {
    const api = mockApi(m2)
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const human = await screen.findByRole('region', { name: 'Waiting for you' })
    await userEvent.click(await within(human).findByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog', { name: /Reject: Buy analytics seat/ })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
    expect(await within(dialog).findByText(/Say why it is rejected/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.path.endsWith('/reject'))).toBe(false)
  })

  it('keeps verified spend and estimates visually and textually distinct', async () => {
    vi.stubGlobal('fetch', mockApi(m2).impl)
    renderCanvas()
    const spend = await screen.findByRole('region', { name: 'Spend' })
    await within(spend).findAllByText('Verified')
    expect(within(spend).getAllByText('$2.00').length).toBeGreaterThan(0) // verified, presented as a charge
    expect(within(spend).getAllByText('≈ $0.75 est.').length).toBeGreaterThan(0) // estimate, never plain
    expect(within(spend).queryByText('$0.75')).not.toBeInTheDocument()
    expect(within(spend).getAllByText(/Estimates are not charges/).length).toBeGreaterThan(0)
    expect(within(spend).getAllByText('Limit $10.00').length).toBe(1)
  })
})
