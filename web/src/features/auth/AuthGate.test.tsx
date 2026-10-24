import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { AuthGate } from './AuthGate'

afterEach(() => vi.unstubAllGlobals())

const me = { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [] }
const unauth = () => jsonResponse({ error: { code: 'unauthenticated', message: 'Sign in required', details: null } }, 401)

describe('AuthGate', () => {
  it('shows the login screen on 401 and mounts nothing else (no 401 spam)', async () => {
    const api = mockApi({ '/me': unauth })
    vi.stubGlobal('fetch', api.impl)
    const child = vi.fn(() => <p>the app</p>)
    renderWithProviders(<AuthGate>{child()}</AuthGate>)

    expect(await screen.findByRole('heading', { name: /Open Foundry AI OS from the login link printed in your terminal/ })).toBeInTheDocument()
    expect(screen.getByText(/uv run uvicorn app.main:app --port 8700/)).toBeInTheDocument()
    expect(screen.getByText(/foundryos up/)).toBeInTheDocument()
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
    // A backend without accounts answers 404 on the session probe; then exactly one /me, not retried (M3b-sec flow).
    await new Promise((r) => setTimeout(r, 50))
    expect(api.calls.map((c) => c.path)).toEqual(['/auth/session', '/me'])
  })

  it('retries /me when asked and lets the app through once signed in', async () => {
    let signedIn = false
    const api = mockApi({ '/me': () => (signedIn ? jsonResponse(me) : unauth()) })
    vi.stubGlobal('fetch', api.impl)
    renderWithProviders(
      <AuthGate>
        <p>the app</p>
      </AuthGate>,
    )
    const retry = await screen.findByRole('button', { name: /try again/ })
    signedIn = true
    await userEvent.click(retry)
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(api.calls.filter((c) => c.path === '/me')).toHaveLength(2)
  })

  it('renders the app straight away when signed in', async () => {
    vi.stubGlobal('fetch', mockApi({ '/me': me }).impl)
    renderWithProviders(
      <AuthGate>
        <p>the app</p>
      </AuthGate>,
    )
    await waitFor(() => expect(screen.getByText('the app')).toBeInTheDocument())
    expect(screen.queryByText(/login link/)).not.toBeInTheDocument()
  })

  it('does not swallow other failures: a server error falls through to the app', async () => {
    vi.stubGlobal('fetch', mockApi({ '/me': () => jsonResponse({ error: { code: 'internal_error', message: 'boom', details: null } }, 500) }).impl)
    renderWithProviders(
      <AuthGate>
        <p>the app</p>
      </AuthGate>,
    )
    expect(await screen.findByText('the app')).toBeInTheDocument()
  })
})
