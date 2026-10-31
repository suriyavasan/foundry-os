import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthGate } from '@/features/auth/AuthGate'
import { jsonResponse, makeAgent, makeCompany, mockApi, page } from '@/test/factories'
import { makeAiSetup, makeProviderTest, readyAiSetup } from '@/test/m10'
import { makeConn, makeStack } from '@/test/m17'
import { renderWithProviders } from '@/test/render'

afterEach(() => vi.unstubAllGlobals())

const PASSWORD = 'violet-lantern-orbit-seven'
const user = { id: 'u1', username: 'maya', display_name: 'Maya Chen', email: null }
const company = makeCompany({ id: 'c9', name: 'Acme' })

function backend() {
  let session: 'setup_required' | 'authenticated' = 'setup_required'
  let ai = makeAiSetup()
  let stack = makeStack([])
  const order: string[] = []
  const api = mockApi({
    '/auth/session': () => jsonResponse(session === 'setup_required' ? { state: session, user: null, setup_token_required: false } : { state: session, user }),
    'POST /auth/setup': () => {
      session = 'authenticated'
      return jsonResponse({ state: 'authenticated', user })
    },
    '/me': { user: { id: 'u1', email: 'm@x', display_name: 'Maya Chen' }, memberships: [{ company_id: 'c9', company_name: 'Acme', role: 'owner' }] },
    '/companies': page([company]),
    'POST /companies': () => jsonResponse(company, 201),
    '/companies/c9/ai-setup': () => jsonResponse(ai),
    '/companies/c9/ai-stack': () => jsonResponse(stack),
    'POST /companies/c9/ai-stack': (_u: URL, init?: RequestInit) => {
      order.push(`POST ai-stack ${JSON.parse(String(init?.body)).option}`)
      stack = makeStack([makeConn({ id: 'k1', option: 'openrouter', label: 'OpenRouter' })])
      ai = readyAiSetup({ stack_count: 1 })
      return jsonResponse(stack.items[0], 201)
    },
    '/companies/c9/model-providers': page([]),
    '/companies/c9/model-providers/detect': { reachable: false, models: [] },
    'POST /companies/c9/credentials': { id: 'cr1', name: 'k', kind: 'api_key' },
    'POST /companies/c9/model-providers': { id: 'p1', name: 'OpenRouter', kind: 'openrouter', default_model: 'm', enabled: true },
    '/companies/c9/model-providers/p1/models': { items: [] },
    'POST /companies/c9/model-providers/p1/test': makeProviderTest(),
    '/cli-runtimes': { items: [] },
    'POST /companies/c9/notes': () => {
      order.push('POST notes')
      return jsonResponse({ id: 'n1' }, 201)
    },
    '/companies/c9/agents': page([
      makeAgent({ id: 'f1', company_id: 'c9', name: 'Atlas', kind: 'founder', role_title: 'Founder & CEO', status: 'active' }),
      makeAgent({ id: 'h1', company_id: 'c9', name: 'Hana', kind: 'specialist', role_title: 'HR Partner', role_family: 'hr', status: 'active' }),
    ]),
  })
  vi.stubGlobal('fetch', api.impl)
  return { api, order }
}

describe('first run, all six screens on an M10 backend', () => {
  it('Welcome -> account -> company -> connect your AI  -> where the code lives -> meet your team -> the app', async () => {
    const { api, order } = backend()
    renderWithProviders(
      <AuthGate>
        <p>the app</p>
      </AuthGate>,
    )
    const u = userEvent.setup()

    // 1 Welcome
    expect(await screen.findByRole('heading', { name: 'A company that runs itself.' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 7 · Welcome')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: /Start/ }))

    // 2 Account
    await u.type(await screen.findByLabelText(/Your name/), 'Maya Chen')
    await u.type(screen.getByLabelText(/^Password/), PASSWORD)
    await u.type(screen.getByLabelText(/Confirm password/), PASSWORD)
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument() // nothing has been created yet
    await u.click(screen.getByRole('button', { name: 'Create account' }))

    // 3 Company: from here on there is no way back to the account form
    await u.type(await screen.findByLabelText(/Company name/), 'Acme')
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    await u.type(screen.getByLabelText(/What is it going to do/), 'Sell lemonade')
    await u.click(screen.getByRole('button', { name: 'Create company' }))

    // 4 Connect your AI: mandatory
    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    expect(screen.getByText('Step 4 of 7 · Connect your AI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
    expect(api.calls.some((c) => c.path.endsWith('/notes'))).toBe(false) // the server would refuse a note now
    await u.click(await screen.findByRole('button', { name: 'Add AI' }))
    await u.click(within(await screen.findByRole('dialog', { name: 'Add an AI' })).getByRole('button', { name: 'OpenRouter' }))
    const flow = await screen.findByRole('dialog', { name: 'Add OpenRouter' })
    await u.type(within(flow).getByLabelText(/OpenRouter API key/), 'sk-or-secret')
    await u.type(within(flow).getByLabelText(/^Model/, { selector: 'input' }), 'anthropic/claude-sonnet-4.5')
    await u.click(within(flow).getByRole('button', { name: 'Test and add' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    expect(order).toEqual(['POST ai-stack openrouter'])
    await u.click(screen.getByRole('button', { name: 'Continue' }))

    // 5 Your code: the idea waits for this choice too, so the Co-founder sees the project with its repository
    expect(await screen.findByRole('heading', { name: 'Where should your code live?' })).toBeInTheDocument()
    expect(screen.getByText('Step 5 of 7 · Your code')).toBeInTheDocument()
    expect(order).toEqual(['POST ai-stack openrouter'])
    await u.click(screen.getByRole('button', { name: /No GitHub for now/ }))

    // 6 Chat apps (skippable): the first note was posted only now that the AI works and the code's home is chosen
    expect(await screen.findByRole('heading', { name: 'Talk to your company from your phone' })).toBeInTheDocument()
    expect(screen.getByText('Step 6 of 7 · Chat apps')).toBeInTheDocument()
    expect(order).toEqual(['POST ai-stack openrouter', 'POST notes'])
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument() // the repository step already ran
    await u.click(screen.getByRole('button', { name: 'Skip for now' }))

    // 7 Meet your team
    expect(await screen.findByRole('heading', { name: 'Meet your team' })).toBeInTheDocument()
    expect(screen.getByText('Step 7 of 7 · Meet your team')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path === '/companies/c9/notes')?.body).toEqual({ body: 'Sell lemonade' })
    const team = screen.getByRole('list', { name: 'Your first employees' })
    expect(await within(team).findByText('Atlas')).toBeInTheDocument()
    expect(within(team).getByText('AI Co-founder')).toBeInTheDocument()
    expect(within(team).getByText('Hana')).toBeInTheDocument()
    expect(screen.getByText('Sell lemonade')).toBeInTheDocument()
    expect(await screen.findByText('1 AI connected')).toBeInTheDocument()

    await u.click(screen.getByRole('button', { name: /Launch/ }))
    expect(await screen.findByText('the app')).toBeInTheDocument()
  }, 30_000)
})
