import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent, TaskRun } from '@/api/types'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { makeAgent, makeCompany, makeTask, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'

const founder = makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', reports_to_human: true })
const run: TaskRun = {
  id: 'r1', company_id: 'c1', task_id: 't1', agent_id: 'f1', attempt_number: 1, status: 'succeeded', runtime: 'hermes', runtime_execution_id: null,
  queued_at: '2026-10-23T12:00:00Z', started_at: '2026-10-23T12:00:05Z', finished_at: '2026-10-23T12:01:05Z', heartbeat_at: null, cancel_requested: false,
  exit_reason: 'completed', error: null, input_tokens: 1200, output_tokens: 300, cost_micros: 420_000, cost_kind: 'estimated',
}
const events: RunEvent[] = [
  { seq: 1, type: 'progress', payload: { message: 'Reading the brief' }, occurred_at: '2026-10-23T12:00:06Z' },
  { seq: 2, type: 'tool_call', payload: { tool: 'web.search' }, occurred_at: '2026-10-23T12:00:09Z' },
]

function setup(task = makeTask({ id: 't1', company_id: 'c1', title: 'Write brief', status: 'in_progress', assignee_agent_id: 'f1', attempt_count: 1, depends_on: [] }), extra: Record<string, unknown> = {}) {
  const api = mockApi({
    '/companies/c1': makeCompany({ id: 'c1' }),
    '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [] },
    '/companies': page([makeCompany({ id: 'c1' })]),
    '/companies/c1/agents': page([founder]),
    '/companies/c1/activity': page([]),
    '/companies/c1/tasks': page([task]),
    '/companies/c1/tasks/t1': task,
    '/companies/c1/tasks/t1/runs': page([run]),
    '/companies/c1/runs/r1': run,
    '/companies/c1/runs/r1/events': page(events),
    '/companies/c1/artifacts': page([]),
    ...extra,
  })
  vi.stubGlobal('fetch', api.impl)
  return api
}

function renderAt(route: string) {
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

describe('task detail panel', () => {
  it('shows status, brief, runs with cost provenance, and only the actions the state machine allows', async () => {
    setup()
    renderAt('/c/c1?panel=task:t1')
    const dialog = await screen.findByRole('dialog', { name: 'Write brief' })
    expect(await within(dialog).findByRole('button', { name: 'Block' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Cancel task' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(await within(dialog).findByText('≈ $0.42 est.')).toBeInTheDocument()
    expect(within(dialog).getByText('No dependencies.')).toBeInTheDocument()
  })

  it('offers retry (and nothing else destructive) for a failed task and posts to /retry', async () => {
    const failed = makeTask({ id: 't1', company_id: 'c1', title: 'Write brief', status: 'failed', assignee_agent_id: 'f1', error: { code: 'run_failed', message: 'Model timed out', run_id: 'r1' }, depends_on: [] })
    const api = setup(failed, { 'POST /companies/c1/tasks/t1/retry': { ...failed, status: 'ready' } })
    renderAt('/c/c1?panel=task:t1')
    const dialog = await screen.findByRole('dialog', { name: 'Write brief' })
    expect(await within(dialog).findByText(/Model timed out/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/tasks/t1/retry')).toBe(true))
  })

  it('requires a reason to block', async () => {
    const api = setup()
    renderAt('/c/c1?panel=task:t1')
    const dialog = await screen.findByRole('dialog', { name: 'Write brief' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Block' }))
    const confirm = await screen.findByRole('dialog', { name: /Block “Write brief”/ })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Block' }))
    expect(await within(confirm).findByText('A reason is required.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path.endsWith('/block'))).toBe(false)
  })

  it('shows a run with its event log', async () => {
    setup()
    renderAt('/c/c1?panel=task:t1&run=r1')
    const dialog = await screen.findByRole('dialog', { name: /Run · Write brief/ })
    expect(await within(dialog).findByText('Reading the brief')).toBeInTheDocument()
    expect(within(dialog).getByText('web.search')).toBeInTheDocument()
    expect(within(dialog).getByText('Estimated')).toBeInTheDocument()
  })

  it('shows not found for an unknown task', async () => {
    setup(undefined, { '/companies/c1/tasks/t1': undefined })
    renderAt('/c/c1?panel=task:t1')
    expect((await screen.findAllByText('Task not found')).length).toBeGreaterThan(0)
  })
})

describe('run timeline: work graph steps (M18)', () => {
  it('shows graph.node events as compact step lines and other events as before', async () => {
    const graphEvents: RunEvent[] = [
      { seq: 1, type: 'graph.node', payload: { node: 'prepare', outcome: 'ok', summary: 'prepare: ok' }, occurred_at: '2026-10-23T12:00:06Z' },
      { seq: 2, type: 'progress', payload: { message: 'Reading the brief' }, occurred_at: '2026-10-23T12:00:07Z' },
      { seq: 3, type: 'graph.node', payload: { node: 'check', outcome: 'failed/revise', revisions: 1, summary: 'check: failed/revise' }, occurred_at: '2026-10-23T12:00:30Z' },
      { seq: 4, type: 'graph.node', payload: { node: 'check', outcome: 'passed', summary: 'check: passed' }, occurred_at: '2026-10-23T12:00:50Z' },
      { seq: 5, type: 'graph.node', payload: { node: 'wait_founder', outcome: 'waiting', summary: 'wait_founder: waiting' }, occurred_at: '2026-10-23T12:00:55Z' },
    ]
    setup(undefined, { '/companies/c1/runs/r1/events': page(graphEvents) })
    renderAt('/c/c1?panel=task:t1&run=r1')
    const dialog = await screen.findByRole('dialog', { name: /Run · Write brief/ })
    const log = await within(dialog).findByRole('log', { name: 'Run events' })
    const steps = within(log).getAllByTestId('graph-step').map((s) => s.textContent)
    expect(steps).toEqual(['›Prepared the task', '›Revising (1/2)', '›Self-check passed', '›Waiting for your answer'])
    expect(within(log).getByText('Reading the brief')).toBeInTheDocument()
    // graph steps do not carry the raw type chip
    expect(within(log).queryByText('graph.node')).not.toBeInTheDocument()
  })
})
