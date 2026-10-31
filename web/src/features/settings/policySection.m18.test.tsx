import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMember, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { diffPolicy, policyToForm, validatePolicyForm } from './policyForm'

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
  updated_at: '2026-10-30T09:00:00Z',
}
const m18 = { approval_timeout_seconds: 86_400, agent_approval_timeout_seconds: 1_800, self_check_enabled: true, work_graph_enabled: true }
const m21 = { meetings_enabled: true, max_meetings_per_agent_per_day: 3, max_meetings_per_company_per_day: 12 }

async function openSettings(p: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/policy': p, '/companies/c1/members': page([makeMember({ id: 'm-me', display_name: 'Sam', role: 'owner' })]), ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=settings')
  const dialog = await screen.findByRole('dialog', { name: 'Settings' })
  await within(dialog).findByText('Autonomy mode')
  return { api, dialog, user: userEvent.setup() }
}

describe('Settings -> Policy: M18 and M21 fields', () => {
  it('hides both groups when the backend does not report the fields', async () => {
    const { dialog } = await openSettings(policy)
    expect(within(dialog).queryByRole('region', { name: 'Approval deadlines and self-check' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('region', { name: 'Agent meetings' })).not.toBeInTheDocument()
  })

  it('shows hours and minutes (not seconds) with plain-language help', async () => {
    const { dialog } = await openSettings({ ...policy, ...m18, ...m21 })
    const g = within(dialog).getByRole('region', { name: 'Approval deadlines and self-check' })
    expect(within(g).getByLabelText(/Your approvals expire after/)).toHaveValue('24')
    expect(within(g).getByLabelText(/Co-founder approvals go to you after/)).toHaveValue('30')
    expect(within(g).getByText(/Unanswered approvals are declined after this time, never approved/)).toBeInTheDocument()
    expect(within(g).getByLabelText(/check their own work/)).toBeChecked()
    expect(within(g).getByLabelText(/Durable work graph/)).toBeChecked()
    const mt = within(dialog).getByRole('region', { name: 'Agent meetings' })
    expect(within(mt).getByLabelText(/Employees may hold meetings/)).toBeChecked()
    expect(within(mt).getByLabelText(/per employee per day/)).toHaveValue('3')
    expect(within(mt).getByLabelText(/per company per day/)).toHaveValue('12')
    expect(within(dialog).getByRole('button', { name: 'Save policy' })).toBeDisabled()
  })

  it('PATCHes only what changed, converting hours and minutes to seconds', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m18, ...m21 }, { 'PATCH /companies/c1/policy': { ...policy, ...m18, ...m21 } })
    const g = within(dialog).getByRole('region', { name: 'Approval deadlines and self-check' })
    const hours = within(g).getByLabelText(/Your approvals expire after/)
    await user.clear(hours)
    await user.type(hours, '5')
    const mins = within(g).getByLabelText(/Co-founder approvals go to you after/)
    await user.clear(mins)
    await user.type(mins, '20')
    await user.click(within(g).getByLabelText(/check their own work/))
    const mt = within(dialog).getByRole('region', { name: 'Agent meetings' })
    await user.click(within(mt).getByLabelText(/Employees may hold meetings/))
    const perAgent = within(mt).getByLabelText(/per employee per day/)
    await user.clear(perAgent)
    await user.type(perAgent, '1')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({
      approval_timeout_seconds: 18_000,
      agent_approval_timeout_seconds: 1_200,
      self_check_enabled: false,
      meetings_enabled: false,
      max_meetings_per_agent_per_day: 1,
    })
  })

  it('refuses values outside what the server allows before sending anything', async () => {
    const { api, dialog, user } = await openSettings({ ...policy, ...m18, ...m21 })
    const g = within(dialog).getByRole('region', { name: 'Approval deadlines and self-check' })
    const hours = within(g).getByLabelText(/Your approvals expire after/)
    await user.clear(hours)
    await user.type(hours, '200')
    const mins = within(g).getByLabelText(/Co-founder approvals go to you after/)
    await user.clear(mins)
    await user.type(mins, '2')
    const mt = within(dialog).getByRole('region', { name: 'Agent meetings' })
    const perCompany = within(mt).getByLabelText(/per company per day/)
    await user.clear(perCompany)
    await user.type(perCompany, '900')
    await user.click(within(dialog).getByRole('button', { name: 'Save policy' }))
    expect(await within(g).findByText('At most 168 hours')).toBeInTheDocument()
    expect(within(g).getByText('At least 5 minutes')).toBeInTheDocument()
    expect(within(mt).getByText('At most 500')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
  })
})

describe('policyForm M18 / M21 helpers', () => {
  it('leaves an older backend alone', () => {
    const form = policyToForm(policy as never)
    expect(form.m18).toEqual({})
    expect(form.m21).toEqual({})
    expect(diffPolicy(form, policy as never)).toEqual({})
    expect(validatePolicyForm(form, policy as never)).toEqual({})
  })
  it('round-trips an unchanged form (including a non-whole hour) to an empty diff', () => {
    const p = { ...policy, ...m18, ...m21, approval_timeout_seconds: 5_400 } as never
    expect(policyToForm(p).m18.approval_timeout_hours).toBe('1.5')
    expect(diffPolicy(policyToForm(p), p)).toEqual({})
  })
})
