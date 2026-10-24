import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeCompany, mockApi } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { AuthGate } from './AuthGate'
import { InvitePage } from './InvitePage'

afterEach(() => vi.unstubAllGlobals())

const PASSWORD = 'violet-lantern-orbit-seven'
const user = { id: 'u1', username: 'maya', display_name: 'Maya Chen', email: null }
const authed = { state: 'authenticated', user }
const unauth = { state: 'unauthenticated', user: null }
const err = (status: number, code: string, message = 'x', details: unknown = null) => jsonResponse({ error: { code, message, details } }, status)
const me = { user: { id: 'u1', email: 'm@x', display_name: 'Maya Chen' }, memberships: [] }

function renderGate() {
  return renderWithProviders(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  )
}

describe('AuthGate on an M7 backend', () => {
  it('shows the app when the session is authenticated', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': authed, '/me': me }).impl)
    renderGate()
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument()
  })

  it('shows the login page when unauthenticated, and mounts nothing else', async () => {
    const api = mockApi({ '/auth/session': unauth })
    vi.stubGlobal('fetch', api.impl)
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 30))
    expect(api.calls.map((c) => c.path)).toEqual(['/auth/session'])
  })

  it('shows the setup wizard on a fresh install', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': { state: 'setup_required', user: null } }).impl)
    renderGate()
    // M10: the onboarding opens on a welcome screen; the account form is the second of six steps.
    expect(await screen.findByRole('heading', { name: 'A company that runs itself.' })).toBeInTheDocument()
    expect(screen.getByText(/Step 1 of 6/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Start/ }))
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    expect(screen.getByText(/Step 2 of 6/)).toBeInTheDocument()
  })

  it('keeps the owner-token flow on a backend without /auth/session', async () => {
    vi.stubGlobal('fetch', mockApi({ '/me': () => err(401, 'unauthenticated') }).impl)
    renderGate()
    expect(await screen.findByRole('heading', { name: /login link printed in your terminal/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument()
  })

  it('says so when the session endpoint itself fails', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': () => err(500, 'internal_error', 'boom') }).impl)
    renderGate()
    expect(await screen.findByText('Could not reach Foundry AI OS', {}, { timeout: 5000 })).toBeInTheDocument()
  })

  it('treats a 401 from the session probe as signed out', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': () => err(401, 'unauthenticated') }).impl)
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })
})

describe('login', () => {
  function setup(login: (body: Record<string, unknown>) => Response) {
    let signedIn = false
    const api = mockApi({
      '/auth/session': () => jsonResponse(signedIn ? authed : unauth),
      'POST /auth/login': (_u: URL, init?: RequestInit) => {
        const res = login(JSON.parse(String(init?.body)))
        if (res.status === 200) signedIn = true
        return res
      },
      '/me': me,
    })
    vi.stubGlobal('fetch', api.impl)
    renderGate()
    return api
  }

  it('signs in and swaps to the app', async () => {
    const api = setup(() => jsonResponse(authed))
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path === '/auth/login')!.body).toEqual({ username: 'maya', password: PASSWORD })
  })

  it('validates empty fields before calling the server', async () => {
    const api = setup(() => jsonResponse(authed))
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter your username')).toBeInTheDocument()
    expect(screen.getByText('Enter your password')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path === '/auth/login')).toBe(false)
  })

  it('gives the same generic error for a wrong password and clears the password', async () => {
    setup(() => err(401, 'unauthenticated', 'No user named maya'))
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), 'wrong-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
    expect(screen.queryByText(/No user named/)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/^Password/)).toHaveValue('')
    expect(screen.getByLabelText(/Username/)).toHaveValue('maya')
  })

  it('asks for the authenticator code when the account has 2FA, then sends it with the credentials', async () => {
    const bodies: Array<Record<string, unknown>> = []
    setup((body) => {
      bodies.push(body)
      return body.totp_code ? jsonResponse(authed) : jsonResponse({ state: 'unauthenticated', user: null, two_factor_required: true })
    })
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    const code = await screen.findByLabelText(/Authentication code/)
    expect(screen.queryByLabelText(/^Password/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText(/Enter the 6-digit code/)).toBeInTheDocument()
    await userEvent.type(code, '123 456')
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(bodies[1]).toEqual({ username: 'maya', password: PASSWORD, totp_code: '123456' })
  })

  it('also handles 2FA announced as an error code', async () => {
    setup((body) => (body.totp_code ? jsonResponse(authed) : err(401, 'two_factor_required')))
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByLabelText(/Authentication code/)).toBeInTheDocument()
  })

  it('tells the person a wrong code was not accepted, without leaving the code step', async () => {
    setup((body) => (body.totp_code ? err(401, 'unauthenticated') : err(401, 'two_factor_required')))
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await userEvent.type(await screen.findByLabelText(/Authentication code/), '000000')
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That code was not accepted')
    expect(screen.getByLabelText(/Authentication code/)).toBeInTheDocument()
  })

  it('disables the button and counts down when rate limited', async () => {
    setup(() => err(429, 'rate_limited', 'x', { retry_after_seconds: 45 }))
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many sign-in attempts\. Try again in 45 seconds\./)
    expect(screen.getByRole('button', { name: /Try again in 45 seconds/ })).toBeDisabled()
  })

  it('explains the CLI password reset, and that it does not exist yet', async () => {
    setup(() => jsonResponse(authed))
    await userEvent.click(await screen.findByRole('button', { name: /Forgot your password/ }))
    expect(screen.getByText(/foundryos auth reset-password/)).toBeInTheDocument()
    expect(screen.getByText(/ships with the CLI in M9 and does not exist yet/)).toBeInTheDocument()
  })
})

describe('setup wizard', () => {
  function setup({ tokenRequired = false, notes = true, setupBody }: { tokenRequired?: boolean; notes?: boolean; setupBody?: (b: Record<string, unknown>) => Response } = {}) {
    let state: 'setup_required' | 'authenticated' = 'setup_required'
    const company = makeCompany({ id: 'c9', name: 'Acme' })
    const api = mockApi({
      '/auth/session': () => jsonResponse(state === 'setup_required' ? { state, user: null, setup_token_required: tokenRequired } : authed),
      'POST /auth/setup': (_u: URL, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body))
        const res = setupBody ? setupBody(body) : jsonResponse(authed)
        if (res.status === 200) state = 'authenticated'
        return res
      },
      'POST /companies': () => jsonResponse(company, 201),
      ...(notes ? { 'POST /companies/c9/notes': () => jsonResponse({ id: 'n1' }, 201) } : null),
      'PATCH /companies/c9': () => jsonResponse(company),
      '/companies': { items: [company], next_cursor: null },
      '/me': me,
    })
    vi.stubGlobal('fetch', api.impl)
    renderGate()
    return api
  }

  /** Past the welcome screen to the account form. */
  async function yourName() {
    await userEvent.click(await screen.findByRole('button', { name: /Start/ }))
    return screen.findByLabelText(/Your name/)
  }

  async function fillAccountFields() {
    await userEvent.type(await screen.findByLabelText(/Your name/), 'Maya Chen')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.type(screen.getByLabelText(/Confirm password/), PASSWORD)
  }

  async function fillAccount() {
    await userEvent.click(await screen.findByRole('button', { name: /Start/ }))
    await fillAccountFields()
  }

  it('suggests a username from the name until the person edits it', async () => {
    setup()
    await userEvent.type(await yourName(), 'Maya Chen')
    expect(screen.getByLabelText(/^Username/)).toHaveValue('maya.chen')
    await userEvent.clear(screen.getByLabelText(/^Username/))
    await userEvent.type(screen.getByLabelText(/^Username/), 'mc')
    await userEvent.type(screen.getByLabelText(/Your name/), ' Jr')
    expect(screen.getByLabelText(/^Username/)).toHaveValue('mc')
  })

  it('shows a strength meter and the common-password warning, and blocks submitting it', async () => {
    const api = setup()
    await userEvent.type(await yourName(), 'Maya Chen')
    await userEvent.type(screen.getByLabelText(/^Password/), 'password1234')
    const meter = screen.getByRole('meter', { name: 'Password strength' })
    expect(meter).toHaveAttribute('aria-valuetext', expect.stringMatching(/Very weak|Weak/))
    expect(screen.getByText(/commonly used passwords/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Confirm password/), 'password1234')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText('That password is too common. Pick another.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path === '/auth/setup')).toBe(false)
  })

  it('requires 12 characters and matching confirmation', async () => {
    setup()
    await userEvent.type(await yourName(), 'Maya Chen')
    await userEvent.type(screen.getByLabelText(/^Password/), 'short')
    await userEvent.type(screen.getByLabelText(/Confirm password/), 'shorty')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText('Use at least 12 characters')).toBeInTheDocument()
    expect(screen.getByText('The passwords do not match')).toBeInTheDocument()
  })

  it('rejects a bad username with the rule', async () => {
    setup()
    await fillAccount()
    await userEvent.clear(screen.getByLabelText(/^Username/))
    await userEvent.type(screen.getByLabelText(/^Username/), 'Maya!')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText(/Use lowercase letters only/)).toBeInTheDocument()
  })

  it('shows the setup token field, with where to find it, only when the server requires it', async () => {
    setup({ tokenRequired: true })
    await yourName()
    expect(await screen.findByLabelText(/Setup token/)).toBeInTheDocument()
    expect(screen.getByText('data/owner.token')).toBeInTheDocument()
    expect(screen.getByText(/terminal where the backend started/)).toBeInTheDocument()
  })

  it('hides the setup token field on loopback', async () => {
    setup({ tokenRequired: false })
    await yourName()
    expect(screen.queryByLabelText(/Setup token/)).not.toBeInTheDocument()
  })

  it('reveals the field when the server refuses for a missing token anyway', async () => {
    setup({ setupBody: () => err(403, 'setup_token_required') })
    await fillAccount()
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByLabelText(/Setup token/)).toBeInTheDocument()
    expect(screen.getByText(/Enter the setup token printed by the server/)).toBeInTheDocument()
  })

  /** Account + company on a backend that predates the AI setup (every /ai-setup answers 404). */
  async function toAiStep(opts: { idea?: string } = {}) {
    await fillAccount()
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    await userEvent.type(await screen.findByLabelText(/Company name/), 'Acme')
    if (opts.idea) await userEvent.type(screen.getByLabelText(/What is it going to do/), opts.idea)
    await userEvent.click(screen.getByRole('button', { name: 'Create company' }))
    await screen.findByRole('heading', { name: 'Connect your AI' })
  }

  async function throughAiAndWork() {
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByRole('heading', { name: 'How should work get done?' })
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByRole('heading', { name: 'Meet your team' })
  }

  it('sends the token, then walks company -> AI -> work -> team -> the app, staying in the onboarding after sign-in', async () => {
    const api = setup({ tokenRequired: true })
    await userEvent.click(await screen.findByRole('button', { name: /Start/ }))
    await fillAccountFields()
    await userEvent.type(screen.getByLabelText(/Setup token/), '  tok-123  ')
    await userEvent.type(screen.getByLabelText(/^Email/), 'maya@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: 'Name your company' })).toBeInTheDocument()
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
    expect(api.calls.find((c) => c.path === '/auth/setup')!.body).toEqual({
      username: 'maya.chen',
      display_name: 'Maya Chen',
      password: PASSWORD,
      email: 'maya@example.com',
      setup_token: 'tok-123',
    })

    await userEvent.type(screen.getByLabelText(/Company name/), 'Acme')
    await userEvent.type(screen.getByLabelText(/What is it going to do/), 'A newsletter about research papers')
    await userEvent.click(screen.getByRole('button', { name: 'Create company' }))

    expect(await screen.findByRole('heading', { name: 'Connect your AI' })).toBeInTheDocument()
    const created = api.calls.find((c) => c.method === 'POST' && c.path === '/companies')!
    expect(created.body).toMatchObject({ name: 'Acme', founder: { name: 'Atlas' } })
    expect(typeof (created.body as { timezone: string }).timezone).toBe('string')
    // The server refuses notes until the AI works, so the idea waits until the work step is done.
    expect(api.calls.some((c) => c.path === '/companies/c9/notes')).toBe(false)
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)

    await throughAiAndWork()
    expect(api.calls.find((c) => c.path === '/companies/c9/notes')!.body).toEqual({ body: 'A newsletter about research papers' })
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: /Launch/ }))
    expect(await screen.findByText('the app')).toBeInTheDocument()
  })

  it('does not post a note when no idea was written', async () => {
    const api = setup()
    await toAiStep()
    await throughAiAndWork()
    expect(api.calls.some((c) => c.path.endsWith('/notes'))).toBe(false)
  })

  it('keeps the idea as the strategic goal when the backend has no notes yet', async () => {
    const api = setup({ notes: false })
    await toAiStep({ idea: 'Sell lemonade' })
    await throughAiAndWork()
    expect(api.calls.find((c) => c.method === 'PATCH' && c.path === '/companies/c9')!.body).toEqual({ strategic_goal: 'Sell lemonade' })
  })

  it('says plainly that this backend has no AI setup yet, and lets the person go on', async () => {
    setup()
    await toAiStep()
    expect(await screen.findByText(/AI setup is not available on this server yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
  })

  it('offers sign-in when someone else finished setup first', async () => {
    setup({ setupBody: () => err(409, 'conflict', 'already set up') })
    await fillAccount()
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/already has an owner account/)
    expect(screen.getByRole('button', { name: 'Go to sign in' })).toBeInTheDocument()
  })
})

describe('accept invitation', () => {
  const TOKEN = 'tok_abcdefghijklmnopqrstuv'
  function renderInvite() {
    return renderWithProviders(
      <Routes>
        <Route path="/invite/:token" element={<InvitePage />} />
        <Route path="/c/:companyId" element={<p>joined company</p>} />
        <Route path="/" element={<p>home</p>} />
      </Routes>,
      { route: `/invite/${TOKEN}` },
    )
  }

  it('lets a new person create an account and join', async () => {
    const api = mockApi({
      '/auth/session': unauth,
      [`POST /invitations/${TOKEN}/accept`]: () => jsonResponse({ company_id: 'c1', company_name: 'Acme', role: 'member' }),
    })
    vi.stubGlobal('fetch', api.impl)
    renderInvite()
    await userEvent.type(await screen.findByLabelText(/Your name/), 'Ben Ray')
    expect(screen.getByLabelText(/^Username/)).toHaveValue('ben.ray')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.type(screen.getByLabelText(/Confirm password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Create account and join' }))
    expect(await screen.findByText('joined company')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path.endsWith('/accept'))!.body).toEqual({ username: 'ben.ray', display_name: 'Ben Ray', password: PASSWORD })
  })

  it('validates the new account locally', async () => {
    const api = mockApi({ '/auth/session': unauth })
    vi.stubGlobal('fetch', api.impl)
    renderInvite()
    await userEvent.click(await screen.findByRole('button', { name: 'Create account and join' }))
    expect(await screen.findByText('Enter the name people should see')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path.endsWith('/accept'))).toBe(false)
  })

  it('lets an existing person sign in first, then joins', async () => {
    let signedIn = false
    const api = mockApi({
      '/auth/session': () => jsonResponse(signedIn ? authed : unauth),
      'POST /auth/login': () => {
        signedIn = true
        return jsonResponse(authed)
      },
      [`POST /invitations/${TOKEN}/accept`]: () => jsonResponse({ company_id: 'c1' }),
      '/me': me,
    })
    vi.stubGlobal('fetch', api.impl)
    renderInvite()
    await userEvent.click(await screen.findByRole('tab', { name: 'I have an account' }))
    await userEvent.type(screen.getByLabelText(/Username/), 'maya')
    await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD)
    await userEvent.click(screen.getByRole('button', { name: 'Sign in and join' }))
    expect(await screen.findByText('joined company')).toBeInTheDocument()
    const order = api.calls.filter((c) => c.method === 'POST').map((c) => c.path)
    expect(order).toEqual(['/auth/login', `/invitations/${TOKEN}/accept`])
    expect(api.calls.find((c) => c.path.endsWith('/accept'))!.body).toEqual({})
  })

  it('asks a signed-in person to confirm before joining', async () => {
    const api = mockApi({ '/auth/session': authed, [`POST /invitations/${TOKEN}/accept`]: () => jsonResponse({ company_id: 'c1' }) })
    vi.stubGlobal('fetch', api.impl)
    renderInvite()
    expect(await screen.findByText(/You are signed in as/)).toHaveTextContent('Maya Chen')
    expect(api.calls.some((c) => c.path.endsWith('/accept'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Accept invitation' }))
    expect(await screen.findByText('joined company')).toBeInTheDocument()
  })

  it('says one thing for any dead link', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': authed, [`POST /invitations/${TOKEN}/accept`]: () => err(410, 'gone', 'Invitation was revoked') }).impl)
    renderInvite()
    await userEvent.click(await screen.findByRole('button', { name: 'Accept invitation' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByRole('heading', { name: 'Invitation not valid' })).toBeInTheDocument()
    // the server's own wording ("was revoked") is not echoed: every dead link reads the same
    expect(screen.queryByText('Invitation was revoked')).not.toBeInTheDocument()
  })

  it('does not look up a token that cannot be one', async () => {
    const api = mockApi({ '/auth/session': unauth })
    vi.stubGlobal('fetch', api.impl)
    renderWithProviders(
      <Routes>
        <Route path="/invite/:token" element={<InvitePage />} />
      </Routes>,
      { route: '/invite/short' },
    )
    expect(await screen.findByRole('heading', { name: 'Invitation not valid' })).toBeInTheDocument()
  })

  it('degrades on a backend without accounts', async () => {
    vi.stubGlobal('fetch', mockApi({}).impl)
    renderInvite()
    expect(await screen.findByText('Invitations are not built yet')).toBeInTheDocument()
    expect(screen.getByText(/Arrives in M7/)).toBeInTheDocument()
  })

  it('is not behind the auth gate', async () => {
    vi.stubGlobal('fetch', mockApi({ '/auth/session': unauth }).impl)
    renderInvite()
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Create account' })).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument()
  })
})
