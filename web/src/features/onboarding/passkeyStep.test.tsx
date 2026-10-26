import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthGate } from '@/features/auth/AuthGate'
import { jsonResponse, makeCompany, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { creationJson, domError, installWebAuthn, uninstallWebAuthn } from '@/test/webauthn'

afterEach(() => uninstallWebAuthn())

const PASSWORD = 'violet-lantern-orbit-seven'
const user = { id: 'u1', username: 'maya', display_name: 'Maya Chen', email: null }
const company = makeCompany({ id: 'c9', name: 'Acme' })

function backend(setupSession: Record<string, unknown> = { passkey_supported_origin: true, passkeys_count: 0 }, over: Record<string, unknown> = {}) {
  let session: 'setup_required' | 'authenticated' = 'setup_required'
  const api = mockApi({
    '/auth/session': () => jsonResponse(session === 'setup_required' ? { state: session, user: null, setup_token_required: false } : { state: session, user }),
    'POST /auth/setup': () => {
      session = 'authenticated'
      return jsonResponse({ state: 'authenticated', user, ...setupSession })
    },
    '/me': { user: { id: 'u1', email: 'm@x', display_name: 'Maya Chen' }, memberships: [] },
    '/companies': page([company]),
    'POST /auth/passkeys/register/options': () => jsonResponse({ attempt_id: 'att1', public_key: creationJson }),
    'POST /auth/passkeys/register/verify': () =>
      jsonResponse({ id: 'k1', name: 'Mac · Chrome', created_at: '2026-10-24T09:00:00Z', last_used_at: null, transports: ['internal'], backed_up: true }, 201),
    ...over,
  })
  vi.stubGlobal('fetch', api.impl)
  return api
}

async function createAccount() {
  renderWithProviders(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  )
  const u = userEvent.setup()
  await u.click(await screen.findByRole('button', { name: /Start/ }))
  await u.type(await screen.findByLabelText(/Your name/), 'Maya Chen')
  await u.type(screen.getByLabelText(/^Password/), PASSWORD)
  await u.type(screen.getByLabelText(/Confirm password/), PASSWORD)
  await u.click(screen.getByRole('button', { name: 'Create account' }))
  return u
}

describe('onboarding: add a passkey', () => {
  it('offers a passkey after the account is created, and creates one', async () => {
    const wa = installWebAuthn()
    const api = backend()
    const u = await createAccount()
    expect(await screen.findByRole('heading', { name: 'Add a passkey (recommended)' })).toBeInTheDocument()
    expect(screen.getByText(/counts as two-factor sign-in/)).toBeInTheDocument()
    // the account exists: there is no way back to the account form
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: /Create a passkey/ }))
    expect(await screen.findByRole('heading', { name: 'Passkey added' })).toBeInTheDocument()
    expect(wa.create).toHaveBeenCalledTimes(1)
    const verify = api.calls.find((c) => c.path === '/auth/passkeys/register/verify')!.body as { attempt_id: string; name?: string; credential: { id: string; response: Record<string, unknown> } }
    expect(verify.attempt_id).toBe('att1')
    expect(verify.credential.id).toBe('cred-1')
    await u.click(screen.getByRole('button', { name: /Continue/ }))
    expect(await screen.findByLabelText(/Company name/)).toBeInTheDocument()
  })

  it('can be skipped, and nothing is registered', async () => {
    const wa = installWebAuthn()
    const api = backend()
    const u = await createAccount()
    await u.click(await screen.findByRole('button', { name: 'Skip for now' }))
    expect(await screen.findByLabelText(/Company name/)).toBeInTheDocument()
    expect(wa.create).not.toHaveBeenCalled()
    expect(api.calls.some((c) => c.path.includes('passkeys/register'))).toBe(false)
  })

  it('never blocks: a cancelled prompt leaves both choices open', async () => {
    installWebAuthn({ create: () => { throw domError('NotAllowedError') } })
    backend()
    const u = await createAccount()
    await u.click(await screen.findByRole('button', { name: /Create a passkey/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/cancelled or timed out/)
    expect(screen.getByRole('button', { name: /Create a passkey/ })).toBeEnabled()
    await u.click(screen.getByRole('button', { name: 'Skip for now' }))
    expect(await screen.findByLabelText(/Company name/)).toBeInTheDocument()
  })

  it('goes straight to the company when the backend has no passkeys', async () => {
    installWebAuthn()
    backend({})
    await createAccount()
    expect(await screen.findByLabelText(/Company name/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add a passkey (recommended)' })).not.toBeInTheDocument()
  })

  it('goes straight to the company when the browser cannot do passkeys', async () => {
    installWebAuthn(null)
    backend()
    await createAccount()
    expect(await screen.findByLabelText(/Company name/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add a passkey (recommended)' })).not.toBeInTheDocument()
  })

  it('goes straight to the company on an address passkeys cannot use', async () => {
    installWebAuthn()
    backend({ passkey_supported_origin: false, passkeys_count: 0 })
    await createAccount()
    await waitFor(() => expect(screen.getByLabelText(/Company name/)).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Add a passkey (recommended)' })).not.toBeInTheDocument()
  })
})
