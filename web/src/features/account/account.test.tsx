import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const PASSWORD = 'violet-lantern-orbit-seven'
const NEW_PASSWORD = 'amber-harbor-quiet-engine-9'
const SECRET_TOKEN = 'cos_pat_SUPERSECRETVALUE1234567890'
const user = { id: 'u', username: 'maya', display_name: 'Sam', email: 'sam@example.com', two_factor_enabled: false }
const noContent = () => new Response(null, { status: 204 })
const err = (status: number, code: string, message = 'x') => jsonResponse({ error: { code, message, details: null } }, status)

const sessions = [
  { id: 's1', created_at: '2026-10-20T09:00:00Z', last_seen_at: '2026-10-24T09:00:00Z', expires_at: '2026-11-03T09:00:00Z', ip: '10.0.0.5', user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36', current: true },
  { id: 's2', created_at: '2026-10-10T09:00:00Z', last_seen_at: '2026-10-12T09:00:00Z', expires_at: '2026-10-30T09:00:00Z', ip: '10.0.0.9', user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1', current: false },
]
const token = { id: 't1', name: 'laptop CLI', scope: 'read', prefix: 'cos_pat_ab12', created_at: '2026-10-20T09:00:00Z', last_used_at: null }

function routes(over: Record<string, unknown> = {}) {
  return {
    ...baseRoutes,
    '/auth/session': { state: 'authenticated', user },
    '/auth/sessions': sessions,
    '/auth/tokens': [token],
    ...over,
  }
}
const open = async () => screen.findByRole('dialog', { name: 'Account' })

describe('account panel', () => {
  it('says accounts are not built on a backend without them', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByText('Accounts are not built yet')).toBeInTheDocument()
    expect(within(d).getByText(/Arrives in M7/)).toBeInTheDocument()
    expect(within(d).queryByText('Change password')).not.toBeInTheDocument()
  })

  it('shows the profile and logs out', async () => {
    const api = mockApi(routes({ 'POST /auth/logout': noContent }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=account')
    const d = await open()
    expect(await within(d).findByText('@maya')).toBeInTheDocument()
    expect(within(d).getByText('sam@example.com')).toBeInTheDocument()
    await userEvent.click(within(d).getByRole('button', { name: 'Log out' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/auth/logout')).toBe(true))
  })

  describe('password', () => {
    it('validates, then changes it and clears the fields', async () => {
      const api = mockApi(routes({ 'POST /auth/password': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      const form = await within(d).findByRole('form', { name: 'Change password' })
      await userEvent.click(within(form).getByRole('button', { name: 'Change password' }))
      expect(await within(form).findByText('Enter your current password')).toBeInTheDocument()
      expect(api.calls.some((c) => c.path === '/auth/password')).toBe(false)

      await userEvent.type(within(form).getByLabelText(/Current password/), PASSWORD)
      await userEvent.type(within(form).getByLabelText(/^New password/), 'password1234')
      await userEvent.type(within(form).getByLabelText(/Confirm password/), 'password1234')
      await userEvent.click(within(form).getByRole('button', { name: 'Change password' }))
      expect(await within(form).findByText('That password is too common. Pick another.')).toBeInTheDocument()

      await userEvent.clear(within(form).getByLabelText(/^New password/))
      await userEvent.clear(within(form).getByLabelText(/Confirm password/))
      await userEvent.type(within(form).getByLabelText(/^New password/), NEW_PASSWORD)
      await userEvent.type(within(form).getByLabelText(/Confirm password/), NEW_PASSWORD)
      await userEvent.click(within(form).getByRole('button', { name: 'Change password' }))
      await waitFor(() => expect(api.calls.find((c) => c.path === '/auth/password')!.body).toEqual({ current_password: PASSWORD, new_password: NEW_PASSWORD }))
      await waitFor(() => expect(within(form).getByLabelText(/Current password/)).toHaveValue(''))
      expect(await screen.findByText('Password changed')).toBeInTheDocument()
    })

    it('shows the server’s refusal (wrong current password)', async () => {
      vi.stubGlobal('fetch', mockApi(routes({ 'POST /auth/password': () => err(401, 'unauthenticated', 'Current password is incorrect') })).impl)
      renderCanvas('/c/c1?panel=account')
      const form = await within(await open()).findByRole('form', { name: 'Change password' })
      await userEvent.type(within(form).getByLabelText(/Current password/), 'wrong')
      await userEvent.type(within(form).getByLabelText(/^New password/), NEW_PASSWORD)
      await userEvent.type(within(form).getByLabelText(/Confirm password/), NEW_PASSWORD)
      await userEvent.click(within(form).getByRole('button', { name: 'Change password' }))
      expect(await within(form).findByText('Current password is incorrect')).toBeInTheDocument()
    })
  })

  describe('sessions', () => {
    it('lists sessions with this device first and revokes another one', async () => {
      const api = mockApi(routes({ 'DELETE /auth/sessions/s2': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      expect(await within(d).findByText('Chrome on macOS')).toBeInTheDocument()
      expect(within(d).getByText('This device')).toBeInTheDocument()
      expect(within(d).getByText('Safari on iOS')).toBeInTheDocument()
      await userEvent.click(within(d).getByRole('button', { name: /Revoke session from Safari on iOS/ }))
      await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/auth/sessions/s2')).toBe(true))
    })

    it('logs out everywhere only after confirming', async () => {
      const api = mockApi(routes({ 'DELETE /auth/sessions': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Log out everywhere' }))
      expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
      const confirm = await screen.findByRole('dialog', { name: 'Log out everywhere?' })
      await userEvent.click(within(confirm).getByRole('button', { name: 'Log out everywhere' }))
      await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/auth/sessions')).toBe(true))
    })
  })

  describe('API tokens', () => {
    it('creates a token, shows the secret exactly once with a copy button, and never again', async () => {
      let created = false
      const api = mockApi(
        routes({
          '/auth/tokens': () => jsonResponse(created ? [token, { ...token, id: 't2', name: 'ci', scope: 'write', prefix: 'cos_pat_zz99' }] : [token]),
          'POST /auth/tokens': () => {
            created = true
            return jsonResponse({ ...token, id: 't2', name: 'ci', scope: 'write', prefix: 'cos_pat_zz99', token: SECRET_TOKEN }, 201)
          },
        }),
      )
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const u = userEvent.setup()
      const d = await open()
      await u.click(await within(d).findByRole('button', { name: 'Create token' }))
      const dialog = await screen.findByRole('dialog', { name: 'Create API token' })
      await u.click(within(dialog).getByRole('button', { name: 'Create token' }))
      expect(await within(dialog).findByText(/Name the token/)).toBeInTheDocument()
      await u.type(within(dialog).getByLabelText(/Name/), 'ci')
      await u.selectOptions(within(dialog).getByLabelText('Scope'), 'write')
      await u.click(within(dialog).getByRole('button', { name: 'Create token' }))

      const reveal = await within(d).findByTestId('token-reveal')
      expect(api.calls.find((c) => c.method === 'POST' && c.path === '/auth/tokens')!.body).toEqual({ name: 'ci', scope: 'write' })
      expect(within(reveal).getByLabelText('New API token')).toHaveValue(SECRET_TOKEN)
      expect(reveal).toHaveTextContent(/only time it is shown/)
      await u.click(within(reveal).getByRole('button', { name: 'Copy' }))
      expect(await navigator.clipboard.readText()).toBe(SECRET_TOKEN)
      expect(within(reveal).getByRole('button', { name: 'Copied' })).toBeInTheDocument()

      // the list shows the new token by name and prefix only
      expect((await within(d).findAllByText('ci')).length).toBeGreaterThanOrEqual(2) // the reveal and the list row
      expect(within(d).getByText(/cos_pat_zz99/)).toBeInTheDocument()

      await u.click(within(reveal).getByRole('button', { name: 'I have saved it' }))
      expect(within(d).queryByTestId('token-reveal')).not.toBeInTheDocument()
      expect(d.textContent).not.toContain(SECRET_TOKEN)
    })

    it('lists tokens and revokes after confirmation', async () => {
      const api = mockApi(routes({ 'DELETE /auth/tokens/t1': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      expect(await within(d).findByText('laptop CLI')).toBeInTheDocument()
      expect(within(d).getByText('Never')).toBeInTheDocument()
      await userEvent.click(within(d).getByRole('button', { name: 'Revoke token laptop CLI' }))
      const confirm = await screen.findByRole('dialog', { name: 'Revoke this token?' })
      expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
      await userEvent.click(within(confirm).getByRole('button', { name: 'Revoke token' }))
      await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/auth/tokens/t1')).toBe(true))
    })

    it('offers read, write and admin scopes, with a warning for admin', async () => {
      vi.stubGlobal('fetch', mockApi(routes()).impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Create token' }))
      const dialog = await screen.findByRole('dialog', { name: 'Create API token' })
      const scope = within(dialog).getByLabelText('Scope')
      expect(within(scope).getAllByRole('option').map((o) => o.textContent)).toEqual(['Read', 'Write', 'Admin'])
      await userEvent.selectOptions(scope, 'admin')
      expect(within(dialog).getByText(/Treat it like your password/)).toBeInTheDocument()
    })
  })

  describe('two-step verification', () => {
    const URI = 'otpauth://totp/Company%20OS:maya?secret=JBSWY3DPEHPK3PXP&issuer=Company%20OS'

    it('enrols: QR code and key, verify with a code, recovery codes shown once', async () => {
      const api = mockApi(
        routes({
          'POST /auth/2fa/enroll': () => jsonResponse({ secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: URI }),
          'POST /auth/2fa/verify': (_u: URL, init?: RequestInit) => (JSON.parse(String(init?.body)).code === '123456' ? jsonResponse({ enabled: true, recovery_codes: ['aaaa-1111', 'bbbb-2222'] }) : err(400, 'invalid_code', 'That code is not right')),
        }),
      )
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Set up two-step verification' }))
      expect(await within(d).findByRole('img', { name: 'QR code for your authenticator app' })).toBeInTheDocument()
      expect(within(d).getByTestId('totp-secret')).toHaveTextContent('JBSWY3DPEHPK3PXP')

      const form = within(d).getByRole('form', { name: 'Verify authenticator code' })
      await userEvent.click(within(form).getByRole('button', { name: 'Verify and turn on' }))
      expect(await within(form).findByText(/Enter the 6-digit code/)).toBeInTheDocument()
      await userEvent.type(within(form).getByLabelText(/^Code/), '000000')
      await userEvent.click(within(form).getByRole('button', { name: 'Verify and turn on' }))
      expect(await within(form).findByText('That code is not right')).toBeInTheDocument()
      expect(within(d).queryByTestId('recovery-codes')).not.toBeInTheDocument()

      await userEvent.clear(within(form).getByLabelText(/^Code/))
      await userEvent.type(within(form).getByLabelText(/^Code/), '123 456')
      await userEvent.click(within(form).getByRole('button', { name: 'Verify and turn on' }))
      const codes = await within(d).findByTestId('recovery-codes')
      expect(codes).toHaveTextContent('aaaa-1111')
      expect(api.calls.filter((c) => c.path === '/auth/2fa/verify').map((c) => c.body)).toEqual([{ code: '000000' }, { code: '123456' }])
      expect(within(d).queryByRole('img', { name: /QR code/ })).not.toBeInTheDocument()
      await userEvent.click(within(codes).getByRole('button', { name: 'I have saved them' }))
      expect(within(d).queryByTestId('recovery-codes')).not.toBeInTheDocument()
    })

    it('can be cancelled without enabling anything', async () => {
      const api = mockApi(routes({ 'POST /auth/2fa/enroll': () => jsonResponse({ secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: URI }) }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Set up two-step verification' }))
      await userEvent.click(await within(d).findByRole('button', { name: 'Cancel' }))
      expect(within(d).getByRole('button', { name: 'Set up two-step verification' })).toBeInTheDocument()
      expect(api.calls.some((c) => c.path === '/auth/2fa/verify')).toBe(false)
    })

    it('turns it off with the password and a current code', async () => {
      const api = mockApi(routes({ '/auth/session': { state: 'authenticated', user: { ...user, two_factor_enabled: true } }, 'POST /auth/2fa/disable': noContent }))
      vi.stubGlobal('fetch', api.impl)
      renderCanvas('/c/c1?panel=account')
      const d = await open()
      await userEvent.click(await within(d).findByRole('button', { name: 'Turn off' }))
      const dialog = await screen.findByRole('dialog', { name: 'Turn off two-step verification?' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Turn off' }))
      expect(await within(dialog).findByText(/Enter your password and a current 6-digit code/)).toBeInTheDocument()
      await userEvent.type(within(dialog).getByLabelText(/^Password/), PASSWORD)
      await userEvent.type(within(dialog).getByLabelText(/Authentication code/), '654321')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Turn off' }))
      await waitFor(() => expect(api.calls.find((c) => c.path === '/auth/2fa/disable')!.body).toEqual({ code: '654321', password: PASSWORD }))
    })
  })
})
