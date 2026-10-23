import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { jsonResponse, makeAgent, makeCompany, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'

const SECRET = 'sk-or-v1-supersecretvalue-0123456789'
const founder = makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', role_title: 'Founder & CEO' })

const provider = (over = {}) => ({
  id: 'p1', name: 'Local Ollama', kind: 'ollama', base_url: 'http://127.0.0.1:11434', credential_id: null, default_model: 'qwen2.5:7b', context_length: 32768, is_local: true, enabled: true, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null, ...over,
})

const base = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder]),
  '/companies/c1/departments': page([]),
  '/companies/c1/activity': page([]),
  '/runtimes': {
    items: [
      { id: 'company_agent', display_name: 'Company Agent (Hermes fork)', kind: 'company_agent', capabilities: ['streaming_events', 'cancel'], health: { status: 'degraded', detail: 'Runtime python found. Docker daemon is not reachable; sandbox backend: none (restricted local mode).', checked_at: '2026-10-23T12:00:00Z' }, config_hint: null },
    ],
  },
}

function renderIntegrations(routes: Record<string, unknown>) {
  const api = mockApi({ ...base, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(
    <Routes>
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route: '/c/c1?panel=integrations' },
  )
  return api
}

afterEach(() => vi.unstubAllGlobals())

describe('Integrations: M3b endpoints missing', () => {
  it('shows honest not-built states for providers and credentials, and still shows runtimes', async () => {
    renderIntegrations({})
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    expect(await within(dialog).findByText('Model providers are not built yet')).toBeInTheDocument()
    expect(await within(dialog).findByText('The credential store is not built yet')).toBeInTheDocument()
    expect(within(dialog).getAllByText(/Arrives in M3b/).length).toBe(2)
  })
})

describe('Runtime health card', () => {
  it('shows the company_agent health and pulls the sandbox status out of the detail', async () => {
    renderIntegrations({})
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    const card = await within(dialog).findByRole('region', { name: 'Company Agent runtime health' })
    expect(within(card).getByText('Degraded')).toBeInTheDocument()
    expect(within(card).getByText(/Docker daemon is not reachable/)).toBeInTheDocument()
    expect(within(card).getByText('Sandbox')).toBeInTheDocument()
    expect(within(card).getByText(/Runtime python found/)).toBeInTheDocument()
  })
  it('says plainly when the runtime is not registered', async () => {
    renderIntegrations({ '/runtimes': { items: [] } })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    expect(await within(dialog).findByText('The company_agent runtime is not registered')).toBeInTheDocument()
  })
})

describe('Model providers', () => {
  it('lists providers with local/free badges and honest prices, and shows credential metadata only', async () => {
    renderIntegrations({
      '/companies/c1/model-providers': page([provider(), provider({ id: 'p2', name: 'OpenRouter', kind: 'openrouter', base_url: null, is_local: false, credential_id: 'c9', default_model: 'qwen/qwen3', input_price_per_mtok_micros: 150_000, output_price_per_mtok_micros: 600_000, enabled: false }), provider({ id: 'p3', name: 'Mystery', kind: 'openai_compatible', is_local: false, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null })]),
      '/companies/c1/credentials': page([{ id: 'c9', name: 'openrouter-main', kind: 'api_key', created_at: '2026-10-23T12:00:00Z', revoked_at: null }]),
    })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    const rows = (await within(dialog).findByText('Local Ollama')).closest('table')!
    expect(within(rows).getByText(/Local · no provider charge/)).toBeInTheDocument()
    expect(within(rows).getByText('$0.15 in / $0.60 out per 1M')).toBeInTheDocument()
    expect(within(rows).getByText('Unpriced')).toBeInTheDocument() // unknown is never shown as $0
    expect(within(rows).getByText('Disabled')).toBeInTheDocument()
    expect(within(rows).getByText('Key stored')).toBeInTheDocument()
    expect(await within(dialog).findByText('openrouter-main')).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent(/sk-/)
  })

  it('tests a provider and shows the server’s served-context warning', async () => {
    const warning = 'Ollama serves qwen2.5:7b with a 4096-token context, less than the 32768 configured.'
    const api = renderIntegrations({
      '/companies/c1/model-providers': page([provider()]),
      'POST /companies/c1/model-providers/p1/test': { ok: true, message: 'Model answered', latency_ms: 420, model: 'qwen2.5:7b', warnings: [warning] },
    })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Test' }))
    expect(await within(dialog).findByText(warning)).toBeInTheDocument()
    expect(within(dialog).getByText('Local Ollama answered')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers/p1/test')).toBe(true)
  })

  it('shows a failed test and a test that could not run', async () => {
    renderIntegrations({
      '/companies/c1/model-providers': page([provider()]),
      'POST /companies/c1/model-providers/p1/test': { ok: false, message: 'connection refused' },
    })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Test' }))
    expect(await within(dialog).findByText('Local Ollama did not pass')).toBeInTheDocument()
    expect(within(dialog).getByText('connection refused')).toBeInTheDocument()
  })

  it('detects local Ollama models and registers one with a pre-filled form', async () => {
    const api = renderIntegrations({
      '/companies/c1/model-providers': page([]),
      '/companies/c1/model-providers/detect': { reachable: true, base_url: 'http://127.0.0.1:11434', models: [{ name: 'qwen2.5:7b' }, { name: 'olmo2:13b' }] },
      'POST /companies/c1/model-providers': (_u: URL, init?: RequestInit) => jsonResponse({ ...provider(), ...JSON.parse(String(init?.body)), id: 'new' }, 201),
    })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Detect local Ollama' }))
    const found = await within(dialog).findByRole('status', { name: 'Local Ollama detection' })
    expect(within(found).getByText(/Found 2 local models/)).toBeInTheDocument()
    await userEvent.click(within(found).getAllByRole('button', { name: 'Register' })[0]!)
    const form = await screen.findByRole('dialog', { name: 'Add model provider' })
    expect(within(form).getByLabelText(/Name/)).toHaveValue('Ollama · qwen2.5:7b')
    expect(within(form).getByLabelText(/Base URL/)).toHaveValue('http://127.0.0.1:11434')
    expect(within(form).getByLabelText(/Default model/)).toHaveValue('qwen2.5:7b')
    expect(within(form).queryByLabelText(/Secret value/)).not.toBeInTheDocument() // Ollama takes no key
    await userEvent.click(within(form).getByRole('button', { name: 'Add provider' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers')?.body).toMatchObject({ name: 'Ollama · qwen2.5:7b', kind: 'ollama', is_local: true, credential_id: null, default_model: 'qwen2.5:7b' }))
    expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/credentials')).toBe(false)
  })

  it('reports when Ollama is not running', async () => {
    renderIntegrations({ '/companies/c1/model-providers': page([]), '/companies/c1/model-providers/detect': { reachable: false, base_url: 'http://127.0.0.1:11434' } })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Detect local Ollama' }))
    expect(await within(dialog).findByText(/No local Ollama answered/)).toBeInTheDocument()
  })

  it('says detection is not built yet when the endpoint is missing', async () => {
    renderIntegrations({ '/companies/c1/model-providers': page([]) })
    const dialog = await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Detect local Ollama' }))
    expect(await within(dialog).findByText(/Detection is not built yet/)).toBeInTheDocument()
  })
})

describe('Add provider form: the secret is write-only', () => {
  async function fillCloud(secret = SECRET) {
    await userEvent.click(await screen.findByRole('button', { name: 'Add provider' }))
    const form = await screen.findByRole('dialog', { name: 'Add model provider' })
    await userEvent.selectOptions(within(form).getByLabelText(/Kind/), 'openrouter')
    await userEvent.type(within(form).getByLabelText(/^Name/), 'OpenRouter')
    await userEvent.type(within(form).getByLabelText(/Default model/), 'qwen/qwen3')
    await userEvent.type(within(form).getByLabelText(/Input price/), '0.15')
    await userEvent.type(within(form).getByLabelText(/Output price/), '0.6')
    await userEvent.type(within(form).getByLabelText(/Credential name/), 'openrouter-main')
    await userEvent.type(within(form).getByLabelText(/Secret value/), secret)
    return form
  }

  it('is a password field that never autocompletes', async () => {
    renderIntegrations({ '/companies/c1/model-providers': page([]) })
    await screen.findByRole('dialog', { name: 'Integrations' })
    const form = await fillCloud()
    const input = within(form).getByLabelText(/Secret value/)
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
  })

  it('stores the key once as a credential, sends the provider only the credential id, and echoes nothing back', async () => {
    const api = renderIntegrations({
      '/companies/c1/model-providers': page([]),
      'POST /companies/c1/credentials': (_u: URL, init?: RequestInit) => jsonResponse({ id: 'cred-1', name: JSON.parse(String(init?.body)).name, kind: 'api_key', created_at: '2026-10-24T00:00:00Z', revoked_at: null }, 201),
      'POST /companies/c1/model-providers': (_u: URL, init?: RequestInit) => jsonResponse({ ...provider(), ...JSON.parse(String(init?.body)), id: 'new' }, 201),
    })
    await screen.findByRole('dialog', { name: 'Integrations' })
    const form = await fillCloud()
    await userEvent.click(within(form).getByRole('button', { name: 'Add provider' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers')).toBe(true))

    const cred = api.calls.find((c) => c.path === '/companies/c1/credentials' && c.method === 'POST')!
    expect(cred.body).toEqual({ name: 'openrouter-main', kind: 'api_key', value: SECRET })
    const prov = api.calls.find((c) => c.path === '/companies/c1/model-providers' && c.method === 'POST')!
    expect(prov.body).toMatchObject({ name: 'OpenRouter', kind: 'openrouter', credential_id: 'cred-1', default_model: 'qwen/qwen3', input_price_per_mtok_micros: 150_000, output_price_per_mtok_micros: 600_000, is_local: false })
    expect(JSON.stringify(prov.body)).not.toContain(SECRET)
    expect(api.calls.filter((c) => JSON.stringify(c.body ?? '').includes(SECRET))).toHaveLength(1) // only the credential call
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add model provider' })).not.toBeInTheDocument())
    expect(document.body.innerHTML).not.toContain(SECRET)
  })

  it('clears the key from the form once stored, and never stores it twice when the provider call is retried', async () => {
    let providerAttempts = 0
    const api = renderIntegrations({
      '/companies/c1/model-providers': page([]),
      'POST /companies/c1/credentials': { id: 'cred-1', name: 'openrouter-main', kind: 'api_key' },
      'POST /companies/c1/model-providers': (_u: URL, init?: RequestInit) => {
        providerAttempts++
        // A server that (wrongly) echoes the secret in its error must still not reach the screen.
        return providerAttempts === 1 ? jsonResponse({ error: { code: 'bad_request', message: `rejected near ${SECRET}`, details: null } }, 400) : jsonResponse({ ...provider(), ...JSON.parse(String(init?.body)), id: 'new' }, 201)
      },
    })
    await screen.findByRole('dialog', { name: 'Integrations' })
    const form = await fillCloud()
    await userEvent.click(within(form).getByRole('button', { name: 'Add provider' }))

    const alert = await within(form).findByText(/rejected near/)
    expect(alert).not.toHaveTextContent(SECRET)
    expect(alert).toHaveTextContent('[redacted]')
    expect(within(form).queryByLabelText(/Secret value/)).not.toBeInTheDocument()
    expect(within(form).getByText(/Key saved as credential/)).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(SECRET)

    await userEvent.click(within(form).getByRole('button', { name: 'Add provider' }))
    await waitFor(() => expect(providerAttempts).toBe(2))
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/credentials')).toHaveLength(1)
    expect(api.calls.find((c) => c.path === '/companies/c1/model-providers' && c.method === 'POST' && c.body)).toBeTruthy()
  })

  it('validates before sending anything', async () => {
    const api = renderIntegrations({ '/companies/c1/model-providers': page([]) })
    await screen.findByRole('dialog', { name: 'Integrations' })
    await userEvent.click(await screen.findByRole('button', { name: 'Add provider' }))
    const form = await screen.findByRole('dialog', { name: 'Add model provider' })
    await userEvent.selectOptions(within(form).getByLabelText(/Kind/), 'openai')
    await userEvent.type(within(form).getByLabelText(/Input price/), 'abc')
    await userEvent.click(within(form).getByRole('button', { name: 'Add provider' }))
    expect(await within(form).findByText('Name is required')).toBeInTheDocument()
    expect(within(form).getByText('An API key is required for this provider')).toBeInTheDocument()
    expect(within(form).getByText(/Enter a USD amount per 1M tokens/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })
})
