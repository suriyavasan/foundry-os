import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeCli, readyAiSetup } from '@/test/m10'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const tested = makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: true, detail: 'OK', tested_at: null } } })
const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/ai-setup': readyAiSetup(),
  '/companies/c1/model-providers': page([]),
  '/companies/c1/model-providers/detect': { reachable: false, models: [] },
  '/cli-runtimes': { items: [tested] },
  ...over,
})

describe('Settings → AI panel (?panel=ai)', () => {
  it('shows the management model and how work gets done, to revisit any time', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const connected = await within(dialog).findByRole('region', { name: 'Connected AI' })
    expect(within(connected).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(connected).getByText('anthropic/claude-sonnet-4.5')).toBeInTheDocument()
    expect(within(dialog).getByRole('radiogroup', { name: 'Work mode' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled() // nothing changed yet
  })

  it('changes to API + coding CLIs, picks the CLI that tested OK and saves', async () => {
    const api = mockApi(routes({ 'PUT /companies/c1/ai-setup': readyAiSetup({ work_mode: 'hybrid', work_runtime: 'cli:claude_code' }) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('radio', { name: /^API \+ coding CLIs/ }))
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: /Use Claude Code for the work/ })).toBeChecked()) // the only CLI that tested OK
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'hybrid', work_runtime: 'cli:claude_code' }))
  })

  it('will not save hybrid without a CLI that tested OK', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/cli-runtimes': { items: [makeCli()] } })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    await userEvent.click(await within(dialog).findByRole('radio', { name: /^API \+ coding CLIs/ }))
    expect(await within(dialog).findByText(/Install a coding CLI and run its test first|Choose which CLI/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('can switch the management model: the picker opens from "Change provider or model"', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Change provider or model' }))
    expect(await within(dialog).findByRole('radiogroup', { name: 'Provider' })).toBeInTheDocument()
  })

  it('is read-only for someone who cannot change it', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    await within(dialog).findByRole('region', { name: 'Connected AI' })
    expect(within(dialog).queryByRole('button', { name: 'Test again' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(within(dialog).getByText(/Only an owner or admin can change how work gets done/)).toBeInTheDocument()
  })

  it('says it is not built yet on a backend without the AI setup', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    expect(await within(dialog).findByText('AI settings are not built yet')).toBeInTheDocument()
    expect(within(dialog).getByText('Arrives in M10')).toBeInTheDocument()
  })

  it('has an error state with retry (the canvas itself still opens: the gate cannot tell)', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/ai-setup': () => jsonResponse({ error: { code: 'internal_error', message: 'Settings are down', details: null } }, 500) })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' }, { timeout: 9000 })
    expect(await within(dialog).findByText('Settings are down', {}, { timeout: 9000 })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  }, 25_000)
})

describe('Settings and the account menu lead to the AI panel', () => {
  it('Settings has an AI card with the current model and mode, and a button that opens the panel', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/policy': { autonomy_mode: 'autonomous', human_approval_required_for: [], daily_budget_micros: null, monthly_budget_micros: null, max_concurrent_runs: 2, max_delegation_depth: 3, max_tasks_per_planning_cycle: 10, max_attempts_per_task: 3, max_revisions: 2, max_run_seconds: 900, max_headcount: 12, heartbeat_interval_seconds: 900, default_run_reservation_micros: 50000, allowed_model_providers: [] } })).impl)
    renderCanvas('/c/c1?panel=settings')
    const dialog = await screen.findByRole('dialog', { name: 'Settings' })
    const card = (await within(dialog).findByText('The model that runs the company, and how work gets done')).closest('section') as HTMLElement
    expect(within(card).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(card).getByText('API only')).toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: 'Open AI settings' }))
    await waitFor(() => expect(loc()).toBe('?panel=ai'))
  })
})
