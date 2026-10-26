import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AiSetupStatus, CliRuntimeStatus, ProviderTest } from '@/api/types'
import { jsonResponse, makeCompany, mockApi, page } from '@/test/factories'
import { makeAiSetup, makeCli, makeProviderTest, readyAiSetup } from '@/test/m10'
import { renderWithProviders } from '@/test/render'
import { OnboardingFlow } from './OnboardingFlow'

afterEach(() => vi.unstubAllGlobals())

const SECRET = 'sk-or-v1-very-secret-key'
const me = { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'owner' }] }

interface Backend {
  ai?: AiSetupStatus
  providers?: unknown[]
  test?: ProviderTest | (() => ProviderTest)
  models?: Array<{ id: string }> | 'missing'
  detect?: unknown
  clis?: CliRuntimeStatus[] | 'missing'
}

/** A stateful fake of the M10 endpoints the two AI steps use. */
function backend(b: Backend = {}) {
  let ai = b.ai ?? makeAiSetup()
  let providers = [...(b.providers ?? [])] as Array<Record<string, unknown>>
  const api = mockApi({
    '/auth/session': { state: 'authenticated', user: { id: 'u', username: 'sam', display_name: 'Sam', email: null } },
    '/me': me,
    '/companies/c1': makeCompany({ id: 'c1' }),
    '/companies/c1/ai-setup': () => jsonResponse(ai),
    'PUT /companies/c1/ai-setup': (_u: URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      const provider = providers.find((p) => p.id === body.management_provider_id)
      ai = readyAiSetup({
        management: { provider_id: body.management_provider_id, provider_name: String(provider?.name ?? 'Provider'), kind: String(provider?.kind ?? 'openrouter'), model: body.management_model, last_test: makeProviderTest() },
        work_mode: body.work_mode,
        work_runtime: body.work_runtime === 'company_agent' ? null : body.work_runtime,
        clis: ai.clis,
      })
      return jsonResponse(ai)
    },
    '/companies/c1/model-providers': () => jsonResponse(page(providers)),
    '/companies/c1/model-providers/detect': b.detect ?? { reachable: false, base_url: null, models: [], message: 'No Ollama' },
    'POST /companies/c1/credentials': () => jsonResponse({ id: 'cr1', name: 'openrouter-key', kind: 'api_key' }, 201),
    'POST /companies/c1/model-providers': (_u: URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      const created = { id: `p${providers.length + 1}`, company_id: 'c1', context_length: 32768, is_local: false, enabled: true, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null, ...body }
      providers = [...providers, created]
      return jsonResponse(created, 201)
    },
    'PATCH /companies/c1/model-providers/p1': (_u: URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      providers = providers.map((p) => (p.id === 'p1' ? { ...p, ...body } : p))
      return jsonResponse(providers.find((p) => p.id === 'p1'))
    },
    'POST /companies/c1/model-providers/p1/test': () => jsonResponse(typeof b.test === 'function' ? b.test() : (b.test ?? makeProviderTest())),
    ...(b.models !== 'missing' ? { '/companies/c1/model-providers/p1/models': { items: b.models ?? [{ id: 'anthropic/claude-sonnet-4.5' }, { id: 'openai/gpt-4.1' }] } } : null),
    ...(b.clis !== 'missing' ? { '/cli-runtimes': { items: b.clis ?? [] } } : null),
  })
  vi.stubGlobal('fetch', api.impl)
  return api
}

function renderGate(onDone = vi.fn()) {
  renderWithProviders(<OnboardingFlow mode="gate" companyId="c1" onDone={onDone} />)
  return onDone
}

async function chooseOpenRouter(model = 'anthropic/claude-sonnet-4.5') {
  await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
  await userEvent.type(screen.getByLabelText(/OpenRouter API key/), SECRET)
  await userEvent.type(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' }), model)
}

const saveBtn = () => screen.getByRole('button', { name: 'Save and open my company' })
const testPassedMsg = () => screen.findByText(/Test passed\. It is saved with the button below/)
const readyClis = (...ids: Array<[string, string]>) => ids.map(([id, name]) => makeCli({ id, display_name: name, auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: true, detail: 'OK', tested_at: null } } }))

describe('the AI gate as a one-step onboarding (existing users)', () => {
  it('is just "Connect your AI", with no welcome, account or company step', async () => {
    backend()
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 1 · Connect your AI')).toBeInTheDocument()
    expect(screen.queryByText(/A company that runs itself/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Your name/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Setup progress' })).getAllByRole('listitem')).toHaveLength(1)
  })

  it('lays out the model API as required and the coding CLIs as optional, with one save', async () => {
    backend({ clis: [makeCli()] })
    renderGate()
    const api = await screen.findByRole('region', { name: 'Model API' })
    expect(within(api).getByText('Required')).toBeInTheDocument()
    expect(within(api).getByText(/Runs the company/)).toBeInTheDocument()
    const cli = screen.getByRole('region', { name: 'Coding CLIs' })
    expect(within(cli).getByText('Optional')).toBeInTheDocument()
    expect(within(cli).getByText(/Skip it and they do everything through the model API/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Save/ })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument()
  })

  it('offers the four providers and tells you why you are here', async () => {
    backend()
    renderGate()
    const group = await screen.findByRole('radiogroup', { name: 'Provider' })
    for (const name of [/^OpenRouter/, /^Anthropic/, /^OpenAI(?!-)/, /^OpenAI-compatible/]) expect(within(group).getByRole('radio', { name })).toBeInTheDocument()
    expect(screen.getByText(/Connect an AI provider and run its test/)).toBeInTheDocument()
  })

  it('the save is disabled until the model API passed its test, and says why', async () => {
    backend()
    renderGate()
    await screen.findByRole('heading', { name: 'Connect your AI' })
    expect(saveBtn()).toBeDisabled()
    expect(await screen.findByText('Test the model API to continue.')).toBeInTheDocument()
  })
})

describe('Connect your AI: key, test, model, ready', () => {
  it('stores the key once as a credential, makes the provider with the credential id, tests it with the loader showing every real check, and saves nothing until the one button is pressed', async () => {
    const api = backend()
    const done = renderGate()
    await chooseOpenRouter()
    const key = screen.getByLabelText(/OpenRouter API key/)
    expect(key).toHaveAttribute('type', 'password')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))

    // the form stays; the loader shows every real check with the server's detail, and nothing is saved yet
    expect(await testPassedMsg()).toBeInTheDocument()
    const loader = screen.getByRole('list', { name: 'Connection test' })
    for (const label of ['Reachable', 'Authenticated', 'Tool calling', 'Context window', 'Model available', 'Ready']) expect(within(loader).getByRole('listitem', { name: `${label}: passed` })).toBeInTheDocument()
    expect(within(loader).getByText('tool_calling is fine')).toBeInTheDocument() // the server's own detail

    // the order of events and what each carried: testing never saves
    const writes = () => api.calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/companies/c1'))
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(['POST /companies/c1/credentials', 'POST /companies/c1/model-providers', 'POST /companies/c1/model-providers/p1/test'])
    expect(writes()[0]!.body).toEqual({ name: 'openrouter-key', kind: 'api_key', value: SECRET })
    expect(writes()[1]!.body).toMatchObject({ kind: 'openrouter', credential_id: 'cr1', default_model: 'anthropic/claude-sonnet-4.5' })
    expect(JSON.stringify(writes().slice(1))).not.toContain(SECRET) // the secret went to the credential endpoint only
    expect(key).toHaveValue('') // the server has the key; the browser forgot it
    expect(document.body.innerHTML).not.toContain(SECRET)

    await waitFor(() => expect(saveBtn()).toBeEnabled())
    expect(saveBtn()).toBeEnabled()
    expect(screen.queryByText('Test the model API to continue.')).not.toBeInTheDocument()
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    const puts = api.calls.filter((c) => c.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0]!.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'api', work_runtime: 'company_agent' })
  })

  it('a failing check says which and why, saves nothing and keeps the save disabled', async () => {
    const api = backend({ test: makeProviderTest({ failing: 'tool_calling' }) })
    renderGate()
    await chooseOpenRouter()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    const loader = await screen.findByRole('list', { name: 'Connection test' })
    await waitFor(() => expect(within(loader).getByRole('listitem', { name: 'Tool calling: failed' })).toBeInTheDocument())
    expect(within(loader).getByRole('listitem', { name: 'Reachable: passed' })).toBeInTheDocument()
    expect(within(loader).getByRole('listitem', { name: 'Ready: failed' })).toBeInTheDocument()
    expect(within(loader).getByText('tool_calling failed: nope')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(/tool calling failed: tool_calling failed: nope/)
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
    expect(saveBtn()).toBeDisabled()
    expect(await screen.findByText('Test the model API to continue.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test again' })).toBeEnabled()
  })

  it('does not store the key twice, or create a second provider, when only the model is changed and tested again', async () => {
    const api = backend({ test: vi.fn().mockReturnValueOnce(makeProviderTest({ failing: 'model_exists' })).mockReturnValue(makeProviderTest()) as unknown as () => ProviderTest })
    renderGate()
    await chooseOpenRouter('nope/not-a-model')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await screen.findByRole('listitem', { name: 'Model available: failed' })
    // the provider now exists, so its model list is offered as a picker
    // re-query each time: the input re-renders when the model list arrives, so a kept reference goes stale
    const modelInput = () => screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' })
    await userEvent.clear(modelInput())
    await userEvent.click(modelInput())
    const option = await screen.findByRole('option', { name: /openai\/gpt-4\.1/ })
    await userEvent.click(option)
    await waitFor(() => expect(modelInput()).toHaveValue('openai/gpt-4.1'))
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' })) // a changed model clears the old result
    await testPassedMsg()
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
    await waitFor(() => expect(saveBtn()).toBeEnabled())
    await userEvent.click(saveBtn())
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/credentials')).toHaveLength(1)
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers')).toHaveLength(1)
    expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ default_model: 'openai/gpt-4.1' })
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ management_model: 'openai/gpt-4.1' })
  })

  it('falls back to a plain text field with examples when the provider has no model list', async () => {
    backend({ models: 'missing', test: makeProviderTest({ failing: 'model_exists' }) })
    renderGate()
    await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'anthropic/claude-sonnet-4.5' })) // an example fills the field
    expect(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' })).toHaveValue('anthropic/claude-sonnet-4.5')
    expect(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' })).toHaveAttribute('placeholder', 'Type the model name')
  })

  it('requires a key for a cloud provider and a base URL for an OpenAI-compatible one, before any request', async () => {
    const api = backend()
    renderGate()
    await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
    await userEvent.type(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' }), 'm')
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled() // no key yet
    await userEvent.click(screen.getByRole('radio', { name: /^OpenAI-compatible/ }))
    await userEvent.type(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' }), 'm')
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled() // no base URL
    await userEvent.type(screen.getByLabelText(/Base URL/), 'http://127.0.0.1:1234/v1')
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('never lets a server error echo the key', async () => {
    const api = backend()
    api.calls.length = 0
    vi.stubGlobal(
      'fetch',
      mockApi({
        '/me': me,
        '/companies/c1/ai-setup': makeAiSetup(),
        '/companies/c1/model-providers': page([]),
        '/companies/c1/model-providers/detect': { reachable: false, models: [] },
        'POST /companies/c1/credentials': () => jsonResponse({ error: { code: 'bad_request', message: `Bad key ${SECRET}`, details: null } }, 400),
      }).impl,
    )
    renderGate()
    await chooseOpenRouter()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/Bad key \[redacted\]/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET)
  })

  it('explains a refused save with the reason the server gives', async () => {
    const api = backend()
    void api
    vi.stubGlobal(
      'fetch',
      mockApi({
        '/me': me,
        '/companies/c1/ai-setup': makeAiSetup(),
        '/companies/c1/model-providers': page([]),
        '/companies/c1/model-providers/detect': { reachable: false, models: [] },
        'POST /companies/c1/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
        'POST /companies/c1/model-providers': { id: 'p1', name: 'OpenRouter', kind: 'openrouter', default_model: 'm' },
        '/companies/c1/model-providers/p1/models': { items: [] },
        'POST /companies/c1/model-providers/p1/test': makeProviderTest(),
        'PUT /companies/c1/ai-setup': () => jsonResponse({ error: { code: 'ai_not_ready', message: 'The provider\'s latest test did not pass for this model.', details: { reason: 'provider_test_failed' } } }, 409),
      }).impl,
    )
    renderGate()
    await chooseOpenRouter()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await testPassedMsg()
    await waitFor(() => expect(saveBtn()).toBeEnabled())
    await userEvent.click(saveBtn()) // the reason shows up after pressing save, not after testing
    expect(await screen.findByText(/latest test did not pass for this model/)).toBeInTheDocument()
    expect(screen.getByText(/The last test of your AI provider did not pass/)).toBeInTheDocument()
  })
})

describe('Connect your AI: local Ollama and providers that already exist', () => {
  it('detects a local Ollama by itself and offers it as a fifth card, with its models', async () => {
    const api = backend({ detect: { reachable: true, base_url: 'http://127.0.0.1:11434', models: [{ name: 'qwen2.5:7b', size_bytes: 4_700_000_000, context_length: 32768 }], message: null } })
    renderGate()
    const radio = await screen.findByRole('radio', { name: /Local Ollama/ })
    expect(radio).toHaveTextContent(/Found 1 model on this computer/)
    expect(api.calls.some((c) => c.path === '/companies/c1/model-providers/detect')).toBe(true)
    await userEvent.click(radio)
    expect(screen.getByLabelText(/Base URL/)).toHaveValue('http://127.0.0.1:11434')
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument()
    const model = screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' })
    await userEvent.click(model)
    await userEvent.click(await screen.findByRole('option', { name: /qwen2\.5:7b/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await testPassedMsg()
    await waitFor(() => expect(saveBtn()).toBeEnabled())
    await userEvent.click(saveBtn())
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(api.calls.some((c) => c.path === '/companies/c1/credentials')).toBe(false) // no key for a local model
    expect(api.calls.find((c) => c.path === '/companies/c1/model-providers' && c.method === 'POST')?.body).toMatchObject({ kind: 'ollama', base_url: 'http://127.0.0.1:11434', default_model: 'qwen2.5:7b', is_local: true })
  })

  it('says nothing is found, without a card, when there is no Ollama', async () => {
    backend()
    renderGate()
    await screen.findByRole('radiogroup', { name: 'Provider' })
    expect(screen.queryByRole('radio', { name: /Local Ollama/ })).not.toBeInTheDocument()
    expect(await screen.findByText(/No local Ollama found on this computer/)).toBeInTheDocument()
  })

  it('reuses a provider that is already connected: no new key, no new provider, just a test', async () => {
    const existing = { id: 'p1', company_id: 'c1', name: 'Team OpenRouter', kind: 'openrouter', enabled: true, default_model: 'openai/gpt-4.1', base_url: null, credential_id: 'cr0', context_length: 128000, is_local: false, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null }
    const api = backend({ providers: [existing] })
    renderGate()
    await userEvent.click(await screen.findByRole('button', { name: /Team OpenRouter/ }))
    expect(screen.getByLabelText(/^Model(?! API)\b/, { selector: 'input' })).toHaveValue('openai/gpt-4.1')
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await testPassedMsg()
    await waitFor(() => expect(saveBtn()).toBeEnabled())
    await userEvent.click(saveBtn())
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(api.calls.some((c) => c.path === '/companies/c1/credentials')).toBe(false)
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers')).toHaveLength(0)
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ management_provider_id: 'p1', management_model: 'openai/gpt-4.1' })
  })

  it('shows what is connected when the AI is already ready, and lets you test it again', async () => {
    const api = backend({ ai: readyAiSetup() })
    const done = renderGate()
    const summary = await screen.findByRole('region', { name: 'Connected AI' })
    expect(within(summary).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(summary).getByText('anthropic/claude-sonnet-4.5')).toBeInTheDocument()
    expect(within(summary).getAllByText('Ready').length).toBeGreaterThan(0)
    expect(saveBtn()).toBeEnabled()
    // saving with nothing changed writes nothing
    api.calls.length = 0
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
  })
})

describe('Coding CLIs: optional, always visible, saved together with the model API', () => {
  async function toForm(b: Backend) {
    const api = backend({ ai: readyAiSetup(), ...b })
    const done = renderGate()
    await screen.findByRole('region', { name: 'Connected AI' })
    await screen.findByRole('radiogroup', { name: 'Who does the hands-on work' })
    return { api, done }
  }
  const handsOn = () => screen.getByRole('radiogroup', { name: 'Who does the hands-on work' })
  const codex = makeCli({ id: 'codex', display_name: 'Codex CLI', installed: false, version: null, binary_path: null, install: { available: true, command: 'npm install -g @openai/codex', requires: ['npm'] } })

  it('lists the CLIs and what is installed with no extra click; Model API only is checked by default; saving writes nothing new', async () => {
    const { api, done } = await toForm({ clis: [...readyClis(['claude_code', 'Claude Code']), codex, makeCli({ id: 'opencode', display_name: 'OpenCode', version: null })] })
    expect(screen.getByRole('article', { name: 'Claude Code' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Codex CLI' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'OpenCode' })).toBeInTheDocument()
    expect(screen.getByText(/Claude Code — ready · Codex CLI — not installed · OpenCode — installed, not tested/)).toBeInTheDocument()
    expect(within(handsOn()).getByRole('radio', { name: /Model API only/ })).toBeChecked()
    expect(within(handsOn()).getByRole('radio', { name: /Use Claude Code for hands-on work/ })).not.toBeChecked()
    expect(saveBtn()).toBeEnabled() // optional: nothing to decide
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('picking a CLI that tested OK and saving PUTs hybrid with that runtime', async () => {
    const { api, done } = await toForm({ clis: [...readyClis(['claude_code', 'Claude Code'], ['opencode', 'OpenCode']), codex] })
    await userEvent.click(within(screen.getByRole('article', { name: 'Claude Code' })).getByRole('radio', { name: /Use Claude Code for hands-on work/ }))
    expect(within(handsOn()).getByRole('radio', { name: /Model API only/ })).not.toBeChecked()
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    const puts = api.calls.filter((c) => c.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0]!.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'hybrid', work_runtime: 'cli:claude_code' })
  })

  it('does not preselect a CLI by itself, even when exactly one is ready', async () => {
    await toForm({ clis: readyClis(['claude_code', 'Claude Code']) })
    expect(screen.getByRole('radio', { name: /Use Claude Code for hands-on work/ })).not.toBeChecked()
    expect(within(handsOn()).getByRole('radio', { name: /Model API only/ })).toBeChecked()
  })

  it('a CLI that has not tested OK is disabled ("test it first"); a CLI that is not installed has no radio', async () => {
    await toForm({ clis: [makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: null } }), codex] })
    expect(screen.getByRole('radio', { name: /Use Claude Code for hands-on work/ })).toBeDisabled()
    expect(screen.getByText(/test it first/)).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'Codex CLI' })).queryByRole('radio')).not.toBeInTheDocument()
    expect(saveBtn()).toBeEnabled() // the model API alone is a complete setup
  })

  it('preselects the saved CLI, and going back to Model API only saves api + company_agent', async () => {
    const { api, done } = await toForm({ ai: readyAiSetup({ work_mode: 'hybrid', work_runtime: 'cli:claude_code' }), clis: readyClis(['claude_code', 'Claude Code']) })
    expect(screen.getByRole('radio', { name: /Use Claude Code for hands-on work/ })).toBeChecked()
    await userEvent.click(within(handsOn()).getByRole('radio', { name: /Model API only/ }))
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ work_mode: 'api', work_runtime: 'company_agent' })
  })

  it('one save sends both parts: the tested model API and the chosen CLI, in a single PUT', async () => {
    const api = backend({ clis: readyClis(['claude_code', 'Claude Code']) })
    const done = renderGate()
    await chooseOpenRouter()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await testPassedMsg()
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false) // testing saves nothing
    await userEvent.click(screen.getByRole('radio', { name: /Use Claude Code for hands-on work/ }))
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
    const puts = api.calls.filter((c) => c.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0]!.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'hybrid', work_runtime: 'cli:claude_code' })
  })

  it('degrades when the server does not report CLIs yet: the model API alone works and the save is still usable', async () => {
    const { done } = await toForm({ clis: 'missing' })
    expect(screen.getByText(/does not report coding CLIs yet/)).toBeInTheDocument()
    expect(screen.getByText(/The model API alone works/)).toBeInTheDocument()
    await userEvent.click(saveBtn())
    await waitFor(() => expect(done).toHaveBeenCalled())
  })

  it('says so when no CLI is known', async () => {
    await toForm({ clis: [] })
    expect(screen.getByText(/No coding CLI is known to this server/)).toBeInTheDocument()
    expect(within(handsOn()).getByRole('radio', { name: /Model API only/ })).toBeChecked()
  })

  it('shows the CLI tiles read-only to someone who cannot manage them (member)', async () => {
    const api = backend({ ai: readyAiSetup(), clis: [makeCli()] })
    vi.stubGlobal(
      'fetch',
      ((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
        if (url.endsWith('/api/me')) return Promise.resolve(jsonResponse({ ...me, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] }))
        return api.impl(input, init)
      }) as typeof fetch,
    )
    renderGate()
    await screen.findByRole('region', { name: 'Connected AI' })
    await screen.findByRole('radiogroup', { name: 'Who does the hands-on work' })
    expect(screen.queryByRole('button', { name: 'Test again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change provider or model' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save and open my company' })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an owner or admin can change how work gets done/)).toBeInTheDocument()
  })
})
