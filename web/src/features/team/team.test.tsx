import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeMember, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const noContent = () => new Response(null, { status: 204 })
const me = makeMember({ id: 'm-me', user_id: 'u', username: 'sam', display_name: 'Sam', role: 'owner', manager_ref: null })
const maya = makeMember({ id: 'm1', username: 'maya', display_name: 'Maya Chen', role: 'member', title: 'Designer', manager_ref: 'agent:f1' })
const vee = makeMember({ id: 'm2', username: 'vee', display_name: 'Vee Lo', role: 'viewer' })
const invitations = [
  { id: 'i1', company_id: 'c1', email: 'ben@example.com', role: 'member', title: 'Engineer', manager_ref: null, status: 'pending', expires_at: '2026-10-31T09:00:00Z', created_at: '2026-10-24T09:00:00Z' },
  { id: 'i2', company_id: 'c1', email: 'old@example.com', role: 'admin', title: null, manager_ref: null, status: 'expired', expires_at: '2026-10-01T09:00:00Z', created_at: '2026-09-24T09:00:00Z' },
  { id: 'i3', company_id: 'c1', email: 'done@example.com', role: 'member', title: null, manager_ref: null, status: 'accepted', expires_at: '2026-10-01T09:00:00Z', created_at: '2026-09-20T09:00:00Z' },
  { id: 'i4', company_id: 'c1', email: 'nope@example.com', role: 'viewer', title: null, manager_ref: null, status: 'revoked', expires_at: '2026-10-01T09:00:00Z', created_at: '2026-09-10T09:00:00Z' },
]

const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/members': page([me, maya, vee]),
  '/companies/c1/invitations': page(invitations),
  ...over,
})
const open = () => screen.findByRole('dialog', { name: 'Team' })

describe('team panel', () => {
  it('lists members with role, manager and status, and marks you', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    const row = (await within(d).findByText('Maya Chen')).closest('tr')!
    expect(within(row).getByText('@maya · Designer')).toBeInTheDocument()
    expect(within(row).getByText('Member')).toBeInTheDocument()
    expect(within(row).getByText('Atlas')).toBeInTheDocument() // manager_ref agent:f1
    expect(within(row).getByText('Active')).toBeInTheDocument()
    const own = within(d).getByText('Sam').closest('tr')!
    expect(within(own).getByText('You')).toBeInTheDocument()
    expect(within(own).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    expect(within(d).getAllByText('The Founder').length).toBeGreaterThan(0)
  })

  it('lists invitations with every status', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    const table = (await within(d).findByText('ben@example.com')).closest('table')!
    for (const label of ['Pending', 'Expired', 'Accepted', 'Revoked']) expect(within(table).getByText(label)).toBeInTheDocument()
    expect(within(table).getAllByRole('button', { name: /Revoke invitation/ })).toHaveLength(1)
  })

  it('revokes a pending invitation after confirming', async () => {
    const api = mockApi(routes({ 'DELETE /companies/c1/invitations/i1': noContent }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Revoke invitation for ben@example.com' }))
    expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
    const confirm = await screen.findByRole('dialog', { name: 'Revoke this invitation?' })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Revoke invitation' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/invitations/i1')).toBe(true))
  })

  it('changes role, title and manager', async () => {
    const api = mockApi(routes({ 'PATCH /companies/c1/members/m1': () => jsonResponse(maya) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Edit Maya Chen' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit Maya Chen' })
    await userEvent.selectOptions(within(dialog).getByLabelText('Role'), 'admin')
    await userEvent.clear(within(dialog).getByLabelText('Title'))
    await userEvent.type(within(dialog).getByLabelText('Title'), 'Head of Design')
    const manager = within(dialog).getByLabelText('Reports to')
    expect(within(manager).getByRole('group', { name: 'Team members' })).toBeInTheDocument()
    await userEvent.selectOptions(manager, 'member:m-me')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')!.body).toEqual({ role: 'admin', title: 'Head of Design', manager_ref: 'member:m-me' }))
  })

  it('does not offer to demote yourself and does not offer yourself as your own manager', async () => {
    vi.stubGlobal('fetch', mockApi(routes()).impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Edit Sam' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit Sam' })
    expect(within(dialog).getByLabelText('Role')).toBeDisabled()
    expect(within(within(dialog).getByLabelText('Reports to')).queryByRole('option', { name: /Sam/ })).not.toBeInTheDocument()
  })

  it('confirms removal and says what happens to their tasks', async () => {
    const api = mockApi(routes({ 'DELETE /companies/c1/members/m1': noContent }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Remove Maya Chen' }))
    const confirm = await screen.findByRole('dialog', { name: 'Remove Maya Chen?' })
    expect(confirm).toHaveTextContent(/open tasks are reassigned/)
    expect(api.calls.some((c) => c.method === 'DELETE')).toBe(false)
    await userEvent.click(within(confirm).getByRole('button', { name: 'Remove from company' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/members/m1')).toBe(true))
  })

  it('shows a server refusal when removing the last owner', async () => {
    vi.stubGlobal('fetch', mockApi(routes({ 'DELETE /companies/c1/members/m1': () => jsonResponse({ error: { code: 'conflict', message: 'A company needs at least one owner', details: null } }, 409) })).impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Remove Maya Chen' }))
    const confirm = await screen.findByRole('dialog', { name: 'Remove Maya Chen?' })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Remove from company' }))
    expect(await within(confirm).findByText('A company needs at least one owner')).toBeInTheDocument()
  })

  it('hides every management control from a plain member', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi(routes({ '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'member' }] } })).impl,
    )
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await within(d).findByText('Maya Chen')
    expect(within(d).queryByRole('button', { name: 'Invite' })).not.toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /^Edit / })).not.toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /Revoke/ })).not.toBeInTheDocument()
  })

  it('says teams are not built on a backend without the members endpoint', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    expect(await within(d).findByText('Teams are not built yet')).toBeInTheDocument()
    expect(within(d).getByText(/Arrives in M7/)).toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: 'Invite' })).not.toBeInTheDocument()
  })
})

describe('invite dialog', () => {
  async function openInvite(api: ReturnType<typeof mockApi>) {
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=team')
    const d = await open()
    await userEvent.click(await within(d).findByRole('button', { name: 'Invite' }))
    return screen.findByRole('dialog', { name: 'Invite a person' })
  }
  const created = (over: Record<string, unknown>) => ({ ...invitations[0], id: 'i9', email: 'zed@example.com', ...over })

  it('validates the email first', async () => {
    const api = mockApi(routes())
    const dialog = await openInvite(api)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create invitation' }))
    expect(await within(dialog).findByText('Enter an email address')).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'nope')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create invitation' }))
    expect(await within(dialog).findByText('Enter a valid email address')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('shows the copyable link when no email provider is connected', async () => {
    const api = mockApi(routes({ 'POST /companies/c1/invitations': () => jsonResponse(created({ delivery: 'link', token: 'tok_zzzzzzzzzzzzzzzzzzzz' }), 201) }))
    const dialog = await openInvite(api)
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'zed@example.com')
    await userEvent.selectOptions(within(dialog).getByLabelText('Role'), 'admin')
    await userEvent.type(within(dialog).getByLabelText('Title'), 'COO')
    await userEvent.selectOptions(within(dialog).getByLabelText('Reports to'), 'agent:f1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create invitation' }))
    expect(api.calls.find((c) => c.method === 'POST')!.body).toEqual({ email: 'zed@example.com', role: 'admin', title: 'COO', manager_ref: 'agent:f1' })

    const result = await screen.findByTestId('invite-result')
    expect(result).toHaveAttribute('data-delivery', 'link')
    expect(result).toHaveTextContent(/No email provider is connected, so nothing was sent/)
    const link = within(result).getByLabelText('Invitation link') as HTMLInputElement
    expect(link.value).toBe(`${window.location.origin}/invite/tok_zzzzzzzzzzzzzzzzzzzz`)
    expect(result).toHaveTextContent(/works once, for zed@example.com only, and expires in 7 days/)
    const u = userEvent.setup()
    await u.click(within(result).getByRole('button', { name: 'Copy link' }))
    expect(await navigator.clipboard.readText()).toBe(link.value)
  })

  it('says the email was sent, with no link, when an email provider delivered it', async () => {
    const api = mockApi(routes({ 'POST /companies/c1/invitations': () => jsonResponse(created({ delivery: 'email' }), 201) }))
    const dialog = await openInvite(api)
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'zed@example.com')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create invitation' }))
    const result = await screen.findByTestId('invite-result')
    expect(result).toHaveAttribute('data-delivery', 'email')
    expect(result).toHaveTextContent('An invitation email was sent to zed@example.com.')
    expect(within(result).queryByLabelText('Invitation link')).not.toBeInTheDocument()
    expect(within(result).queryByRole('button', { name: 'Copy link' })).not.toBeInTheDocument()
  })

  it('only an owner can invite another owner', async () => {
    const api = mockApi(routes())
    const dialog = await openInvite(api)
    expect(within(within(dialog).getByLabelText('Role')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Owner', 'Admin', 'Member', 'Viewer'])
  })
})
