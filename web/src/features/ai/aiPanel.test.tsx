import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeCli, makeProviderTest, readyAiSetup } from '@/test/m10'
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
  it('shows the model API (required) and the coding CLIs (optional) in one form, to revisit any time', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const connected = await within(dialog).findByRole('region', { name: 'Connected AI' })
    expect(within(connected).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(connected).getByText('anthropic/claude-sonnet-4.5')).toBeInTheDocument()
    expect(within(within(dialog).getByRole('region', { name: 'Model API' })).getByText('Required')).toBeInTheDocument()
    expect(within(within(dialog).getByRole('region', { name: 'Coding CLIs' })).getByText('Optional')).toBeInTheDocument()
    expect(await within(dialog).findByRole('radiogroup', { name: 'Who does the hands-on work' })).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: /Model API only/ })).toBeChecked()
    expect(within(dialog).getByRole('button', { name: 'All providers' })).toBeInTheDocument()
    expect(within(dialog).getAllByRole('button', { name: 'Save' })).toHaveLength(1)
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled() // nothing changed yet
  })

  it('picks the CLI that tested OK and saves once', async () => {
    const api = mockApi(routes({ 'PUT /companies/c1/ai-setup': readyAiSetup({ work_mode: 'hybrid', work_runtime: 'cli:claude_code' }) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const user = userEvent.setup()
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await user.click(await within(dialog).findByRole('radio', { name: /Use Claude Code for hands-on work/ }))
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'PUT')).toHaveLength(1))
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'hybrid', work_runtime: 'cli:claude_code' })
    expect(await screen.findByText('Saved your AI setup')).toBeInTheDocument()
  })

  it('will not save a CLI that has not tested OK: its radio is disabled and nothing changes', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/cli-runtimes': { items: [makeCli()] } })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    expect(await within(dialog).findByRole('radio', { name: /Use Claude Code for hands-on work/ })).toBeDisabled()
    expect(within(dialog).getByText(/test it first/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('shows a saved CLI as chosen and can go back to Model API only', async () => {
    const api = mockApi(routes({ '/companies/c1/ai-setup': readyAiSetup({ work_mode: 'hybrid', work_runtime: 'cli:claude_code' }), 'PUT /companies/c1/ai-setup': readyAiSetup() }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    expect(await within(dialog).findByRole('radio', { name: /Use Claude Code for hands-on work/ })).toBeChecked()
    await userEvent.click(within(dialog).getByRole('radio', { name: /Model API only/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ work_mode: 'api', work_runtime: 'company_agent' }))
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
    await within(dialog).findByRole('radiogroup', { name: 'Who does the hands-on work' })
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

const provider = (id: string, name: string, model: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  kind: 'openrouter',
  base_url: null,
  credential_id: null,
  default_model: model,
  context_length: 65_536,
  is_local: false,
  enabled: true,
  input_price_per_mtok_micros: null,
  output_price_per_mtok_micros: null,
  last_test: { ...makeProviderTest(), model },
  ...over,
})
const providers = page([provider('p1', 'OpenRouter', 'anthropic/claude-sonnet-4.5'), provider('p2', 'Local', 'qwen3:32b'), provider('p3', 'Untested', 'x/y', { last_test: null }), provider('p4', 'Off', 'a/b', { enabled: false })])
const workModelSection = (dialog: HTMLElement) => within(dialog).getByRole('region', { name: 'Work model (heavy lifting)' })

describe('Settings → AI panel: the optional work model (M12)', () => {
  it('explains the split, defaults to the main model, and lists only tested, enabled providers', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/model-providers': providers })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const section = await waitFor(() => workModelSection(dialog))
    expect(within(section).getByText('Optional')).toBeInTheDocument()
    expect(within(section).getByText(/Planning, standups and reviews always use the main model/)).toBeInTheDocument()
    expect(within(section).getByText(/In hybrid mode, work goes to the coding CLI/)).toBeInTheDocument()
    const select = await within(section).findByLabelText('Model for heavy work')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    expect(select).toHaveValue('')
    expect(within(select).getByRole('option', { name: 'Same as the main model' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: 'Local · qwen3:32b' })).toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: /Untested/ })).not.toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: /Off/ })).not.toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Clear work model' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('saves a chosen work model with the one Save, alongside the management model', async () => {
    const api = mockApi(routes({ '/companies/c1/model-providers': providers, 'PUT /companies/c1/ai-setup': readyAiSetup() }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const select = await within(dialog).findByLabelText('Model for heavy work')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    await userEvent.selectOptions(select, 'Local · qwen3:32b')
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await waitFor(() => expect(save).toBeEnabled())
    await userEvent.click(save)
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'PUT')).toHaveLength(1))
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({
      management_provider_id: 'p1',
      management_model: 'anthropic/claude-sonnet-4.5',
      work_mode: 'api',
      work_runtime: 'company_agent',
      work_model_provider_id: 'p2',
      work_model_name: 'qwen3:32b',
    })
  })

  it('shows the saved work model and clears it by sending both fields as null', async () => {
    const saved = { provider_id: 'p2', provider_name: 'Local', kind: 'ollama', model: 'qwen3:32b', last_test: makeProviderTest() }
    const api = mockApi(routes({ '/companies/c1/model-providers': providers, '/companies/c1/ai-setup': readyAiSetup({ work_model: saved }), 'PUT /companies/c1/ai-setup': readyAiSetup() }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const select = await within(dialog).findByLabelText('Model for heavy work')
    await waitFor(() => expect(select).toHaveValue('p2::qwen3:32b'))
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled() // untouched: nothing to save
    await userEvent.click(within(workModelSection(dialog)).getByRole('button', { name: 'Clear work model' }))
    expect(select).toHaveValue('')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ work_model_provider_id: null, work_model_name: null }))
  })

  it('keeps a saved work model visible even when no provider is tested for it any more', async () => {
    const saved = { provider_id: 'pz', provider_name: 'Gone', kind: 'openrouter', model: 'old/model', last_test: null }
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/ai-setup': readyAiSetup({ work_model: saved }) })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const select = await within(dialog).findByLabelText('Model for heavy work')
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Gone · old/model (current)' })).toBeInTheDocument())
    expect(select).toHaveValue('pz::old/model')
    expect(within(select).getAllByRole('option')).toHaveLength(2) // the main-model default and the saved one
  })

  it('is read-only for someone who cannot change the AI setup', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/model-providers': providers, '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } })).impl)
    renderCanvas('/c/c1?panel=ai')
    const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
    const select = await within(dialog).findByLabelText('Model for heavy work')
    expect(select).toBeDisabled()
    expect(within(dialog).getByText(/Only an owner or admin can change the work model/)).toBeInTheDocument()
  })
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
