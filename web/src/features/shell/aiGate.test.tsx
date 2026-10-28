import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeAiSetup, makeProviderTest, readyAiSetup } from '@/test/m10'
import { makeConn, makeStack } from '@/test/m17'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const notReady = (over: Parameters<typeof makeAiSetup>[0] = {}) => makeAiSetup(over)
const routes = (ai: unknown, extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/ai-setup': ai,
  '/companies/c1/model-providers': page([]),
  '/companies/c1/model-providers/detect': { reachable: false, models: [] },
  '/companies/c1/ai-stack': makeStack([]),
  '/cli-runtimes': { items: [] },
  ...extra,
})

describe('the AI gate on the canvas', () => {
  it('blocks the canvas with the AI step when the AI is not ready', async () => {
    vi.stubGlobal('fetch', mockApi(routes(notReady())).impl)
    renderCanvas()
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 1 · Connect your AI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open my company' })).toBeDisabled()
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

  it('adds an AI in the gate, and Continue opens the company only once the stack is ready', async () => {
    let ai = notReady({ blocking_reason: 'no_ai' })
    let stack = makeStack([])
    const api = mockApi(
      routes(() => jsonResponse(ai), {
        '/companies/c1/ai-stack': () => jsonResponse(stack),
        'POST /companies/c1/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
        'POST /companies/c1/model-providers': { id: 'p1', name: 'OpenRouter', kind: 'openrouter', default_model: 'm', enabled: true },
        '/companies/c1/model-providers/p1/models': { items: [] },
        'POST /companies/c1/model-providers/p1/test': makeProviderTest(),
        'POST /companies/c1/ai-stack': () => {
          stack = makeStack([makeConn({ id: 'k1', option: 'openrouter', label: 'OpenRouter' })])
          ai = readyAiSetup()
          return jsonResponse(stack.items[0], 201)
        },
      }),
    )
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const user = userEvent.setup()
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText(/Add the AI your company will use/)).toBeInTheDocument()
    expect(await screen.findByText('No AI yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open my company' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add AI' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: 'OpenRouter' }))
    const flow = await screen.findByRole('dialog', { name: 'Add OpenRouter' })
    await user.type(within(flow).getByLabelText(/OpenRouter API key/), 'sk-or')
    await user.type(within(flow).getByLabelText(/^Model/, { selector: 'input' }), 'm')
    await user.click(within(flow).getByRole('button', { name: 'Test and add' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open my company' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Open my company' }))
    expect(await screen.findByRole('region', { name: 'Organization' })).toBeInTheDocument()
  })

  it('explains each stack blocking reason to a member who cannot change the setup', async () => {
    const reasons: Array<[string, RegExp]> = [
      ['no_ai', /No AI yet/],
      ['no_tested_ai', /Test your AI/],
      ['no_heavy_ai', /heavy lifting/i],
      ['no_everyday_ai', /everyday work/i],
    ]
    for (const [reason, text] of reasons) {
      const api = mockApi({ ...routes(notReady({ blocking_reason: reason as never })), '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } })
      vi.stubGlobal('fetch', api.impl)
      const r = renderCanvas()
      expect(await screen.findByRole('heading', { name: /waiting for its AI to be connected/ })).toBeInTheDocument()
      expect(screen.getByText(text)).toBeInTheDocument()
      expect(screen.getByText(/An owner or admin has to add an AI/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Add AI' })).not.toBeInTheDocument()
      r.unmount()
    }
  })
})
