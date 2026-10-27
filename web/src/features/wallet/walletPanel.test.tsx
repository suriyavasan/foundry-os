import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentCard, CardTransaction, Subscription } from '@/api/wallet'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { activeWallet, catalog, makePurchase, manualAccount } from './walletFixtures'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const card: AgentCard = {
  id: 'card1', company_id: 'c1', card_account_id: 'ca1', provider_card_id: 'ic_1', kind: 'single_use', status: 'active', purchase_request_id: 'p1', subscription_id: null, agent_id: 'a2',
  last4: '4242', brand: 'visa', exp_month: 12, exp_year: 2027, limit_micros: 10_000_000, limit_interval: 'per_authorization', merchant_lock: { domain: 'plausible.io', mcc_allow: [] },
  expires_at: '2026-10-30T09:00:00Z', frozen_reason: null, closed_at: null, created_at: '2026-10-27T09:00:00Z',
}
const txn: CardTransaction = {
  id: 'tx1', company_id: 'c1', card_account_id: 'ca1', card_id: 'card1', purchase_request_id: 'p1', subscription_id: null, provider_txn_id: 'ipi_1', kind: 'capture', status: 'settled', amount_micros: 9_000_000,
  cost_kind: 'verified', merchant_amount_minor: 900, merchant_currency: 'USD', merchant_name: 'PLAUSIBLE.IO', merchant_mcc: '5734', merchant_country: 'EE', match_status: 'matched', source: 'provider',
  occurred_at: '2026-10-27T10:00:00Z', settled_at: '2026-10-27T10:00:00Z', created_at: '2026-10-27T10:00:00Z',
}
const sub: Subscription = {
  id: 's1', company_id: 'c1', purchase_request_id: 'p1', agent_id: 'a2', agent_name: 'Iris', vendor: 'Plausible', vendor_domain: 'plausible.io', item: 'Analytics Pro plan', amount_micros: 9_000_000, recurrence: 'monthly',
  card_id: null, status: 'review_due', started_on: '2026-09-02', next_renewal_on: '2026-11-02', last_charged_on: '2026-10-02', last_charge_micros: 9_000_000, review_task_id: 'rt1', cancel_task_id: null, reviewed_at: null,
  created_at: '2026-09-02T09:00:00Z', updated_at: '2026-10-27T09:00:00Z',
}
const pending = makePurchase({
  id: 'p2', item: 'Design assets', vendor_domain: 'assets.example', status: 'pending_approval', decision: 'human', checkout_packet: null, card_id: null, held_micros: 0, checkout_task_id: null, approval_id: 'ap2', amount_micros: 40_000_000, recurrence: 'one_time',
})
const detail = (p = pending, over: Record<string, unknown> = {}) => ({ ...p, approval: { id: 'ap2', status: 'pending', approver: 'human' }, card: null, transactions: [], ...over })

const base = (extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/wallet': activeWallet,
  '/companies/c1/wallet/providers': catalog,
  '/companies/c1/purchases': page([pending, makePurchase()]),
  '/companies/c1/purchases/p2': detail(),
  '/companies/c1/purchases/p1': detail(makePurchase(), { approval: { id: 'ap1', status: 'approved', approver: 'human', decided_at: '2026-10-27T09:10:00Z' }, card, transactions: [txn] }),
  '/companies/c1/wallet/cards': page([card]),
  '/companies/c1/wallet/transactions': page([txn, { ...txn, id: 'tx2', source: 'human', merchant_name: null }]),
  '/companies/c1/subscriptions': page([sub]),
  ...extra,
})

async function openTab(tab: string, extra: Record<string, unknown> = {}) {
  const api = mockApi(base(extra))
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(`/c/c1?panel=wallet&tab=${tab}`)
  const dialog = await screen.findByRole('dialog', { name: 'Agent cards' })
  return { api, dialog, user: userEvent.setup() }
}

describe('Wallet panel', () => {
  it('has the six tabs and starts on the one named in the URL', async () => {
    const { dialog } = await openTab('cards')
    const tabs = within(dialog).getByRole('tablist', { name: 'Agent cards sections' })
    for (const name of [/Overview/, /Purchases/, 'Cards', 'Subscriptions', 'Transactions', 'Setup']) expect(within(tabs).getByRole('tab', { name })).toBeInTheDocument()
    expect(within(tabs).getByRole('tab', { name: 'Cards' })).toHaveAttribute('aria-selected', 'true')
    expect(within(tabs).getByRole('tab', { name: /Purchases \(2\)/ })).toBeInTheDocument()
  })

  it('says it is not built on a backend without purchasing', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes }).impl)
    renderCanvas('/c/c1?panel=wallet')
    const dialog = await screen.findByRole('dialog', { name: 'Agent cards' })
    expect(await within(dialog).findByText('Agent cards are not built yet')).toBeInTheDocument()
    expect(within(dialog).getByText('Arrives in M15')).toBeInTheDocument()
  })

  it('overview: accounts, with a live check and the default switch', async () => {
    const second = { ...manualAccount, id: 'ca2', label: 'Backup card', is_default: false }
    const { api, dialog, user } = await openTab('overview', {
      '/companies/c1/wallet': { ...activeWallet, accounts: [{ ...manualAccount, provider: 'stripe_issuing', label: 'Stripe cards' }, second] },
      'POST /companies/c1/wallet/accounts/ca1/check': { account_id: 'ca1', ok: false, message: 'Key revoked' },
      'POST /companies/c1/wallet/accounts/ca2/default': { ...second, is_default: true },
    })
    const accounts = await within(dialog).findByRole('region', { name: 'Accounts' })
    await user.click(within(accounts).getByRole('button', { name: 'Check Stripe cards' }))
    expect(await within(accounts).findByText(/Check failed\. Key revoked/)).toBeInTheDocument()
    await user.click(within(accounts).getByRole('button', { name: 'Make Backup card the default' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/wallet/accounts/ca2/default')).toBe(true))
  })

  it('purchases: filter, list as text, and a detail with the packet, card, transactions and timeline', async () => {
    const { api, dialog, user } = await openTab('purchases')
    const table = await within(dialog).findByRole('table')
    expect(within(table).getByText('Design assets')).toBeInTheDocument()
    expect(within(table).getByText('assets.example')).toBeInTheDocument()
    await user.selectOptions(within(dialog).getByLabelText('Filter purchases'), 'pending_approval')
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/purchases' && c.method === 'GET')).toBe(true))

    const refreshed = await within(dialog).findByRole('table')
    await user.click(within(refreshed).getByRole('button', { name: 'Details of Analytics Pro plan' }))
    const d = await screen.findByRole('dialog', { name: 'Analytics Pro plan' })
    expect(await within(d).findByRole('region', { name: 'Checkout packet' })).toBeInTheDocument()
    expect(within(d).getByText(/Visa ••4242/, { selector: 'p' })).toBeInTheDocument()
    expect(within(d).getByText(/Capture · PLAUSIBLE.IO · verified · provider/)).toBeInTheDocument()
    expect(within(d).getByText('Timeline')).toBeInTheDocument()
    expect(within(d).getByText(/privacy-friendly analytics/)).toBeInTheDocument()
  })

  it('purchases: a human approves a pending purchase with a note', async () => {
    const { api, dialog, user } = await openTab('purchases', { 'POST /companies/c1/purchases/p2/approve': detail() })
    await user.click(await within(dialog).findByRole('button', { name: 'Details of Design assets' }))
    const d = await screen.findByRole('dialog', { name: 'Design assets' })
    await user.click(await within(d).findByRole('button', { name: 'Approve' }))
    const confirm = await screen.findByRole('dialog', { name: /Approve: Design assets/ })
    expect(within(confirm).getByText(/This commits \$40\.00 to assets\.example/)).toBeInTheDocument()
    await user.type(within(confirm).getByLabelText(/Note/), 'Looks fine')
    await user.click(within(confirm).getByRole('button', { name: 'Approve purchase' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/purchases/p2/approve')?.body).toEqual({ note: 'Looks fine' }))
  })

  it('purchases: rejecting needs a reason', async () => {
    const { api, dialog, user } = await openTab('purchases', { 'POST /companies/c1/purchases/p2/reject': detail() })
    await user.click(await within(dialog).findByRole('button', { name: 'Details of Design assets' }))
    const d = await screen.findByRole('dialog', { name: 'Design assets' })
    await user.click(await within(d).findByRole('button', { name: 'Reject' }))
    const confirm = await screen.findByRole('dialog', { name: /Reject: Design assets/ })
    await user.click(within(confirm).getByRole('button', { name: 'Reject purchase' }))
    expect(await within(confirm).findAllByText('Say why it is rejected so the agent can act on it.')).not.toHaveLength(0)
    expect(api.calls.some((c) => c.path.endsWith('/reject'))).toBe(false)
    await user.type(within(confirm).getByLabelText(/Note/), 'Too expensive')
    await user.click(within(confirm).getByRole('button', { name: 'Reject purchase' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/purchases/p2/reject')?.body).toEqual({ note: 'Too expensive' }))
  })

  it('purchases: a purchase the AI Co-founder decides has no human Approve button', async () => {
    const { dialog, user } = await openTab('purchases', { '/companies/c1/purchases/p2': detail(pending, { approval: { id: 'ap2', status: 'pending', approver: 'founder' } }) })
    await user.click(await within(dialog).findByRole('button', { name: 'Details of Design assets' }))
    const d = await screen.findByRole('dialog', { name: 'Design assets' })
    await within(d).findByText(/The AI Co-founder decides this one/)
    expect(within(d).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('purchases: cancelling asks first', async () => {
    const { api, dialog, user } = await openTab('purchases', { 'POST /companies/c1/purchases/p2/cancel': detail() })
    await user.click(await within(dialog).findByRole('button', { name: 'Details of Design assets' }))
    const d = await screen.findByRole('dialog', { name: 'Design assets' })
    await user.click(await within(d).findByRole('button', { name: 'Cancel purchase' }))
    const confirm = await screen.findByRole('dialog', { name: 'Cancel this purchase?' })
    expect(api.calls.some((c) => c.path.endsWith('/cancel'))).toBe(false)
    await user.click(within(confirm).getByRole('button', { name: 'Cancel purchase' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/purchases/p2/cancel')).toBe(true))
  })

  it('purchases: empty and error states', async () => {
    const { dialog } = await openTab('purchases', { '/companies/c1/purchases': page([]) })
    expect(await within(dialog).findByText('No purchase requests yet')).toBeInTheDocument()
  })

  it('cards: only last four digits, freeze after confirmation, unfreeze at once, close after confirmation', async () => {
    const { api, dialog, user } = await openTab('cards', {
      'POST /companies/c1/wallet/cards/card1/freeze': { ...card, status: 'frozen' },
      'POST /companies/c1/wallet/cards/card1/unfreeze': card,
      'POST /companies/c1/wallet/cards/card1/close': { ...card, status: 'closed' },
    })
    const row = (await within(dialog).findByText(/Visa ••4242/)).closest('tr')!
    expect(within(row).getByText('plausible.io')).toBeInTheDocument()
    expect(within(row).getByText('$10.00')).toBeInTheDocument()
    expect(dialog.textContent).not.toMatch(/\d{13,}/)
    await user.click(within(row).getByRole('button', { name: 'Freeze Visa ••4242' }))
    const confirm = await screen.findByRole('dialog', { name: 'Freeze this card?' })
    await user.click(within(confirm).getByRole('button', { name: 'Freeze card' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/wallet/cards/card1/freeze')).toBe(true))
    await user.click(within(row).getByRole('button', { name: 'Close Visa ••4242' }))
    const close = await screen.findByRole('dialog', { name: 'Close this card?' })
    await user.click(within(close).getByRole('button', { name: 'Close card' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/wallet/cards/card1/close')).toBe(true))
  })

  it('cards: a frozen card can be unfrozen by a person', async () => {
    const { api, dialog, user } = await openTab('cards', { '/companies/c1/wallet/cards': page([{ ...card, status: 'frozen', frozen_reason: 'Emergency stop' }]), 'POST /companies/c1/wallet/cards/card1/unfreeze': card })
    expect(await within(dialog).findByText('Emergency stop')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Unfreeze Visa ••4242' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/wallet/cards/card1/unfreeze')).toBe(true))
  })

  it('cards: Reveal gives a link to the provider, never a number', async () => {
    const { api, dialog, user } = await openTab('cards', {
      '/companies/c1/wallet/cards/card1/reveal': { kind: 'provider_dashboard', url: 'https://dashboard.stripe.com/issuing/cards/ic_1', provider: 'stripe_issuing', note: 'Open the card on Stripe to see the number.' },
    })
    await user.click(await within(dialog).findByRole('button', { name: 'Reveal Visa ••4242' }))
    const reveal = await screen.findByRole('dialog', { name: 'Reveal Visa ••4242' })
    expect(api.calls.some((c) => c.path.endsWith('/reveal'))).toBe(false) // only on click
    await user.click(within(reveal).getByRole('button', { name: 'Get the link' }))
    const link = await within(reveal).findByRole('link', { name: /Open on Stripe Issuing/ })
    expect(link).toHaveAttribute('href', 'https://dashboard.stripe.com/issuing/cards/ic_1')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(reveal.textContent).not.toMatch(/\d{13,}/)
  })

  it('cards: when a recent sign-in is needed it says so and lets you try again', async () => {
    let calls = 0
    const { dialog, user } = await openTab('cards', {
      '/companies/c1/wallet/cards/card1/reveal': () => {
        calls += 1
        return calls === 1
          ? jsonResponse({ error: { code: 'forbidden', message: 'Confirm it is you', details: { reason: 'recent_login_required' } } }, 403)
          : jsonResponse({ kind: 'provider_dashboard', url: 'https://dashboard.stripe.com/x', provider: 'stripe_issuing', note: 'ok' })
      },
    })
    await user.click(await within(dialog).findByRole('button', { name: 'Reveal Visa ••4242' }))
    const reveal = await screen.findByRole('dialog', { name: 'Reveal Visa ••4242' })
    await user.click(within(reveal).getByRole('button', { name: 'Get the link' }))
    expect(await within(reveal).findByText(/Sign in again/)).toBeInTheDocument()
    await user.click(within(reveal).getByRole('button', { name: 'Try again' }))
    await within(reveal).findByRole('link', { name: /Open on/ })
  })

  it('cards: refuses to open an address that is not https', async () => {
    const { dialog, user } = await openTab('cards', { '/companies/c1/wallet/cards/card1/reveal': { kind: 'provider_dashboard', url: 'javascript:alert(1)', provider: 'stripe_issuing', note: 'x' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Reveal Visa ••4242' }))
    const reveal = await screen.findByRole('dialog', { name: 'Reveal Visa ••4242' })
    await user.click(within(reveal).getByRole('button', { name: 'Get the link' }))
    expect(await within(reveal).findByText(/not a plain https link/)).toBeInTheDocument()
    expect(within(reveal).queryByRole('link')).not.toBeInTheDocument()
  })

  it('subscriptions: renewal, review status, keep, and mark cancelled after confirmation', async () => {
    const { api, dialog, user } = await openTab('subscriptions', { 'POST /companies/c1/subscriptions/s1/keep': sub, 'POST /companies/c1/subscriptions/s1/cancelled': { ...sub, status: 'cancelled' } })
    const row = (await within(dialog).findByText('Analytics Pro plan')).closest('tr')!
    expect(within(row).getByText('Review due')).toBeInTheDocument()
    expect(within(row).getByText('2026-11-02')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Keep Plausible' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/subscriptions/s1/keep')).toBe(true))
    await user.click(within(row).getByRole('button', { name: 'Mark Plausible cancelled' }))
    const confirm = await screen.findByRole('dialog', { name: 'Mark Plausible as cancelled?' })
    expect(api.calls.some((c) => c.path.endsWith('/cancelled'))).toBe(false)
    await user.click(within(confirm).getByRole('button', { name: 'I cancelled it' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/subscriptions/s1/cancelled')).toBe(true))
  })

  it('transactions: cost kind and source badges, "confirmed by you" for a human entry', async () => {
    const { dialog } = await openTab('transactions')
    const table = await within(dialog).findByRole('table')
    expect(within(table).getAllByText('Verified')).toHaveLength(2)
    expect(within(table).getByText('provider')).toBeInTheDocument()
    expect(within(table).getByText('confirmed by you')).toBeInTheDocument()
    expect(within(table).getAllByText('Matched')).toHaveLength(2)
  })
})
