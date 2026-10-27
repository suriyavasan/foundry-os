import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const policy = {
  autonomy_mode: 'autonomous',
  human_approval_required_for: [],
  daily_budget_micros: null,
  monthly_budget_micros: null,
  max_concurrent_runs: 3,
  max_delegation_depth: 3,
  max_tasks_per_planning_cycle: 10,
  max_attempts_per_task: 3,
  max_revisions: 2,
  max_run_seconds: 900,
  max_headcount: 12,
  heartbeat_interval_seconds: 900,
  default_run_reservation_micros: 50_000,
  allowed_model_providers: [],
  updated_at: '2026-10-26T09:00:00Z',
}
const m12 = {
  cli_role_families: null,
  cli_run_reservation_micros: 1_000_000,
  management_reserved_slots: 1,
  max_parallel_runs_per_agent: 1,
  max_work_tasks_per_project: 15,
  max_subtasks_per_task: 5,
  circuit_breaker_cooldown_seconds: 1800,
  initiative_enabled: true,
  initiative_interval_seconds: 21_600,
  project_auto_complete: true,
}

async function openSettings(p: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/policy': p, ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=settings')
  const dialog = await screen.findByRole('dialog', { name: 'Settings' })
  await within(dialog).findByText('Autonomy mode')
  return { api, dialog }
}

describe('Settings → Policy: Milestone 12 fields', () => {
  it('hides the whole group when the backend does not report it', async () => {
    const { dialog } = await openSettings(policy)
    expect(within(dialog).queryByRole('region', { name: 'Full autonomy' })).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/Max work tasks per project/)).not.toBeInTheDocument()
  })

  it('shows the current values, money as USD, and every role family when the list is null', async () => {
    const { dialog } = await openSettings({ ...policy, ...m12 })
    const group = within(dialog).getByRole('region', { name: 'Full autonomy' })
    expect(within(group).getByLabelText(/Reserved per coding CLI run/)).toHaveValue('1.00')
    expect(within(group).getByLabelText('Slots reserved for management')).toHaveValue('1')
    expect(within(group).getByLabelText('Parallel runs per agent')).toHaveValue('1')
    expect(within(group).getByLabelText('Max work tasks per project')).toHaveValue('15')
    expect(within(group).getByLabelText('Max subtasks per task')).toHaveValue('5')
    expect(within(group).getByLabelText(/Circuit breaker cooldown/)).toHaveValue('1800')
    expect(within(group).getByLabelText(/Initiative interval/)).toHaveValue('21600')
    expect(within(group).getByLabelText(/Start new work on its own/)).toBeChecked()
    expect(within(group).getByLabelText(/Complete finished projects automatically/)).toBeChecked()
    expect(within(group).getByLabelText(/Every role family/)).toBeChecked()
    expect(within(group).queryByRole('group', { name: 'Role families' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save policy' })).toBeDisabled()
  })

  it('narrows the role families with a multi-select and PATCHes only the changed fields', async () => {
    const { api, dialog } = await openSettings({ ...policy, ...m12 }, { 'PATCH /companies/c1/policy': { ...policy, ...m12 } })
    const user = userEvent.setup()
    const group = within(dialog).getByRole('region', { name: 'Full autonomy' })
    await user.click(within(group).getByLabelText(/Every role family/))
    const families = within(group).getByRole('group', { name: 'Role families' })
    expect(within(families).getAllByRole('checkbox')).toHaveLength(12)
    for (const name of ['Design', 'Marketing', 'Sales', 'Customer success', 'Research', 'Operations', 'Finance', 'Generic', 'Founder', 'HR']) {
      await user.click(within(families).getByLabelText(name))
    }
    expect(within(families).getByLabelText('Engineering')).toBeChecked()
    expect(within(families).getByLabelText('QA')).toBeChecked()
    expect(within(families).getByLabelText('Design')).not.toBeChecked()
    const reserve = within(group).getByLabelText(/Reserved per coding CLI run/)
    await user.clear(reserve)
    await user.type(reserve, '2.5')
    const tasks = within(group).getByLabelText('Max work tasks per project')
    await user.clear(tasks)
    await user.type(tasks, '40')
    await user.click(within(group).getByLabelText(/Start new work on its own/))
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH')).toBe(true))
    const body = api.calls.find((c) => c.method === 'PATCH')!.body as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(['cli_role_families', 'cli_run_reservation_micros', 'initiative_enabled', 'max_work_tasks_per_project'])
    expect([...(body.cli_role_families as string[])].sort()).toEqual(['engineering', 'qa'])
    expect(body).toMatchObject({ cli_run_reservation_micros: 2_500_000, max_work_tasks_per_project: 40, initiative_enabled: false })
  })

  it('returns to "every role family" by sending null', async () => {
    const { api, dialog } = await openSettings({ ...policy, ...m12, cli_role_families: ['engineering'] }, { 'PATCH /companies/c1/policy': { ...policy, ...m12 } })
    const group = within(dialog).getByRole('region', { name: 'Full autonomy' })
    expect(within(within(group).getByRole('group', { name: 'Role families' })).getByLabelText('Engineering')).toBeChecked()
    await userEvent.click(within(group).getByLabelText(/Every role family/))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ cli_role_families: null }))
  })

  it('blocks out-of-range values before sending, and shows the server message when it refuses', async () => {
    const { api, dialog } = await openSettings({ ...policy, ...m12 }, {
      'PATCH /companies/c1/policy': () => jsonResponse({ error: { code: 'validation_error', message: 'Invalid', details: [{ loc: ['body', 'max_subtasks_per_task'], msg: 'refused by the server' }] } }, 422),
    })
    const user = userEvent.setup()
    const interval = within(dialog).getByLabelText(/Initiative interval/)
    await user.clear(interval)
    await user.type(interval, '60')
    const slots = within(dialog).getByLabelText('Slots reserved for management')
    await user.clear(slots)
    await user.type(slots, '3')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(dialog).findByText('At least 600')).toBeInTheDocument()
    expect(within(dialog).getByText('Must be lower than max concurrent runs')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
    await user.clear(interval)
    await user.type(interval, '600')
    await user.clear(slots)
    await user.type(slots, '2')
    const subtasks = within(dialog).getByLabelText('Max subtasks per task')
    await user.clear(subtasks)
    await user.type(subtasks, '9')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(dialog).findByText('refused by the server')).toBeInTheDocument()
  })
})
