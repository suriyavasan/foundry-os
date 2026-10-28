import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeMember, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { diffM16, policyToForm, validatePolicyForm } from './policyForm'

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
  updated_at: '2026-10-27T09:00:00Z',
}
const m16 = {
  radar_enabled: true,
  radar_max_fetches_per_day: 2000,
  radar_max_items_per_day: 400,
  radar_max_ideas_per_day: 5,
  radar_max_open_ideas: 25,
  radar_auto_accept: true,
  radar_max_goals_per_week: 1,
  radar_agent_sources: true,
  self_critique_enabled: true,
  radar_contact_email: null,
}

async function openSettings(p: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/policy': p, '/companies/c1/members': page([makeMember({ id: 'm-me', display_name: 'Sam', role: 'owner' })]), ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=settings')
  const dialog = await screen.findByRole('dialog', { name: 'Settings' })
  await within(dialog).findByText('Autonomy mode')
  return { api, dialog, user: userEvent.setup() }
}

describe('Settings -> Policy: market radar group (Milestone 16)', () => {
  it('is hidden when the backend does not report the radar fields', async () => {
    const { dialog } = await openSettings(policy)
    expect(within(dialog).queryByRole('region', { name: 'Market radar' })).not.toBeInTheDocument()
  })

  it('shows the current values', async () => {
    const { dialog } = await openSettings({ ...policy, ...m16, radar_contact_email: 'hello@acme.test' })
    const group = within(dialog).getByRole('region', { name: 'Market radar' })
    expect(within(group).getByLabelText(/Read the outside world every day/)).toBeChecked()
    expect(within(group).getByLabelText(/criticise its own work/)).toBeChecked()
    expect(within(group).getByLabelText(/Fetches per day/)).toHaveValue('2000')
    expect(within(group).getByLabelText(/Stored items per day/)).toHaveValue('400')
    expect(within(group).getByLabelText(/New ideas per day/)).toHaveValue('5')
    expect(within(group).getByLabelText(/Open ideas at most/)).toHaveValue('25')
    expect(within(group).getByLabelText(/Goals from ideas per week/)).toHaveValue('1')
    expect(within(group).getByLabelText(/Contact e-mail/)).toHaveValue('hello@acme.test')
    expect(within(dialog).getByRole('button', { name: 'Save policy' })).toBeDisabled()
  })

  it('PATCHes only what changed, and a blank contact clears it', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m16, radar_contact_email: 'hello@acme.test' }, { 'PATCH /companies/c1/policy': { ...policy, ...m16 } })
    const group = within(dialog).getByRole('region', { name: 'Market radar' })
    await user.click(within(group).getByLabelText(/The AI Co-founder may accept ideas/))
    const fetches = within(group).getByLabelText(/Fetches per day/)
    await user.clear(fetches)
    await user.type(fetches, '500')
    await user.clear(within(group).getByLabelText(/Contact e-mail/))
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({ radar_auto_accept: false, radar_max_fetches_per_day: 500, radar_contact_email: null })
  })

  it('refuses out-of-range numbers and a bad address before sending anything', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m16 })
    const group = within(dialog).getByRole('region', { name: 'Market radar' })
    const goals = within(group).getByLabelText(/Goals from ideas per week/)
    await user.clear(goals)
    await user.type(goals, '50')
    const mail = within(group).getByLabelText(/Contact e-mail/)
    await user.type(mail, 'not an address')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(group).findByText('At most 20')).toBeInTheDocument()
    expect(within(group).getByText(/Enter one plain e-mail address/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
  })

  it('shows the server message when it refuses a value', async () => {
    const { dialog, user } = await openSettings({ ...policy, ...m16 }, {
      'PATCH /companies/c1/policy': () => jsonResponse({ error: { code: 'validation_error', message: 'Invalid', details: [{ loc: ['body', 'radar_max_open_ideas'], msg: 'refused by the server' }] } }, 422),
    })
    const open = within(dialog).getByLabelText(/Open ideas at most/)
    await user.clear(open)
    await user.type(open, '10')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(dialog).findByText('refused by the server')).toBeInTheDocument()
  })
})

describe('policyForm radar helpers', () => {
  it('leaves a backend without radar alone', () => {
    const form = policyToForm(policy as never)
    expect(form.m16).toEqual({})
    expect(diffM16(form.m16, policy as never)).toEqual({})
    expect(validatePolicyForm(form, policy as never)).toEqual({})
  })
  it('round-trips an unchanged form to an empty diff', () => {
    const p = { ...policy, ...m16 } as never
    expect(diffM16(policyToForm(p).m16, p)).toEqual({})
  })
})
