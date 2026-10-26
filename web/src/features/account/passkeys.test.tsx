import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EPHEMERAL_ORIGIN_MESSAGE, INSECURE_ORIGIN_MESSAGE } from '@/lib/webauthn'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { creationJson, domError, installWebAuthn, uninstallWebAuthn } from '@/test/webauthn'

afterEach(() => uninstallWebAuthn())

const user = { id: 'u', username: 'maya', display_name: 'Sam', email: 'sam@example.com', two_factor_enabled: true }
const err = (status: number, code: string, message = 'x', details: unknown = null) => jsonResponse({ error: { code, message, details } }, status)
const noContent = () => new Response(null, { status: 204 })

const synced = { id: 'k1', name: 'iCloud Keychain', created_at: '2026-10-20T09:00:00Z', last_used_at: '2026-10-23T09:00:00Z', transports: ['internal', 'hybrid'], backed_up: true }
const bound = { id: 'k2', name: 'YubiKey', created_at: '2026-10-10T09:00:00Z', last_used_at: null as string | null, transports: ['usb'], backed_up: false }

function routes(over: Record<string, unknown> = {}, session: Record<string, unknown> = { passkey_supported_origin: true, passkeys_count: 2 }) {
  return {
    ...baseRoutes,
    '/auth/session': { state: 'authenticated', user, ...session },
    '/auth/sessions': [],
    '/auth/tokens': [],
    '/auth/passkeys': page([synced, bound]),
    ...over,
  }
}
const open = async () => screen.findByRole('dialog', { name: 'Account' })

describe('passkeys in the account panel', () => {
  it('is absent on a backend without passkeys', async () => {
    installWebAuthn()
    const api = mockApi(routes({ '/auth/passkeys': undefined }, {}))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByRole('form', { name: 'Change password' })).toBeInTheDocument()
    await waitFor(() => expect(within(d).queryByRole('button', { name: 'Add a passkey' })).not.toBeInTheDocument())
    expect(api.calls.some((c) => c.method === 'GET' && c.path === '/auth/passkeys')).toBe(true)
    expect(within(d).queryByText('What is a passkey?')).not.toBeInTheDocument()
  })

  it('lists passkeys with their dates and synced badges, and explains what a passkey is', async () => {
    installWebAuthn()
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    const list = await within(d).findByRole('list', { name: 'Your passkeys' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByText('iCloud Keychain')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Synced')).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/last used/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText('This device only')).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/never used/)).toBeInTheDocument()
    expect(within(d).getByText('What is a passkey?')).toBeInTheDocument()
    expect(within(d).getByText(/Passkeys count as two-factor sign-in/)).toBeInTheDocument()
    expect(within(d).getByText(/you are not asked for an authenticator code/)).toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    installWebAuthn()
    vi.stubGlobal('fetch', mockApi(routes({ '/auth/passkeys': page([]) })).impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByText('No passkeys yet')).toBeInTheDocument()
  })

  it('adds a passkey: options, the browser ceremony, then verify with the name', async () => {
    const wa = installWebAuthn()
    const list = [synced as typeof bound]
    const api = mockApi(
      routes({
        '/auth/passkeys': () => jsonResponse(page(list)),
        'POST /auth/passkeys/register/options': () => jsonResponse({ attempt_id: 'att9', public_key: creationJson }),
        'POST /auth/passkeys/register/verify': () => {
          list.push(bound)
          return jsonResponse(bound, 201)
        },
      }),
    )
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Add a passkey' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a passkey' })
    const name = within(dialog).getByLabelText(/Name/)
    await userEvent.clear(name)
    await userEvent.type(name, 'Work laptop')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create passkey' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add a passkey' })).not.toBeInTheDocument())
    expect(api.calls.find((c) => c.path === '/auth/passkeys/register/options')!.body).toEqual({ name: 'Work laptop' })
    const create = wa.create.mock.calls[0]![0] as CredentialCreationOptions
    expect(create.publicKey!.challenge).toBeInstanceOf(ArrayBuffer)
    expect((create.publicKey!.user.id as ArrayBuffer).byteLength).toBe(3)
    const verify = api.calls.find((c) => c.path === '/auth/passkeys/register/verify')!.body as { attempt_id: string; name?: string; credential: { id: string; response: Record<string, unknown> } }
    expect(verify.attempt_id).toBe('att9')
    expect(verify.name).toBe('Work laptop')
    expect(verify.credential.response.attestationObject).toBeTruthy()
    expect(await within(d).findByText('YubiKey')).toBeInTheDocument()
  })

  it('keeps the dialog open with a plain message when the prompt is cancelled', async () => {
    installWebAuthn({ create: () => { throw domError('NotAllowedError') } })
    vi.stubGlobal(
      'fetch',
      mockApi(routes({ 'POST /auth/passkeys/register/options': () => jsonResponse({ attempt_id: 'a', public_key: creationJson }) })).impl,
    )
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Add a passkey' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a passkey' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create passkey' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/cancelled or timed out/)
  })

  it('requires a name', async () => {
    installWebAuthn()
    const api = mockApi(routes())
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Add a passkey' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a passkey' })
    await userEvent.clear(within(dialog).getByLabelText(/Name/))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create passkey' }))
    expect(await within(dialog).findByText('Give the passkey a name')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path.includes('register'))).toBe(false)
  })

  it('disables Add on an unsupported address and explains why, but still lists existing passkeys', async () => {
    installWebAuthn()
    vi.stubGlobal('fetch', mockApi(routes({}, { passkey_supported_origin: false, passkeys_count: 2 })).impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByText('iCloud Keychain')).toBeInTheDocument()
    expect(within(d).getByRole('button', { name: 'Add a passkey' })).toBeDisabled()
    expect(within(d).getByText(INSECURE_ORIGIN_MESSAGE)).toBeInTheDocument()
  })

  it('on a quick-tunnel address it disables Add and says passkeys need a stable address', async () => {
    installWebAuthn()
    vi.stubGlobal('fetch', mockApi(routes({}, { passkey_supported_origin: false, passkey_unsupported_reason: 'ephemeral_origin', passkeys_count: 2 })).impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByText('iCloud Keychain')).toBeInTheDocument()
    expect(within(d).getByRole('button', { name: 'Add a passkey' })).toBeDisabled()
    expect(within(d).getByText(EPHEMERAL_ORIGIN_MESSAGE)).toBeInTheDocument()
    expect(within(d).queryByText(INSECURE_ORIGIN_MESSAGE)).not.toBeInTheDocument()
  })

  it('renames a passkey', async () => {
    installWebAuthn()
    const api = mockApi(routes({ 'PATCH /auth/passkeys/k1': () => jsonResponse({ ...synced, name: 'Personal Mac' }) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Rename passkey iCloud Keychain' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rename passkey' })
    const name = within(dialog).getByLabelText(/Name/)
    expect(name).toHaveValue('iCloud Keychain')
    await userEvent.clear(name)
    await userEvent.type(name, 'Personal Mac')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save name' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({ name: 'Personal Mac' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Rename passkey' })).not.toBeInTheDocument())
  })

  describe('removing', () => {
    it('removes a passkey when no password is needed', async () => {
      installWebAuthn()
      const api = mockApi(routes({ 'DELETE /auth/passkeys/k2': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Remove passkey YubiKey' }))
      const dialog = await screen.findByRole('dialog', { name: 'Remove this passkey?' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/auth/passkeys/k2')).toBe(true))
      expect(api.calls.find((c) => c.method === 'DELETE')!.body).toBeUndefined()
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove this passkey?' })).not.toBeInTheDocument())
    })

    it('asks for the password on reauth_required and retries the same removal with it', async () => {
      installWebAuthn()
      const api = mockApi(
        routes({
          'DELETE /auth/passkeys/k1': (_u: URL, init?: RequestInit) => {
            const body = init?.body ? JSON.parse(String(init.body)) : {}
            return body.password === 'correct horse battery' ? noContent() : err(403, 'reauth_required', 'Confirm your password')
          },
        }),
      )
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Remove passkey iCloud Keychain' }))
      const dialog = await screen.findByRole('dialog', { name: 'Remove this passkey?' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      const password = await within(dialog).findByLabelText(/Password/)
      expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
      // an empty password is not sent
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('Enter your password')
      await userEvent.type(password, 'correct horse battery')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove this passkey?' })).not.toBeInTheDocument())
      const deletes = api.calls.filter((c) => c.method === 'DELETE')
      expect(deletes).toHaveLength(2)
      expect(deletes[0]!.body).toBeUndefined()
      expect(deletes[1]!.body).toEqual({ password: 'correct horse battery' })
    })

    it('says the password was wrong and clears it', async () => {
      installWebAuthn()
      vi.stubGlobal('fetch', mockApi(routes({ 'DELETE /auth/passkeys/k1': () => err(403, 'reauth_required', 'Confirm your password') })).impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Remove passkey iCloud Keychain' }))
      const dialog = await screen.findByRole('dialog', { name: 'Remove this passkey?' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      await userEvent.type(await within(dialog).findByLabelText(/Password/), 'nope')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('That password was not accepted')
      expect(within(dialog).getByLabelText(/Password/)).toHaveValue('')
    })

    it('explains that the last sign-in method cannot be removed', async () => {
      installWebAuthn()
      vi.stubGlobal('fetch', mockApi(routes({ '/auth/passkeys': page([synced]), 'DELETE /auth/passkeys/k1': () => err(409, 'last_sign_in_method', 'Last method') })).impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Remove passkey iCloud Keychain' }))
      const dialog = await screen.findByRole('dialog', { name: 'Remove this passkey?' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Remove passkey' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('This is the only way to sign in to your account, so it cannot be removed. Add another passkey first.')
      expect(screen.getByRole('dialog', { name: 'Remove this passkey?' })).toBeInTheDocument()
    })
  })
})
