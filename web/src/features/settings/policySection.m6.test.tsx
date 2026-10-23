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
  max_concurrent_runs: 2,
  max_delegation_depth: 3,
  max_tasks_per_planning_cycle: 10,
  max_attempts_per_task: 3,
  max_revisions: 2,
  max_run_seconds: 900,
  max_headcount: 12,
  heartbeat_interval_seconds: 900,
  default_run_reservation_micros: 50_000,
  allowed_model_providers: [],
  updated_at: '2026-10-24T09:00:00Z',
}
const m6 = {
  max_agent_monthly_budget_micros: 50_000_000,
  hire_default_monthly_budget_micros: null,
  budget_alert_thresholds: [50, 80, 100],
  model_routing: 'free_first',
  allow_paid_fallback: false,
  auto_spend_limit_micros: 0,
  stall_seconds: 180,
}

async function openSettings(p: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/policy': p, ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=settings')
  const dialog = await screen.findByRole('dialog', { name: 'Settings' })
  await within(dialog).findByText('Autonomy mode')
  return { api, dialog }
}

describe('Settings → Policy: Milestone 6 fields', () => {
  it('hides every M6 field when the backend does not report it', async () => {
    const { dialog } = await openSettings(policy)
    expect(within(dialog).queryByRole('region', { name: 'Spend governance and model routing' })).not.toBeInTheDocument()
    for (const label of [/Max monthly budget per agent/, /Default monthly budget for new hires/, /Automatic spend limit/, /Budget alert thresholds/, /Model routing/, /Stall detection/, /paid model as a fallback/]) {
      expect(within(dialog).queryByLabelText(label)).not.toBeInTheDocument()
    }
  })

  it('shows only the fields that are present', async () => {
    const { dialog } = await openSettings({ ...policy, stall_seconds: 120, model_routing: 'pinned' })
    expect(within(dialog).getByLabelText(/Stall detection/)).toHaveValue('120')
    expect(within(dialog).getByLabelText('Model routing')).toHaveValue('pinned')
    expect(within(dialog).queryByLabelText(/Automatic spend limit/)).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/Budget alert thresholds/)).not.toBeInTheDocument()
  })

  it('shows the values with USD inputs', async () => {
    const { dialog } = await openSettings({ ...policy, ...m6 })
    expect(within(dialog).getByLabelText(/Max monthly budget per agent/)).toHaveValue('50.00')
    expect(within(dialog).getByLabelText(/Default monthly budget for new hires/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/Automatic spend limit/)).toHaveValue('0.00')
    expect(within(dialog).getByLabelText(/Budget alert thresholds/)).toHaveValue('50, 80, 100')
    expect(within(dialog).getByLabelText('Model routing')).toHaveValue('free_first')
    expect(within(dialog).getByLabelText(/paid model as a fallback/)).not.toBeChecked()
    expect(within(dialog).getByLabelText(/Stall detection/)).toHaveValue('180')
  })

  it('saves only what changed, with money as exact micros', async () => {
    const saved = { ...policy, ...m6, max_agent_monthly_budget_micros: 19_990_000, model_routing: 'quality_first', allow_paid_fallback: true, auto_spend_limit_micros: 25_000_000 }
    const { api, dialog } = await openSettings({ ...policy, ...m6 }, { 'PATCH /companies/c1/policy': () => jsonResponse(saved) })
    const user = userEvent.setup()
    const cap = within(dialog).getByLabelText(/Max monthly budget per agent/)
    await user.clear(cap)
    await user.type(cap, '19.99')
    const spend = within(dialog).getByLabelText(/Automatic spend limit/)
    await user.clear(spend)
    await user.type(spend, '25')
    await user.selectOptions(within(dialog).getByLabelText('Model routing'), 'quality_first')
    await user.click(within(dialog).getByLabelText(/paid model as a fallback/))
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({
      max_agent_monthly_budget_micros: 19_990_000,
      auto_spend_limit_micros: 25_000_000,
      model_routing: 'quality_first',
      allow_paid_fallback: true,
    })
  })

  it('blocks an invalid threshold list and a bad cap before sending', async () => {
    const { api, dialog } = await openSettings({ ...policy, ...m6 })
    const user = userEvent.setup()
    const th = within(dialog).getByLabelText(/Budget alert thresholds/)
    await user.clear(th)
    await user.type(th, '50, lots')
    const cap = within(dialog).getByLabelText(/Max monthly budget per agent/)
    await user.clear(cap)
    await user.type(cap, 'abc')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(dialog).findByText(/"lots" is not a percentage/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Enter a USD amount/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
  })
})
