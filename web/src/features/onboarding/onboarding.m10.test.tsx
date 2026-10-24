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
  await userEvent.type(screen.getByLabelText(/^Model\b/, { selector: 'input' }), model)
}

const continueBtn = () => screen.getByRole('button', { name: 'Continue' })

describe('the AI gate as a two-step onboarding (existing users)', () => {
  it('is just "Connect your AI" and "How should work get done?", with no welcome, account or company step', async () => {
    backend()
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 2 · Connect your AI')).toBeInTheDocument()
    expect(screen.queryByText(/A company that runs itself/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Your name/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Setup progress' })).getAllByRole('listitem')).toHaveLength(2)
  })

  it('offers the four providers and tells you why you are here', async () => {
    backend()
    renderGate()
    const group = await screen.findByRole('radiogroup', { name: 'Provider' })
    for (const name of [/^OpenRouter/, /^Anthropic/, /^OpenAI(?!-)/, /^OpenAI-compatible/]) expect(within(group).getByRole('radio', { name })).toBeInTheDocument()
    expect(screen.getByText(/Connect an AI provider and run its test/)).toBeInTheDocument()
  })

  it('Continue is disabled until the setup is ready', async () => {
    backend()
    renderGate()
    await screen.findByRole('heading', { name: 'Connect your AI' })
    expect(continueBtn()).toBeDisabled()
  })
})

describe('Connect your AI: key, test, model, ready', () => {
  it('stores the key once as a credential, makes the provider with the credential id, tests it with the loader showing every real check, and saves it', async () => {
    const api = backend()
    renderGate()
    await chooseOpenRouter()
    const key = screen.getByLabelText(/OpenRouter API key/)
    expect(key).toHaveAttribute('type', 'password')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))

    // saved: the form gives way to the connected summary, whose "last test" lists every real check with the server's detail
    const summary = await screen.findByRole('region', { name: 'Connected AI' })
    const loader = within(summary).getByRole('list', { name: 'Last test' })
    for (const label of ['Reachable', 'Authenticated', 'Tool calling', 'Context window', 'Model available', 'Ready']) expect(within(loader).getByRole('listitem', { name: `${label}: passed` })).toBeInTheDocument()
    expect(within(loader).getByText('tool_calling is fine')).toBeInTheDocument() // the server's own detail

    // the order of events and what each carried
    const posts = api.calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/companies/c1'))
    expect(posts.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /companies/c1/credentials',
      'POST /companies/c1/model-providers',
      'POST /companies/c1/model-providers/p1/test',
      'PUT /companies/c1/ai-setup',
    ])
    expect(posts[0]!.body).toEqual({ name: 'openrouter-key', kind: 'api_key', value: SECRET })
    expect(posts[1]!.body).toMatchObject({ kind: 'openrouter', credential_id: 'cr1', default_model: 'anthropic/claude-sonnet-4.5' })
    expect(JSON.stringify(posts.slice(1))).not.toContain(SECRET) // the secret went to the credential endpoint only
    expect(posts[3]!.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'api', work_runtime: 'company_agent' })
    expect(key).not.toBeInTheDocument() // the connected summary replaced the form, so the key field is gone
    expect(document.body.innerHTML).not.toContain(SECRET)

    await waitFor(() => expect(continueBtn()).toBeEnabled())
  })

  it('a failing check says which and why, saves nothing and keeps Continue disabled', async () => {
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
    expect(continueBtn()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Test again' })).toBeEnabled()
  })

  it('does not store the key twice, or create a second provider, when only the model is changed and tested again', async () => {
    const api = backend({ test: vi.fn().mockReturnValueOnce(makeProviderTest({ failing: 'model_exists' })).mockReturnValue(makeProviderTest()) as unknown as () => ProviderTest })
    renderGate()
    await chooseOpenRouter('nope/not-a-model')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await screen.findByRole('listitem', { name: 'Model available: failed' })
    // the provider now exists, so its model list is offered as a picker
    const model = screen.getByLabelText(/^Model\b/, { selector: 'input' })
    await userEvent.clear(model)
    await userEvent.click(model)
    const option = await screen.findByRole('option', { name: /openai\/gpt-4\.1/ })
    await userEvent.click(option)
    expect(model).toHaveValue('openai/gpt-4.1')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' })) // a changed model clears the old result
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
    expect(screen.getByLabelText(/^Model\b/, { selector: 'input' })).toHaveValue('anthropic/claude-sonnet-4.5')
    expect(screen.getByLabelText(/^Model\b/, { selector: 'input' })).toHaveAttribute('placeholder', 'Type the model name')
  })

  it('requires a key for a cloud provider and a base URL for an OpenAI-compatible one, before any request', async () => {
    const api = backend()
    renderGate()
    await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
    await userEvent.type(screen.getByLabelText(/^Model\b/, { selector: 'input' }), 'm')
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled() // no key yet
    await userEvent.click(screen.getByRole('radio', { name: /^OpenAI-compatible/ }))
    await userEvent.type(screen.getByLabelText(/^Model\b/, { selector: 'input' }), 'm')
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
    expect(await screen.findByText(/latest test did not pass for this model/)).toBeInTheDocument()
    expect(continueBtn()).toBeDisabled()
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
    const model = screen.getByLabelText(/^Model\b/, { selector: 'input' })
    await userEvent.click(model)
    await userEvent.click(await screen.findByRole('option', { name: /qwen2\.5:7b/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
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
    expect(screen.getByLabelText(/^Model\b/, { selector: 'input' })).toHaveValue('openai/gpt-4.1')
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(api.calls.some((c) => c.path === '/companies/c1/credentials')).toBe(false)
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/model-providers')).toHaveLength(0)
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ management_provider_id: 'p1', management_model: 'openai/gpt-4.1' })
  })

  it('shows what is connected when the AI is already ready, and lets you test it again', async () => {
    const api = backend({ ai: readyAiSetup() })
    renderGate()
    const summary = await screen.findByRole('region', { name: 'Connected AI' })
    expect(within(summary).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(summary).getByText('anthropic/claude-sonnet-4.5')).toBeInTheDocument()
    expect(within(summary).getAllByText('Ready').length).toBeGreaterThan(0)
    expect(continueBtn()).toBeEnabled()
    api.calls.length = 0
  })
})

describe('How should work get done?', () => {
  async function toWork(b: Backend) {
    const api = backend({ ai: readyAiSetup(), ...b })
    const done = renderGate()
    await screen.findByRole('region', { name: 'Connected AI' })
    await userEvent.click(continueBtn())
    await screen.findByRole('heading', { name: 'How should work get done?' })
    return { api, done }
  }

  it('has two cards, API only selected by default; Continue finishes the gate without saving anything new', async () => {
    const { api, done } = await toWork({})
    const group = screen.getByRole('radiogroup', { name: 'Work mode' })
    expect(within(group).getByRole('radio', { name: /^API only/ })).toBeChecked()
    expect(within(group).getByRole('radio', { name: /^API \+ coding CLIs/ })).not.toBeChecked()
    expect(screen.getByText('Step 2 of 2 · How work gets done')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open my company' }))
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('hybrid shows the CLI tiles from /cli-runtimes and blocks Continue until one that tested OK is picked', async () => {
    const tested = (id: string, name: string) => makeCli({ id, display_name: name, auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: true, detail: 'OK', tested_at: null } } })
    const untested = makeCli({ id: 'codex', display_name: 'Codex CLI', installed: false, version: null, binary_path: null, install: { available: true, command: 'npm install -g @openai/codex', requires: ['npm'] } })
    const { api, done } = await toWork({ clis: [tested('claude_code', 'Claude Code'), tested('opencode', 'OpenCode'), untested] })
    await userEvent.click(screen.getByRole('radio', { name: /^API \+ coding CLIs/ }))
    expect(await screen.findByRole('article', { name: 'Claude Code' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Codex CLI' })).toBeInTheDocument()
    expect(screen.getByText(/Choose which CLI should do the work/)).toBeInTheDocument() // two are ready: the person must pick
    const finish = screen.getByRole('button', { name: 'Open my company' })
    expect(finish).toBeDisabled()
    expect(within(screen.getByRole('article', { name: 'Codex CLI' })).queryByRole('radio')).not.toBeInTheDocument() // not installed: nothing to choose yet
    await userEvent.click(within(screen.getByRole('article', { name: 'Claude Code' })).getByRole('radio', { name: /Use Claude Code for the work/ }))
    await waitFor(() => expect(finish).toBeEnabled())
    await userEvent.click(finish)
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ management_provider_id: 'p1', management_model: 'anthropic/claude-sonnet-4.5', work_mode: 'hybrid', work_runtime: 'cli:claude_code' })
  })

  it('hybrid with exactly one CLI that tested OK preselects it (and says so by checking its radio)', async () => {
    await toWork({ clis: [makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: true, detail: 'OK', tested_at: null } } })] })
    await userEvent.click(screen.getByRole('radio', { name: /^API \+ coding CLIs/ }))
    expect(await screen.findByRole('radio', { name: /Use Claude Code for the work/ })).toBeChecked()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open my company' })).toBeEnabled())
  })

  it('hybrid with no tested CLI says what to do and cannot be saved', async () => {
    await toWork({ clis: [makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: null } })] })
    await userEvent.click(screen.getByRole('radio', { name: /^API \+ coding CLIs/ }))
    expect(await screen.findByText(/Install a coding CLI and run its test first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open my company' })).toBeDisabled()
    // ...but the person can still go on with API only
    expect(screen.getByRole('button', { name: 'Skip, use API only' })).toBeEnabled()
  })

  it('skipping means API only: nothing is saved when it already is, and a hybrid setup is reset', async () => {
    const hybrid = readyAiSetup({ work_mode: 'hybrid', work_runtime: 'cli:claude_code' })
    const api = backend({ ai: hybrid, clis: [makeCli({ auth: { mode: 'api_key', configured: true, credential_id: 'c', last_test: { ok: true, detail: 'OK', tested_at: null } } })] })
    const done = renderGate()
    await screen.findByRole('region', { name: 'Connected AI' })
    await userEvent.click(continueBtn())
    await screen.findByRole('heading', { name: 'How should work get done?' })
    expect(screen.getByRole('radio', { name: /^API \+ coding CLIs/ })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Skip, use API only' }))
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ work_mode: 'api', work_runtime: 'company_agent' })
  })

  it('degrades when the server does not report CLIs yet', async () => {
    await toWork({ clis: 'missing' })
    await userEvent.click(screen.getByRole('radio', { name: /^API \+ coding CLIs/ }))
    expect(await screen.findByText(/does not report coding CLIs yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Skip, use API only' })).toBeEnabled()
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
    expect(screen.queryByRole('button', { name: 'Test again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change provider or model' })).not.toBeInTheDocument()
  })
})
