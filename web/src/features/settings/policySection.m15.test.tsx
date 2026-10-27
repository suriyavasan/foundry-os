import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeMember, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { diffM15, policyToForm, validatePolicyForm } from './policyForm'

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
const m15 = {
  purchasing_enabled: false,
  purchase_auto_approve_micros: 0,
  purchase_agent_auto_monthly_micros: 0,
  purchase_cofounder_limit_micros: 50_000_000,
  purchase_cofounder_monthly_micros: 200_000_000,
  purchase_monthly_budget_micros: null,
  purchase_recurring_requires_human: true,
  purchase_new_vendor_requires_human: true,
  purchase_tolerance_bps: 1000,
  purchase_tolerance_min_micros: 2_000_000,
  purchase_card_ttl_hours: 72,
  purchase_checkout_member_id: null,
  subscription_renewal_alert_days: 7,
}

async function openSettings(p: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/policy': p, '/companies/c1/members': page([makeMember({ id: 'm-me', display_name: 'Sam', role: 'owner' })]), ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=settings')
  const dialog = await screen.findByRole('dialog', { name: 'Settings' })
  await within(dialog).findByText('Autonomy mode')
  return { api, dialog, user: userEvent.setup() }
}

describe('Settings → Policy: purchasing group (Milestone 15)', () => {
  it('is hidden when the backend does not report purchasing', async () => {
    const { dialog } = await openSettings(policy)
    expect(within(dialog).queryByRole('region', { name: 'Purchasing' })).not.toBeInTheDocument()
  })

  it('shows current values with the code ceilings as helper text', async () => {
    const { dialog } = await openSettings({ ...policy, ...m15 })
    const group = within(dialog).getByRole('region', { name: 'Purchasing' })
    expect(within(group).getByLabelText(/Let agents request purchases/)).not.toBeChecked()
    expect(within(group).getByLabelText(/Approve automatically up to/)).toHaveValue('0.00')
    expect(within(group).getByText(/Ceiling \$100\./)).toBeInTheDocument()
    expect(within(group).getByLabelText(/AI Co-founder may approve up to/)).toHaveValue('50.00')
    expect(within(group).getByText(/Ceiling \$1,000\./)).toBeInTheDocument()
    expect(within(group).getByText(/Ceiling \$500\./)).toBeInTheDocument()
    expect(within(group).getByLabelText(/AI Co-founder total per month/)).toHaveValue('200.00')
    expect(within(group).getByLabelText(/Purchase budget per month/)).toHaveValue('')
    expect(within(group).getByLabelText(/Tax and FX allowance \(%\)/)).toHaveValue('10')
    expect(within(group).getByLabelText(/Unused card closes after/)).toHaveValue('72')
    expect(within(group).getByLabelText(/Renewal review lead time/)).toHaveValue('7')
    expect(within(group).getByLabelText(/Starting a subscription needs me/)).toBeChecked()
    expect(within(group).getByLabelText(/A new vendor needs me/)).toBeChecked()
    expect(within(group).getByLabelText('Who gets checkout tasks')).toHaveValue('')
    expect(within(dialog).getByRole('button', { name: 'Save policy' })).toBeDisabled()
  })

  it('PATCHes only what changed, money as micros and the percentage as basis points', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m15 }, { 'PATCH /companies/c1/policy': { ...policy, ...m15 } })
    const group = within(dialog).getByRole('region', { name: 'Purchasing' })
    await user.click(within(group).getByLabelText(/Let agents request purchases/))
    const auto = within(group).getByLabelText(/Approve automatically up to/)
    await user.clear(auto)
    await user.type(auto, '25')
    const budget = within(group).getByLabelText(/Purchase budget per month/)
    await user.type(budget, '1000.50')
    const tol = within(group).getByLabelText(/Tax and FX allowance \(%\)/)
    await user.clear(tol)
    await user.type(tol, '12.5')
    await user.click(within(group).getByLabelText(/A new vendor needs me/))
    await user.selectOptions(within(group).getByLabelText('Who gets checkout tasks'), 'm-me')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({
      purchasing_enabled: true,
      purchase_auto_approve_micros: 25_000_000,
      purchase_monthly_budget_micros: 1_000_500_000,
      purchase_tolerance_bps: 1250,
      purchase_new_vendor_requires_human: false,
      purchase_checkout_member_id: 'm-me',
    })
  })

  it('refuses values above the code ceilings before sending anything', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m15 })
    const group = within(dialog).getByRole('region', { name: 'Purchasing' })
    const auto = within(group).getByLabelText(/Approve automatically up to/)
    await user.clear(auto)
    await user.type(auto, '150')
    const co = within(group).getByLabelText(/AI Co-founder may approve up to/)
    await user.clear(co)
    await user.type(co, '2000')
    const agent = within(group).getByLabelText(/Automatic total per agent and month/)
    await user.clear(agent)
    await user.type(agent, '600')
    const ttl = within(group).getByLabelText(/Unused card closes after/)
    await user.clear(ttl)
    await user.type(ttl, '0')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(group).findByText('At most $100')).toBeInTheDocument()
    expect(within(group).getByText('At most $1,000')).toBeInTheDocument()
    expect(within(group).getByText('At most $500')).toBeInTheDocument()
    expect(within(group).getByText('At least 1')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
  })

  it('shows the server message when it refuses a value', async () => {
    const { dialog, user } = await openSettings({ ...policy, ...m15 }, {
      'PATCH /companies/c1/policy': () => jsonResponse({ error: { code: 'validation_error', message: 'Invalid', details: [{ loc: ['body', 'purchase_card_ttl_hours'], msg: 'refused by the server' }] } }, 422),
    })
    const ttl = within(dialog).getByLabelText(/Unused card closes after/)
    await user.clear(ttl)
    await user.type(ttl, '48')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(dialog).findByText('refused by the server')).toBeInTheDocument()
  })
})

describe('policyForm purchasing helpers', () => {
  it('round-trips a policy with no change', () => {
    const p = { ...policy, ...m15 } as never
    expect(diffM15(policyToForm(p).m15, p)).toEqual({})
    expect(validatePolicyForm(policyToForm(p), p)).toEqual({})
  })
  it('sends the whole form when there is nothing to compare with (the setup wizard)', () => {
    expect(diffM15({ purchasing_enabled: true, purchase_cofounder_limit_micros: '50' })).toEqual({ purchasing_enabled: true, purchase_cofounder_limit_micros: 50_000_000 })
  })
  it('a blank purchase budget means no cap (null)', () => {
    const p = { ...policy, ...m15, purchase_monthly_budget_micros: 100_000_000 } as never
    const f = policyToForm(p)
    f.m15.purchase_monthly_budget_micros = ''
    expect(diffM15(f.m15, p)).toEqual({ purchase_monthly_budget_micros: null })
  })
  it('has no m15 fields for a policy that does not report them', () => {
    expect(policyToForm(policy as never).m15).toEqual({})
  })
})
