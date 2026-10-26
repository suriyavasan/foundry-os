import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { creationJson, domError, installWebAuthn, requestJson, setSecureContext, uninstallWebAuthn } from '@/test/webauthn'
import { EPHEMERAL_ORIGIN_MESSAGE, INSECURE_ORIGIN_MESSAGE, UNSUPPORTED_BROWSER_MESSAGE } from '@/lib/webauthn'
import { AuthGate } from './AuthGate'

afterEach(() => uninstallWebAuthn())

const user = { id: 'u1', username: 'maya', display_name: 'Maya Chen', email: null }
const err = (status: number, code: string, details: unknown = null) => jsonResponse({ error: { code, message: 'x', details } }, status)

function setup({ session = { passkey_supported_origin: true, passkeys_count: 0 }, verify = () => jsonResponse({ state: 'authenticated', user, passkeys_count: 1, passkey_supported_origin: true }), options = () => jsonResponse({ attempt_id: 'att1', public_key: requestJson }) }: {
  session?: Record<string, unknown>
  verify?: () => Response
  options?: () => Response
} = {}) {
  let signedIn = false
  const api = mockApi({
    '/auth/session': () => jsonResponse(signedIn ? { state: 'authenticated', user, ...session } : { state: 'unauthenticated', user: null, ...session }),
    'POST /auth/passkey/login/options': options,
    'POST /auth/passkey/login/verify': () => {
      const res = verify()
      if (res.status === 200) signedIn = true
      return res
    },
    '/me': { user: { id: 'u1', email: 'm@x', display_name: 'Maya Chen' }, memberships: [] },
  })
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  )
  return api
}
const passkeyButton = () => screen.findByRole('button', { name: 'Sign in with a passkey' })

describe('sign in with a passkey', () => {
  it('signs in without a username, sending the browser assertion to the server', async () => {
    const wa = installWebAuthn()
    const api = setup()
    await userEvent.click(await passkeyButton())
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path === '/auth/passkey/login/options')!.body).toEqual({})
    const verify = api.calls.find((c) => c.path === '/auth/passkey/login/verify')!.body as { attempt_id: string; credential: { id: string; response: Record<string, unknown> } }
    expect(verify.attempt_id).toBe('att1')
    expect(verify.credential.id).toBe('cred-1')
    expect(verify.credential.response.signature).toBeTruthy()
    // the browser got real buffers and no autofill mediation
    const opts = wa.get.mock.calls[0]![0] as CredentialRequestOptions
    expect(opts.publicKey!.challenge).toBeInstanceOf(ArrayBuffer)
    expect(opts.mediation).toBeUndefined()
  })

  it('sends the username when one is typed, and never asks for a password or a TOTP code', async () => {
    installWebAuthn()
    const api = setup()
    await userEvent.type(await screen.findByLabelText(/Username/), 'maya')
    await userEvent.click(await passkeyButton())
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path === '/auth/passkey/login/options')!.body).toEqual({ username: 'maya' })
    expect(screen.queryByLabelText(/Authentication code/)).not.toBeInTheDocument()
    expect(api.calls.some((c) => c.path === '/auth/login')).toBe(false)
  })

  it('makes the passkey button the primary action and keeps the password form', async () => {
    installWebAuthn()
    setup()
    const button = await passkeyButton()
    expect(button.className).toMatch(/bg-accent/)
    expect(screen.getByLabelText(/^Password/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' }).className).not.toMatch(/bg-accent /)
  })

  it('says so plainly when the prompt is cancelled, and can be retried', async () => {
    const wa = installWebAuthn({ get: () => { throw domError('NotAllowedError') } })
    setup()
    await userEvent.click(await passkeyButton())
    expect(await screen.findByRole('alert')).toHaveTextContent(/cancelled or timed out/)
    expect(screen.getByRole('button', { name: 'Sign in with a passkey' })).toBeEnabled()
    expect(wa.get).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
  })

  it('counts down a rate limit and disables the button', async () => {
    installWebAuthn()
    setup({ options: () => err(429, 'rate_limited', { retry_after_seconds: 30 }) })
    await userEvent.click(await passkeyButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many sign-in attempts. Try again in 30 seconds.')
    // both sign-in buttons wait, the passkey one included
    const waiting = screen.getAllByRole('button', { name: 'Try again in 30 seconds' })
    expect(waiting).toHaveLength(2)
    waiting.forEach((b) => expect(b).toBeDisabled())
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
  })

  it('gives a generic message when the server refuses the assertion', async () => {
    installWebAuthn()
    setup({ verify: () => err(401, 'unauthenticated') })
    await userEvent.click(await passkeyButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('That passkey was not accepted')
  })

  it('shows the browser-support message instead of a button when WebAuthn is missing', async () => {
    installWebAuthn(null)
    setup()
    expect(await screen.findByText(UNSUPPORTED_BROWSER_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('shows the insecure-origin message when the server says the address is unsupported', async () => {
    installWebAuthn()
    setup({ session: { passkey_supported_origin: false, passkeys_count: 0 } })
    expect(await screen.findByText(INSECURE_ORIGIN_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
  })

  it('on a quick-tunnel address it hides the passkey button and says passkeys need a stable address', async () => {
    installWebAuthn()
    setup({ session: { passkey_supported_origin: false, passkey_unsupported_reason: 'ephemeral_origin', passkeys_count: 0 } })
    expect(await screen.findByText(EPHEMERAL_ORIGIN_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
    expect(screen.queryByText(INSECURE_ORIGIN_MESSAGE)).not.toBeInTheDocument()
  })

  it('shows the insecure-origin message on a non-secure page even if the server did not object', async () => {
    installWebAuthn()
    setSecureContext(false)
    setup()
    expect(await screen.findByText(INSECURE_ORIGIN_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
  })

  it('hides every passkey surface on a backend that has none', async () => {
    const wa = installWebAuthn({ conditional: true })
    const api = setup({ session: {} })
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument()
    expect(screen.queryByText(UNSUPPORTED_BROWSER_MESSAGE)).not.toBeInTheDocument()
    expect(screen.queryByText(INSECURE_ORIGIN_MESSAGE)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Username/)).toHaveAttribute('autocomplete', 'username')
    expect(wa.get).not.toHaveBeenCalled()
    expect(api.calls.some((c) => c.path.includes('passkey'))).toBe(false)
  })

  it('hides the passkey UI when the options endpoint turns out to be missing', async () => {
    installWebAuthn()
    setup({ options: () => err(404, 'not_found') })
    await userEvent.click(await passkeyButton())
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })
})

describe('passkey autofill (conditional UI)', () => {
  it('starts a conditional request and marks the username field for autofill when the browser supports it', async () => {
    const wa = installWebAuthn({ conditional: true })
    setup()
    await waitFor(() => expect(wa.get).toHaveBeenCalledTimes(1))
    const opts = wa.get.mock.calls[0]![0] as CredentialRequestOptions
    expect(opts.mediation).toBe('conditional')
    expect(opts.signal).toBeInstanceOf(AbortSignal)
    expect(screen.getByLabelText(/Username/)).toHaveAttribute('autocomplete', 'username webauthn')
  })

  it('signs in when the person picks a passkey from the autofill list', async () => {
    const wa = installWebAuthn({ conditional: true, get: () => ({ ...{ id: 'cred-1', type: 'public-key', rawId: new Uint8Array([1]).buffer, response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer, signature: new Uint8Array([3]).buffer }, getClientExtensionResults: () => ({}) } }) })
    const api = setup()
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(wa.get).toHaveBeenCalled()
    expect(api.calls.some((c) => c.path === '/auth/passkey/login/verify')).toBe(true)
  })

  it('is not started where conditional mediation is unavailable', async () => {
    const wa = installWebAuthn({ conditional: false })
    setup()
    await passkeyButton()
    await new Promise((r) => setTimeout(r, 30))
    expect(wa.get).not.toHaveBeenCalled()
    expect(screen.getByLabelText(/Username/)).toHaveAttribute('autocomplete', 'username')
  })

  it('is aborted when the person clicks the button instead, then the modal prompt runs', async () => {
    let conditionalSignal: AbortSignal | undefined
    const wa = installWebAuthn({
      conditional: true,
      get: (o) => {
        if (o.mediation === 'conditional') {
          conditionalSignal = o.signal ?? undefined
          return new Promise((_r, reject) => o.signal?.addEventListener('abort', () => reject(domError('AbortError'))))
        }
        return { id: 'cred-1', type: 'public-key', rawId: new Uint8Array([1]).buffer, response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer, signature: new Uint8Array([3]).buffer }, getClientExtensionResults: () => ({}) }
      },
    })
    setup()
    await waitFor(() => expect(wa.get).toHaveBeenCalledTimes(1))
    await userEvent.click(await passkeyButton())
    expect(await screen.findByText('the app')).toBeInTheDocument()
    expect(conditionalSignal!.aborted).toBe(true)
    expect(wa.get.mock.calls.some(([o]) => (o as CredentialRequestOptions).mediation === undefined)).toBe(true)
  })

  it('stays quiet when the autofill request ends without a choice', async () => {
    installWebAuthn({ conditional: true, get: () => { throw domError('NotAllowedError') } })
    setup()
    await passkeyButton()
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

// keep the shared fixture referenced so a rename in the helper fails here too
void creationJson
