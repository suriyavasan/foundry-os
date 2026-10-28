import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeProviderTest } from '@/test/m10'
import { loginStatus, makeConn, makeStack } from '@/test/m17'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { canContinue } from '@/lib/onboarding'
import { stackBlockingText, summarySentence } from '@/lib/aiStack'

afterEach(() => vi.unstubAllGlobals())

const A = makeConn({ id: 'k1', label: 'Anthropic API', position: 0, rank_label: 'Primary', use_for: 'heavy', share: 3 })
const B = makeConn({ id: 'k2', label: 'My OpenRouter', option: 'openrouter', provider_kind: 'openrouter', model: 'openai/gpt-4.1', position: 1, rank_label: 'Secondary', use_for: 'everyday', share: 0, state: 'needs_test' })

const routes = (stack: unknown, extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/ai-setup': { ready: true, blocking_reason: null, stack_count: 1, clis: [] },
  '/companies/c1/ai-stack': stack,
  '/companies/c1/model-providers': page([]),
  '/companies/c1/model-providers/detect': { reachable: false, models: [] },
  '/cli-runtimes': { items: [] },
  ...extra,
})

async function openPanel(r: Record<string, unknown>) {
  const api = mockApi(r)
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=ai')
  const dialog = await screen.findByRole('dialog', { name: 'AI settings' })
  return { api, dialog, user: userEvent.setup() }
}

const lastBody = (api: ReturnType<typeof mockApi>, method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path).at(-1)?.body

describe('the AI stack list', () => {
  it('shows rank labels, status, model and a plain-language summary', async () => {
    const stack = makeStack([A, B], {
      summary: { heavy: [{ connection_id: 'k1', label: 'Anthropic API', percent: 100, backup: false }], everyday: [{ connection_id: 'k2', label: 'My OpenRouter', percent: 0, backup: true }] },
    })
    const { dialog } = await openPanel(routes(stack))
    const first = await within(dialog).findByRole('listitem', { name: 'Primary: Anthropic API' })
    expect(within(first).getByText('Ready')).toBeInTheDocument()
    expect(within(first).getByText('claude-sonnet-4-5')).toBeInTheDocument()
    const second = within(dialog).getByRole('listitem', { name: 'Secondary: My OpenRouter' })
    expect(within(second).getByText('Needs a test')).toBeInTheDocument()
    expect(within(first).getByRole('combobox', { name: 'Use for, Anthropic API' })).toHaveValue('heavy')
    expect(within(first).getByRole('combobox', { name: 'Importance, Anthropic API' })).toHaveValue('3')
    expect(within(dialog).getByText('Heavy lifting: Anthropic API 100%.')).toBeInTheDocument()
    expect(within(dialog).getByText('Everyday work: My OpenRouter (backup).')).toBeInTheDocument()
  })

  it('has an empty state, a loading state and a not-available state', async () => {
    const { dialog } = await openPanel(routes(makeStack([])))
    expect(await within(dialog).findByText('No AI yet')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Add AI' })).toBeInTheDocument()
  })

  it('says "not available" on a backend without /ai-stack (404)', async () => {
    const r = routes(undefined)
    delete (r as Record<string, unknown>)['/companies/c1/ai-stack']
    const { dialog } = await openPanel(r)
    expect(await within(dialog).findByText(/not available on this server yet/)).toBeInTheDocument()
  })

  it('shows an error with retry when the server fails', async () => {
    const { dialog } = await openPanel(routes(() => jsonResponse({ error: { code: 'internal_error', message: 'boom', details: null } }, 500)))
    expect(await within(dialog).findByRole('button', { name: /Retry|Try again/ }, { timeout: 8000 })).toBeInTheDocument()
  }, 15_000)

  it('PATCHes Use for and Importance', async () => {
    const { api, dialog, user } = await openPanel(routes(makeStack([A, B]), { 'PATCH /companies/c1/ai-stack/k1': makeConn({ id: 'k1' }) }))
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Use for, Anthropic API' }), 'everyday')
    await waitFor(() => expect(lastBody(api, 'PATCH', '/companies/c1/ai-stack/k1')).toEqual({ use_for: 'everyday' }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Importance, Anthropic API' }), '0')
    await waitFor(() => expect(lastBody(api, 'PATCH', '/companies/c1/ai-stack/k1')).toEqual({ share: 0 }))
    await user.click(within(dialog).getByRole('switch', { name: 'Use Anthropic API' }))
    await waitFor(() => expect(lastBody(api, 'PATCH', '/companies/c1/ai-stack/k1')).toEqual({ enabled: false }))
  })

  it('moves up and down with buttons that PUT the whole order', async () => {
    const { api, dialog, user } = await openPanel(routes(makeStack([A, B]), { 'PUT /companies/c1/ai-stack/order': { items: [] } }))
    expect(await within(dialog).findByRole('button', { name: 'Move Anthropic API up' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Move My OpenRouter down' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Move My OpenRouter up' }))
    await waitFor(() => expect(lastBody(api, 'PUT', '/companies/c1/ai-stack/order')).toEqual({ ids: ['k2', 'k1'] }))
    await user.click(within(dialog).getByRole('button', { name: 'Move Anthropic API down' }))
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'PUT').length).toBe(2))
  })

  it('removes only after a confirmation', async () => {
    const { api, dialog, user } = await openPanel(routes(makeStack([A, B]), { 'DELETE /companies/c1/ai-stack/k2': () => new Response(null, { status: 204 }) }))
    await user.click((await within(dialog).findAllByRole('button', { name: 'Manage' }))[1]!)
    await user.click(within(dialog).getByRole('button', { name: 'Remove My OpenRouter' }))
    expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
    const confirm = await screen.findByRole('dialog', { name: 'Remove My OpenRouter?' })
    await user.click(within(confirm).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/ai-stack/k2')).toBe(true))
  })

  it('tests a connection from its row', async () => {
    const { api, dialog, user } = await openPanel(routes(makeStack([A, B]), { 'POST /companies/c1/ai-stack/k2/test': makeConn({ id: 'k2' }) }))
    const second = await within(dialog).findByRole('listitem', { name: 'Secondary: My OpenRouter' })
    await user.click(within(second).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/ai-stack/k2/test')).toBe(true))
  })

  it('read-only for a member: no add, no controls enabled', async () => {
    const r = { ...routes(makeStack([A])), '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } }
    const { dialog } = await openPanel(r)
    expect(await within(dialog).findByRole('combobox', { name: 'Use for, Anthropic API' })).toBeDisabled()
    expect(within(dialog).queryByRole('button', { name: 'Add AI' })).not.toBeInTheDocument()
  })
})

describe('Add AI', () => {
  it('groups the options and shows why one is disabled', async () => {
    const { dialog, user } = await openPanel(routes(makeStack([])))
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    const add = await screen.findByRole('dialog', { name: 'Add an AI' })
    expect(within(add).getByRole('region', { name: 'Sign in with your plan' })).toBeInTheDocument()
    expect(within(add).getByRole('region', { name: 'API keys' })).toBeInTheDocument()
    expect(within(add).getByRole('region', { name: 'On this computer' })).toBeInTheDocument()
    const cursor = within(add).getByRole('button', { name: /Cursor, your plan/ })
    expect(cursor).toBeDisabled()
    expect(cursor).toHaveTextContent('Already connected')
    expect(within(add).getByRole('button', { name: /Ollama/ })).toBeDisabled()
  })

  it('API option: creates the key, the provider, tests it, then adds the connection', async () => {
    const SECRET = 'sk-or-very-secret'
    const { api, dialog, user } = await openPanel(
      routes(makeStack([]), {
        'POST /companies/c1/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
        'POST /companies/c1/model-providers': { id: 'p9', name: 'OpenRouter', kind: 'openrouter', default_model: 'openai/gpt-4.1', enabled: true },
        '/companies/c1/model-providers/p9/models': { items: [] },
        'POST /companies/c1/model-providers/p9/test': makeProviderTest(),
        'POST /companies/c1/ai-stack': () => jsonResponse(makeConn({ id: 'k9', option: 'openrouter', state: 'ready' }), 201),
      }),
    )
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    const add = await screen.findByRole('dialog', { name: 'Add an AI' })
    await user.click(within(add).getByRole('button', { name: 'OpenRouter' }))
    const flow = await screen.findByRole('dialog', { name: 'Add OpenRouter' })
    await user.type(within(flow).getByLabelText(/OpenRouter API key/), SECRET)
    await user.type(within(flow).getByLabelText(/^Model/, { selector: 'input' }), 'openai/gpt-4.1')
    await user.click(within(flow).getByRole('button', { name: 'Test and add' }))
    await waitFor(() => expect(lastBody(api, 'POST', '/companies/c1/ai-stack')).toEqual({ option: 'openrouter', model_provider_id: 'p9', model: 'openai/gpt-4.1' }))
    const order = api.calls.filter((c) => c.method === 'POST').map((c) => c.path)
    expect(order.indexOf('/companies/c1/credentials')).toBeLessThan(order.indexOf('/companies/c1/model-providers'))
    expect(order.indexOf('/companies/c1/model-providers/p9/test')).toBeLessThan(order.indexOf('/companies/c1/ai-stack'))
    expect(JSON.stringify(api.calls.filter((c) => c.path !== '/companies/c1/credentials'))).not.toContain(SECRET)
  })

  it('API option: a failed test adds nothing', async () => {
    const { api, dialog, user } = await openPanel(
      routes(makeStack([]), {
        'POST /companies/c1/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
        'POST /companies/c1/model-providers': { id: 'p9', name: 'OpenRouter', kind: 'openrouter', default_model: 'm', enabled: true },
        '/companies/c1/model-providers/p9/models': { items: [] },
        'POST /companies/c1/model-providers/p9/test': makeProviderTest({ failing: 'auth' }),
      }),
    )
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: 'OpenRouter' }))
    const flow = await screen.findByRole('dialog', { name: 'Add OpenRouter' })
    await user.type(within(flow).getByLabelText(/OpenRouter API key/), 'k')
    await user.type(within(flow).getByLabelText(/^Model/, { selector: 'input' }), 'm')
    await user.click(within(flow).getByRole('button', { name: 'Test and add' }))
    expect(await within(flow).findByRole('alert')).toHaveTextContent(/auth failed/)
    expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/ai-stack')).toBe(false)
  })

  it('CLI API-key option: stores the key as a credential, then adds with credential_id and tests it', async () => {
    const SECRET = 'sk-codex-secret'
    const { api, dialog, user } = await openPanel(
      routes(makeStack([]), {
        'POST /companies/c1/credentials': { id: 'cr7', name: 'codex-key', kind: 'api_key' },
        'POST /companies/c1/ai-stack': () => jsonResponse(makeConn({ id: 'k7', option: 'codex_api', engine: 'cli', cli_id: 'codex', auth_mode: 'api_key', state: 'needs_test' }), 201),
        'POST /companies/c1/ai-stack/k7/test': makeConn({ id: 'k7', state: 'ready' }),
      }),
    )
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: /Codex/ }))
    const flow = await screen.findByRole('dialog', { name: /Add Codex/ })
    await user.type(within(flow).getByLabelText(/API key/), SECRET)
    await user.click(within(flow).getByRole('button', { name: 'Add and test' }))
    await waitFor(() => expect(lastBody(api, 'POST', '/companies/c1/ai-stack')).toEqual({ option: 'codex_api', credential_id: 'cr7', model: null }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/ai-stack/k7/test')).toBe(true))
    expect(JSON.stringify(api.calls.filter((c) => c.path !== '/companies/c1/credentials'))).not.toContain(SECRET)
  })

  it('Cursor API key: says where to get the key, stores it as a credential and adds a cursor_api connection', async () => {
    const SECRET = 'key-cursor-secret'
    const { api, dialog, user } = await openPanel(
      routes(makeStack([]), {
        'POST /companies/c1/credentials': { id: 'cr8', name: 'cursor-key', kind: 'api_key' },
        'POST /companies/c1/ai-stack': () => jsonResponse(makeConn({ id: 'k8', option: 'cursor_api', engine: 'cli', cli_id: 'cursor', auth_mode: 'api_key', state: 'needs_test' }), 201),
        'POST /companies/c1/ai-stack/k8/test': makeConn({ id: 'k8', state: 'ready' }),
      }),
    )
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: /Cursor API key/ }))
    const flow = await screen.findByRole('dialog', { name: /Add Cursor API key/ })
    expect(within(flow).getByText(/Cursor dashboard, under API Keys/)).toBeInTheDocument()
    await user.type(within(flow).getByLabelText(/API key/), SECRET)
    await user.type(within(flow).getByLabelText(/Model/), 'auto')
    await user.click(within(flow).getByRole('button', { name: 'Add and test' }))
    await waitFor(() => expect(lastBody(api, 'POST', '/companies/c1/credentials')).toMatchObject({ name: 'cursor-key', kind: 'api_key', value: SECRET }))
    await waitFor(() => expect(lastBody(api, 'POST', '/companies/c1/ai-stack')).toEqual({ option: 'cursor_api', credential_id: 'cr8', model: 'auto' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/ai-stack/k8/test')).toBe(true))
    expect(JSON.stringify(api.calls.filter((c) => c.path !== '/companies/c1/credentials'))).not.toContain(SECRET)
  })

  it('Claude plan: warns, has no credential inputs, adds first, then signs in with polling and a link', async () => {
    let conn = makeConn({ id: 'k5', label: 'Claude, your plan', option: 'claude_plan', engine: 'cli', cli_id: 'claude_code', auth_mode: 'plan', provider_kind: null, model_provider_id: null, model: null, state: 'needs_sign_in' })
    let added = false
    let login = loginStatus('idle')
    const { api, dialog, user } = await openPanel(
      routes(() => jsonResponse(makeStack(added ? [conn] : [])), {
        'POST /companies/c1/ai-stack': () => {
          added = true
          return jsonResponse(conn, 201)
        },
        '/companies/c1/ai-stack/k5/login': () => jsonResponse(login),
        'POST /companies/c1/ai-stack/k5/login': (_u: URL) => {
          login = loginStatus('running', { login: { state: 'running', started_at: null, finished_at: null, url: 'https://claude.example/sign-in', message: null } })
          return jsonResponse(login)
        },
      }),
    )
    await user.click(await within(dialog).findByRole('button', { name: 'Add AI' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: /Claude, your plan/ }))
    const flow = await screen.findByRole('dialog', { name: /Add Claude, your plan/ })
    expect(within(flow).getByText(/Personal plans assume ordinary individual use/)).toBeInTheDocument()
    expect(within(flow).queryByLabelText(/password|code|token|key/i)).not.toBeInTheDocument()
    expect(flow.querySelector('input')).toBeNull()
    await user.click(within(flow).getByRole('button', { name: 'Add Claude, your plan' }))
    await waitFor(() => expect(lastBody(api, 'POST', '/companies/c1/ai-stack')).toEqual({ option: 'claude_plan' }))
    await user.click(await within(flow).findByRole('button', { name: 'Sign in' }))
    expect(await within(flow).findByText('Finish signing in in the browser window that opened.')).toBeInTheDocument()
    expect(within(flow).getByRole('link', { name: /Open the sign-in page/ })).toHaveAttribute('href', 'https://claude.example/sign-in')
    expect(within(flow).getByText('foundryos ai login "Claude, your plan"')).toBeInTheDocument()
    expect(flow.querySelector('input')).toBeNull()
    // polling notices the sign-in finishing
    const before = api.calls.filter((c) => c.method === 'GET' && c.path === '/companies/c1/ai-stack/k5/login').length
    login = loginStatus('succeeded', { signed_in: true, email: 'me@example.com' })
    conn = { ...conn, status: { ...conn.status, state: 'ready', signed_in: true } }
    expect(await within(flow).findByText('Signed in', undefined, { timeout: 6000 })).toBeInTheDocument()
    expect(api.calls.filter((c) => c.method === 'GET' && c.path === '/companies/c1/ai-stack/k5/login').length).toBeGreaterThan(before)
  }, 15_000)
})

describe('plan rows', () => {
  it('a plan connection that needs sign-in shows the sign-in panel in its row', async () => {
    const plan = makeConn({ id: 'k5', label: 'Claude plan', option: 'claude_plan', engine: 'cli', cli_id: 'claude_code', auth_mode: 'plan', state: 'needs_sign_in' })
    const { dialog } = await openPanel(routes(makeStack([plan]), { '/companies/c1/ai-stack/k5/login': loginStatus('failed', { login: { state: 'failed', started_at: null, finished_at: null, url: null, message: 'timed out' } }) }))
    expect(await within(dialog).findByText(/Sign-in did not finish: timed out/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Try signing in again' })).toBeInTheDocument()
  })
})

describe('onboarding gating', () => {
  it('canContinue follows the server readiness and never blocks an old backend', () => {
    expect(canContinue('ai', { aiReady: false, aiAvailable: true })).toBe(false)
    expect(canContinue('ai', { aiReady: true, aiAvailable: true })).toBe(true)
    expect(canContinue('ai', { aiReady: false, aiAvailable: false })).toBe(true)
  })
  it('has plain text for each blocking reason and the summary', () => {
    expect(stackBlockingText('no_ai')).toMatch(/No AI yet/)
    expect(stackBlockingText('no_tested_ai')).toMatch(/Test your AI/)
    expect(stackBlockingText('no_heavy_ai')).toMatch(/heavy lifting/i)
    expect(stackBlockingText('no_everyday_ai')).toMatch(/everyday work/i)
    expect(summarySentence('Heavy lifting', [])).toBe('Heavy lifting: no AI covers this yet.')
  })
})
