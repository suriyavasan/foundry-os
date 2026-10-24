import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeAiSetup, readyAiSetup } from '@/test/m10'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const notReady = (over: Parameters<typeof makeAiSetup>[0] = {}) => makeAiSetup(over)
const routes = (ai: unknown, extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/ai-setup': ai,
  '/companies/c1/model-providers': page([]),
  '/companies/c1/model-providers/detect': { reachable: false, models: [] },
  '/cli-runtimes': { items: [] },
  ...extra,
})

describe('the AI gate on the canvas', () => {
  it('blocks the canvas with the two AI steps when the AI is not ready', async () => {
    vi.stubGlobal('fetch', mockApi(routes(notReady())).impl)
    renderCanvas()
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 2 · Connect your AI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
    // no canvas, no top bar, no widgets, no command palette
    expect(screen.queryByRole('region', { name: 'Organization' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open command palette' })).not.toBeInTheDocument()
    expect(screen.queryByText('Atlas')).not.toBeInTheDocument()
  })

  it('does not even load the canvas data while gated', async () => {
    const api = mockApi(routes(notReady()))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    await screen.findByRole('heading', { name: 'Connect your AI' })
    expect(api.calls.some((c) => c.path === '/companies/c1/agents' || c.path === '/companies/c1/tasks')).toBe(false)
  })

  it('opens the canvas when the AI is ready', async () => {
    vi.stubGlobal('fetch', mockApi(routes(readyAiSetup())).impl)
    renderCanvas()
    expect(await screen.findByRole('region', { name: 'Organization' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Connect your AI' })).not.toBeInTheDocument()
  })

  it('does not block on a backend that predates the AI setup (404), nor when the status cannot be read (500)', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    const first = renderCanvas()
    expect(await screen.findByRole('region', { name: 'Organization' })).toBeInTheDocument()
    first.unmount()

    vi.stubGlobal('fetch', mockApi(routes(() => jsonResponse({ error: { code: 'internal_error', message: 'boom', details: null } }, 500))).impl)
    renderCanvas()
    expect(await screen.findByRole('region', { name: 'Organization' }, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Connect your AI' })).not.toBeInTheDocument()
  }, 15_000)

  it('walks the two steps and then lets the person into the company, even though saving the model already made it ready', async () => {
    let ai = notReady()
    const api = mockApi(
      routes(() => jsonResponse(ai), {
        'POST /companies/c1/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
        'POST /companies/c1/model-providers': { id: 'p1', name: 'OpenRouter', kind: 'openrouter', default_model: 'm', enabled: true },
        '/companies/c1/model-providers/p1/models': { items: [] },
        'POST /companies/c1/model-providers/p1/test': {
          ok: true,
          served_context: 65536,
          latency_ms: 10,
          tested_at: null,
          checks: ['reachable', 'auth', 'tool_calling', 'context', 'model_exists'].map((name) => ({ name, ok: true, detail: null })),
        },
        'PUT /companies/c1/ai-setup': () => {
          ai = readyAiSetup()
          return jsonResponse(ai)
        },
      }),
    )
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
    await userEvent.type(screen.getByLabelText(/OpenRouter API key/), 'sk-key')
    await userEvent.type(screen.getByLabelText(/^Model\b/, { selector: 'input' }), 'm')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    // ready on the server now, but the gate stays until its own steps are done
    expect(screen.queryByRole('region', { name: 'Organization' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('heading', { name: 'How should work get done?' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open my company' }))
    expect(await screen.findByRole('region', { name: 'Organization' })).toBeInTheDocument()
  })

  it('tells a member who cannot change the setup who can, instead of showing the steps', async () => {
    const api = mockApi({ ...routes(notReady({ blocking_reason: 'provider_test_failed' })), '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    expect(await screen.findByRole('heading', { name: /waiting for its AI to be connected/ })).toBeInTheDocument()
    expect(screen.getByText(/did not pass/)).toBeInTheDocument()
    expect(screen.getByText(/An owner or admin has to connect and test an AI provider/)).toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Provider' })).not.toBeInTheDocument()
    expect(within(document.body).queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument()
  })
})
