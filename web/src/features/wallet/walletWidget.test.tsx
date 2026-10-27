import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { baseRoutes, deferred, loc, renderCanvas } from '@/test/m5'
import { activeWallet, catalog, emptyWallet, manualAccount } from './walletFixtures'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const tile = async () => screen.findByRole('region', { name: 'Agent cards' })

function mount(wallet: unknown, extra: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/wallet/providers': catalog, ...(wallet === undefined ? {} : { '/companies/c1/wallet': wallet }), ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas()
  return api
}

describe('Wallet widget on the canvas', () => {
  it('sits right after Spend in the grid', async () => {
    mount(emptyWallet)
    const wallet = await tile()
    const spend = screen.getByRole('region', { name: 'Spend' })
    expect(spend.nextElementSibling).toBe(wallet)
  })

  it('shows a loading state while the wallet loads', async () => {
    const gate = deferred<Response>()
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost')
      if (url.pathname === '/api/companies/c1/wallet') return gate.promise
      return mockApi({ ...baseRoutes }).impl(input, init)
    })
    renderCanvas()
    const w = await tile()
    expect(within(w).getByRole('status', { name: 'Loading agent cards' })).toBeInTheDocument()
    gate.resolve(jsonResponse(emptyWallet))
    await within(w).findByText("Agents can't buy anything yet.")
  })

  it('says it is not built yet on a backend without purchasing (404), with no numbers', async () => {
    mount(undefined)
    const w = await tile()
    await within(w).findByText(/Not built yet/)
    expect(within(w).getByText('Arrives in M15')).toBeInTheDocument()
    expect(within(w).queryByText(/\$\d/)).not.toBeInTheDocument()
  })

  it('says the same for { available: false, milestone }', async () => {
    mount({ ...emptyWallet, available: false, milestone: 'M15' })
    const w = await tile()
    await within(w).findByText(/Not built yet/)
    expect(within(w).getByText('Arrives in M15')).toBeInTheDocument()
  })

  it('shows an error with a working retry', async () => {
    let fail = true
    mount(() => (fail ? jsonResponse({ error: { code: 'internal_error', message: 'The wallet is down', details: null } }, 500) : jsonResponse(emptyWallet)))
    const w = await tile()
    await within(w).findByText('The wallet is down')
    fail = false
    await userEvent.setup().click(within(w).getByRole('button', { name: 'Retry' }))
    await within(w).findByText("Agents can't buy anything yet.")
  })

  it('not set up: explains, and the button opens the setup wizard', async () => {
    mount(emptyWallet)
    const w = await tile()
    expect(await within(w).findByText("Agents can't buy anything yet.")).toBeInTheDocument()
    expect(within(w).getByText(/becomes a task for you/)).toBeInTheDocument()
    await userEvent.setup().click(within(w).getByRole('button', { name: 'Set up agent cards' }))
    expect(loc()).toContain('panel=wallet')
    expect(loc()).toContain('tab=setup')
    const dialog = await screen.findByRole('dialog', { name: 'Agent cards' })
    expect(await within(dialog).findByText('Where is your company incorporated?')).toBeInTheDocument()
  })

  it('account connected but purchasing off: says so and offers to finish', async () => {
    mount({ ...emptyWallet, accounts: [manualAccount] })
    const w = await tile()
    expect(await within(w).findByText('Purchasing is off')).toBeInTheDocument()
    expect(within(w).getByRole('button', { name: 'Finish setup' })).toBeInTheDocument()
  })

  it('set up: manual card, month split between verified settled and committed, checkouts, approvals, renewals', async () => {
    mount(activeWallet)
    const w = await tile()
    await within(w).findByText('Manual card Visa ••1234')
    expect(within(w).getByText('Settled · verified')).toBeInTheDocument()
    expect(within(w).getByText('$24.50')).toBeInTheDocument()
    expect(within(w).getByText('Committed · not charged yet')).toBeInTheDocument()
    expect(within(w).getByText('$60.00')).toBeInTheDocument()
    expect(within(w).getByRole('img', { name: /\$24\.50 settled, \$60\.00 committed, of \$200\.00/ })).toBeInTheDocument()
    expect(within(w).getByText(/Awaiting your checkout \(1\)/)).toBeInTheDocument()
    expect(within(w).getByText('Analytics Pro plan')).toBeInTheDocument()
    expect(within(w).getByText(/plausible\.io · \$9\.00/)).toBeInTheDocument()
    expect(within(w).getByText('2')).toBeInTheDocument()
    expect(within(w).getByText(/purchase approvals pending/)).toBeInTheDocument()
    expect(within(w).getByText('Renewing soon')).toBeInTheDocument()
    expect(within(w).getByText(/2026-11-02/)).toBeInTheDocument()
    await userEvent.setup().click(within(w).getByRole('button', { name: 'Open checkout for Analytics Pro plan' }))
    expect(loc()).toContain('panel=task:t9')
  })

  it('shows a provider balance with its "as of" time', async () => {
    mount({ ...activeWallet, balance: { available_micros: 1_250_000_000, as_of: '2026-10-27T09:00:00Z', source: 'provider' }, accounts: [{ ...manualAccount, provider: 'stripe_issuing', label: 'Stripe Issuing agent cards' }] })
    const w = await tile()
    expect(await within(w).findByText('$1,250.00')).toBeInTheDocument()
    expect(within(w).getByText(/As of/)).toBeInTheDocument()
    expect(within(w).getByText(/reported by Stripe Issuing agent cards/)).toBeInTheDocument()
  })

  it('low balance: a banner with "How to add funds" that shows the live instructions and the guide', async () => {
    mount(
      { ...activeWallet, low_balance: true, accounts: [{ ...manualAccount, provider: 'stripe_issuing', label: 'Stripe Issuing agent cards' }], balance: { available_micros: 5_000_000, as_of: '2026-10-27T09:00:00Z', source: 'provider' } },
      { '/companies/c1/wallet/accounts/ca1/funding': { account_id: 'ca1', provider: 'stripe_issuing', instructions: { 'Routing number': '110000000' }, fetched_at: '2026-10-27T09:05:00Z' } },
    )
    const w = await tile()
    await within(w).findByText(/Balance is low/)
    await userEvent.setup().click(within(w).getByRole('button', { name: 'How to add funds' }))
    const dialog = await screen.findByRole('dialog', { name: /Add funds/ })
    expect(await within(dialog).findByText('110000000')).toBeInTheDocument()
    expect(within(dialog).getByText(/Not stored/)).toBeInTheDocument()
    expect(within(dialog).getByRole('group', { name: 'Stripe Issuing funding guide' })).toBeInTheDocument()
  })

  it('attention: a failing provider check is shown as an alert', async () => {
    mount({ ...activeWallet, attention: 'The Stripe check is failing: the key was revoked.' })
    const w = await tile()
    expect(await within(w).findByRole('alert')).toHaveTextContent('the key was revoked')
  })

  it('the expand button opens the panel on the overview', async () => {
    mount(activeWallet)
    const w = await tile()
    await userEvent.setup().click(within(w).getByRole('button', { name: 'Open agent cards' }))
    await waitFor(() => expect(loc()).toContain('panel=wallet'))
    expect(loc()).toContain('tab=overview')
  })
})
