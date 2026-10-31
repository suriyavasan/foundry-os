import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Intensity } from '@/api/types'
import { describeIntensity } from '@/lib/intensity'
import { jsonResponse, makeMember, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const balanced = {
  heartbeat_interval_seconds: 1800,
  max_concurrent_runs: 2,
  max_planning_runs_per_hour: 6,
  initiative_enabled: true,
  initiative_interval_seconds: 21_600,
  meetings_enabled: true,
  max_meetings_per_company_per_day: 12,
  self_check_enabled: true,
}
const busy = { ...balanced, heartbeat_interval_seconds: 900, max_concurrent_runs: 3, max_planning_runs_per_hour: 10, initiative_interval_seconds: 10_800, max_meetings_per_company_per_day: 20 }

function view(over: Partial<Intensity> = {}): Intensity {
  return { level: 3, name: 'Balanced', custom: false, preset: balanced, effective: balanced, plan_only: false, warning: null, ...over }
}

const WARNING = 'Personal plans assume ordinary individual use; high intensity may hit usage limits - add an API key for heavy autonomy.'
const asRole = (role: string) => ({ ...baseRoutes, '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role }] } })

async function openPill(intensity: Intensity, routes: Record<string, unknown> = {}, role = 'owner') {
  const api = mockApi({ ...asRole(role), '/companies/c1/intensity': intensity, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas()
  const trigger = await screen.findByRole('button', { name: /Company intensity: / })
  const user = userEvent.setup()
  await user.click(trigger)
  const popover = await screen.findByRole('dialog', { name: 'Company intensity' })
  return { api, user, popover, trigger }
}

describe('describeIntensity', () => {
  it('describes only what the server reports, in plain words', () => {
    expect(describeIntensity(balanced)).toEqual([
      'The Co-founder checks in every 30 min.',
      'Up to 2 agents work at once.',
      'Each agent plans at most 6 times an hour.',
      'When it is idle for 6 h, it starts new work on its own.',
      'Up to 12 meetings a day between agents.',
      'Agents double-check their own results before finishing.',
    ])
    expect(describeIntensity({ initiative_enabled: false, meetings_enabled: false, self_check_enabled: false, max_concurrent_runs: 1 })).toEqual([
      'Up to 1 agent works at once.',
      'It does not start new work on its own.',
      'No agent meetings.',
      'Agents do not double-check their own results before finishing.',
    ])
    expect(describeIntensity({})).toEqual([])
  })
})

describe('top bar intensity control', () => {
  it('shows the level name and, in the popover, what it means from the effective values', async () => {
    const { popover, trigger } = await openPill(view({ level: 4, name: 'Busy', effective: busy, preset: busy }))
    expect(trigger).toHaveAccessibleName('Company intensity: Busy')
    expect(within(popover).getByText('The Co-founder checks in every 15 min.')).toBeInTheDocument()
    expect(within(popover).getByText('Up to 3 agents work at once.')).toBeInTheDocument()
    expect(within(popover).getByText(/Budgets still apply/)).toBeInTheDocument()
    expect(within(popover).getByRole('radio', { name: '4, Busy' })).toBeChecked()
    expect(within(popover).getAllByRole('radio')).toHaveLength(5)
  })

  it('describes hand-edited values as they are and shows the Custom badge', async () => {
    const edited = { ...balanced, max_concurrent_runs: 7 }
    const { popover, trigger } = await openPill(view({ custom: true, effective: edited }))
    expect(trigger).toHaveAccessibleName('Company intensity: Balanced (custom)')
    expect(within(popover).getByText('Up to 7 agents work at once.')).toBeInTheDocument()
    expect(within(popover).getByText('Custom')).toBeInTheDocument()
  })

  it('chooses a level straight away when the stack is not plan-only', async () => {
    const { api, user, popover } = await openPill(view(), {
      'PUT /companies/c1/intensity': (_u: URL, init?: RequestInit) => jsonResponse(view({ level: JSON.parse(String(init?.body)).level, name: 'Busy' })),
    })
    await user.click(within(popover).getByRole('radio', { name: '4, Busy' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ level: 4 })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('asks for a confirmation before level 4 or 5 on a plan-only stack, and sends confirm: true', async () => {
    const put = vi.fn((_u: URL, init?: RequestInit) => jsonResponse(view({ level: JSON.parse(String(init?.body)).level, name: 'Continuous', plan_only: true, warning: WARNING })))
    const { api, user, popover } = await openPill(view({ plan_only: true, warning: WARNING }), { 'PUT /companies/c1/intensity': put })
    await user.click(within(popover).getByRole('radio', { name: '5, Continuous' }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch to Continuous?' })
    expect(within(dialog).getByText(WARNING)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false) // nothing is sent before the confirmation
    await user.click(within(dialog).getByRole('button', { name: 'Use Continuous' }))
    await waitFor(() => expect(put).toHaveBeenCalled())
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ level: 5, confirm: true })
  })

  it('cancelling the confirmation sends nothing; lower levels need none even on a plan-only stack', async () => {
    const put = vi.fn(() => jsonResponse(view({ level: 2, name: 'Light', plan_only: true })))
    const { api, user, popover } = await openPill(view({ plan_only: true, warning: WARNING }), { 'PUT /companies/c1/intensity': put })
    await user.click(within(popover).getByRole('radio', { name: '4, Busy' }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch to Busy?' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
    await user.click(within(await screen.findByRole('dialog', { name: 'Company intensity' })).getByRole('radio', { name: '2, Light' }))
    await waitFor(() => expect(put).toHaveBeenCalled())
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ level: 2 })
  })

  it('shows the plan-only warning in the popover at a high level', async () => {
    const { popover } = await openPill(view({ level: 5, name: 'Continuous', plan_only: true, warning: WARNING }))
    expect(within(popover).getByRole('note')).toHaveTextContent(WARNING)
  })

  it('is read-only for viewers: steps are disabled in effect and nothing is sent', async () => {
    const { api, user, popover } = await openPill(view(), {}, 'viewer')
    const step = within(popover).getByRole('radio', { name: '5, Continuous' })
    expect(step).toHaveAttribute('aria-disabled', 'true')
    expect(within(popover).getByText('Only owners and admins can change the intensity.')).toBeInTheDocument()
    await user.click(step)
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('moves between steps with the arrow keys and chooses with Enter', async () => {
    const { api, user, popover } = await openPill(view(), { 'PUT /companies/c1/intensity': view({ level: 4, name: 'Busy' }) })
    const current = within(popover).getByRole('radio', { name: '3, Balanced' })
    expect(current).toHaveAttribute('tabindex', '0')
    current.focus()
    await user.keyboard('{ArrowRight}')
    expect(within(popover).getByRole('radio', { name: '4, Busy' })).toHaveFocus()
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false) // moving focus does not change the company
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ level: 4 }))
  })

  it('hides the control on a backend without the endpoint', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    expect(screen.queryByRole('button', { name: /Company intensity/ })).not.toBeInTheDocument()
  })
})

describe('Settings -> Policy intensity control', () => {
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
    heartbeat_interval_seconds: 1800,
    default_run_reservation_micros: 50_000,
    allowed_model_providers: [],
    updated_at: '2026-10-31T09:00:00Z',
  }

  async function openSettings(intensity: Intensity, role = 'owner') {
    const api = mockApi({
      ...asRole(role),
      '/companies/c1/policy': policy,
      '/companies/c1/intensity': intensity,
      '/companies/c1/members': page([makeMember({ id: 'm-me', display_name: 'Sam', role: 'owner' })]),
    })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=settings')
    const dialog = await screen.findByRole('dialog', { name: 'Settings' })
    await within(dialog).findByText('Autonomy mode')
    return { api, dialog }
  }

  it('shows the control above the individual fields', async () => {
    const { dialog } = await openSettings(view())
    const section = within(dialog).getByRole('region', { name: 'Company intensity' })
    expect(within(section).getByRole('radio', { name: '3, Balanced' })).toBeChecked()
    expect(within(section).queryByText('Custom')).not.toBeInTheDocument()
    const autonomy = within(dialog).getByText('Autonomy mode')
    expect(section.compareDocumentPosition(autonomy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('marks Custom when a field was edited by hand', async () => {
    const { dialog } = await openSettings(view({ custom: true }))
    expect(within(within(dialog).getByRole('region', { name: 'Company intensity' })).getAllByText('Custom').length).toBeGreaterThan(0)
  })

  it('is read-only for a viewer', async () => {
    const { dialog } = await openSettings(view(), 'viewer')
    const section = within(dialog).getByRole('region', { name: 'Company intensity' })
    expect(within(section).getByRole('radio', { name: '1, Conserve' })).toHaveAttribute('aria-disabled', 'true')
  })
})
