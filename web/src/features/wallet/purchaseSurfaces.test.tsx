import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeApproval, makeMember, makeTask, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { activeWallet, makePurchase } from './walletFixtures'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const scope = {
  action: 'purchase.make',
  purchase_request_id: 'p1',
  vendor_domain: 'plausible.io',
  amount_micros: 9_000_000,
  recurrence: 'monthly',
  limit_micros: 10_000_000,
  merchant_lock: { domain: 'plausible.io', mcc_allow: [] },
}
const purchaseApproval = makeApproval({
  id: 'ap1',
  kind: 'external_purchase',
  title: 'Buy Analytics Pro plan',
  approver: 'human',
  amount_micros: 9_000_000,
  recurrence: 'monthly',
  vendor: 'Plausible',
  requested_by_agent_id: 'a2',
  scope,
})
const detail = { ...makePurchase({ status: 'pending_approval' }), approval: { id: 'ap1', status: 'pending', approver: 'human' }, card: null, transactions: [] }

describe('approval card for a purchase', () => {
  it('shows what is being bought: item, vendor domain, amount, recurring, limit, lock, agent and why', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([purchaseApproval]), '/companies/c1/purchases/p1': detail }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const box = await within(dialog).findByRole('group', { name: 'Purchase details for Buy Analytics Pro plan' })
    await within(box).findByText('Analytics Pro plan')
    expect(within(box).getByText('plausible.io', { selector: 'dd.font-semibold' })).toBeInTheDocument()
    expect(within(box).getByText('$9.00 / month')).toBeInTheDocument()
    expect(within(box).getByText('Recurring')).toBeInTheDocument()
    expect(within(box).getByText('$10.00')).toBeInTheDocument()
    expect(within(box).getByText('Card locked to')).toBeInTheDocument()
    expect(within(box).getByText(/Iris/)).toBeInTheDocument()
    expect(within(box).getByText(/privacy-friendly analytics/)).toBeInTheDocument()
    expect(within(box).getByText('Why (written by the agent)')).toBeInTheDocument()
    // the human decision buttons are still the approvals ones
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  })

  it('shows agent text as plain text, never as markup', async () => {
    const evil = { ...detail, item: '<img src=x onerror=alert(1)>', justification: '<script>alert(1)</script> and a long enough reason to buy it today' }
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([purchaseApproval]), '/companies/c1/purchases/p1': evil }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const box = await within(dialog).findByRole('group', { name: /Purchase details/ })
    await within(box).findByText('<img src=x onerror=alert(1)>')
    expect(box.querySelector('img, script')).toBeNull()
  })

  it('still shows the approved scope when the full request cannot be loaded', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([purchaseApproval]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const box = await within(dialog).findByRole('group', { name: /Purchase details/ })
    expect(within(box).getByText('plausible.io', { selector: 'dd.font-semibold' })).toBeInTheDocument()
    expect(await within(box).findByText(/could not be loaded/)).toBeInTheDocument()
  })

  it('adds nothing to any other approval, nor to a purchase approval without a request id', async () => {
    const other = makeApproval({ id: 'ap2', kind: 'hire', title: 'Hire a designer' })
    const bare = makeApproval({ id: 'ap3', kind: 'external_purchase', title: 'Buy something', scope: { action: 'purchase.make' } })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([other, bare]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    await within(dialog).findByText('Hire a designer')
    expect(within(dialog).queryByRole('group', { name: /Purchase details/ })).not.toBeInTheDocument()
  })
})

const checkoutTask = makeTask({ id: 't9', title: 'Buy Analytics Pro plan from plausible.io', assignee_ref: 'member:m-me', assignee_agent_id: null, status: 'in_progress' })
const taskRoutes = (extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/members': page([makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'owner' })]),
  '/companies/c1/tasks': page([checkoutTask]),
  '/companies/c1/tasks/t9': checkoutTask,
  '/companies/c1/approvals': page([]),
  '/companies/c1/purchases': page([makePurchase()]),
  '/companies/c1/wallet': activeWallet,
  ...extra,
})

describe('checkout packet in the task panel', () => {
  it('shows the vendor domain prominently, the amounts, identity, bring-back, steps and the card without a number', async () => {
    vi.stubGlobal('fetch', mockApi(taskRoutes()).impl)
    renderCanvas('/c/c1?panel=task:t9')
    const dialog = await screen.findByRole('dialog', { name: /Buy Analytics Pro plan/ })
    const packet = await within(dialog).findByRole('region', { name: 'Checkout packet' })
    expect(within(packet).getByTestId('vendor-domain')).toHaveTextContent('plausible.io')
    expect(within(packet).getByText('$9.00 / month')).toBeInTheDocument()
    expect(within(packet).getByText('$10.00')).toBeInTheDocument()
    expect(within(packet).getByText('ops@acme.example')).toBeInTheDocument()
    expect(within(packet).getByText(/analytics\.read/)).toBeInTheDocument()
    expect(within(packet).getByText(/Visa ••4242 · expires 12\/27/)).toBeInTheDocument()
    expect(within(packet).getAllByRole('listitem')).toHaveLength(5) // the default steps
    expect(within(packet).getByText(/written by an agent/)).toBeInTheDocument()
    expect(packet.textContent).not.toMatch(/\d{13,}/)
  })

  it('offers a link to the vendor only when the address is https on the packet domain', async () => {
    vi.stubGlobal('fetch', mockApi(taskRoutes()).impl)
    renderCanvas('/c/c1?panel=task:t9')
    const packet = await screen.findByRole('region', { name: 'Checkout packet' })
    const link = within(packet).getByRole('link', { name: /Open plausible\.io \(external site\)/ })
    expect(link).toHaveAttribute('href', 'https://plausible.io/pricing')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('does not link to a look-alike address, and says so', async () => {
    const phish = makePurchase({ checkout_packet: { ...makePurchase().checkout_packet!, url: 'https://plausible.io.evil.test/pay' } })
    vi.stubGlobal('fetch', mockApi(taskRoutes({ '/companies/c1/purchases': page([phish]) })).impl)
    renderCanvas('/c/c1?panel=task:t9')
    const packet = await screen.findByRole('region', { name: 'Checkout packet' })
    expect(within(packet).queryByRole('link')).not.toBeInTheDocument()
    expect(within(packet).getByText(/does not match that domain/)).toBeInTheDocument()
    expect(within(packet).getByText('https://plausible.io.evil.test/pay')).toBeInTheDocument()
  })

  it('"Reveal card" gives a provider link only, after you ask', async () => {
    const api = mockApi(taskRoutes({ '/companies/c1/wallet/cards/card1/reveal': { kind: 'provider_dashboard', url: 'https://dashboard.stripe.com/issuing/cards/ic_1', provider: 'stripe_issuing', note: 'Open it on Stripe.' } }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=task:t9')
    const packet = await screen.findByRole('region', { name: 'Checkout packet' })
    const user = userEvent.setup()
    await user.click(within(packet).getByRole('button', { name: 'Reveal card' }))
    const reveal = await screen.findByRole('dialog', { name: /Reveal Visa/ })
    await user.click(within(reveal).getByRole('button', { name: 'Get the link' }))
    expect(await within(reveal).findByRole('link', { name: /Open on Stripe Issuing/ })).toHaveAttribute('href', 'https://dashboard.stripe.com/issuing/cards/ic_1')
  })

  it('"Mark paid" records the amount you paid, as text, and validates it', async () => {
    const api = mockApi(taskRoutes({ 'POST /companies/c1/purchases/p1/mark-paid': { ...makePurchase({ status: 'settled' }) } }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=task:t9')
    const packet = await screen.findByRole('region', { name: 'Checkout packet' })
    const user = userEvent.setup()
    await user.click(within(packet).getByRole('button', { name: 'Mark paid' }))
    const dlg = await screen.findByRole('dialog', { name: 'Mark paid' })
    await user.click(within(dlg).getByRole('button', { name: 'Record payment' }))
    expect(await within(dlg).findByText(/Enter the amount charged in USD/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.path.endsWith('/mark-paid'))).toBe(false)
    await user.type(within(dlg).getByLabelText(/Amount charged/), '$9.45')
    await user.type(within(dlg).getByLabelText(/Billed by your bank/), '790.50')
    await user.type(within(dlg).getByLabelText('Note'), 'tax added')
    await user.click(within(dlg).getByRole('button', { name: 'Record payment' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/purchases/p1/mark-paid')?.body).toEqual({ merchant_amount_usd: '9.45', billed_amount_inr: '790.50', note: 'tax added' }))
  })

  it.each(['authorized', 'settled'] as const)('shows "Paid ✓" and no pay buttons once %s', async (status) => {
    vi.stubGlobal('fetch', mockApi(taskRoutes({ '/companies/c1/purchases': page([makePurchase({ status })]) })).impl)
    renderCanvas('/c/c1?panel=task:t9')
    const packet = await screen.findByRole('region', { name: 'Checkout packet' })
    expect(await within(packet).findByText(/Paid ✓/)).toBeInTheDocument()
    expect(within(packet).queryByRole('button', { name: 'Mark paid' })).not.toBeInTheDocument()
    expect(within(packet).queryByRole('button', { name: 'Reveal card' })).not.toBeInTheDocument()
  })

  it('shows nothing for a task that is not a checkout task, and on a backend without purchasing', async () => {
    vi.stubGlobal('fetch', mockApi(taskRoutes({ '/companies/c1/purchases': page([makePurchase({ checkout_task_id: 'other' })]) })).impl)
    renderCanvas('/c/c1?panel=task:t9')
    await screen.findByRole('dialog', { name: /Buy Analytics Pro plan/ })
    await screen.findByText('Brief')
    expect(screen.queryByRole('region', { name: 'Checkout packet' })).not.toBeInTheDocument()
  })

  it('shows the same packet under the task in My work', async () => {
    vi.stubGlobal('fetch', mockApi(taskRoutes()).impl)
    renderCanvas('/c/c1?panel=my-work')
    const dialog = await screen.findByRole('dialog', { name: 'My work' })
    const assigned = await within(dialog).findByRole('region', { name: 'Assigned to me' })
    const packet = await within(assigned).findByRole('region', { name: 'Checkout packet' })
    expect(within(packet).getByTestId('vendor-domain')).toHaveTextContent('plausible.io')
  })
})

describe('Purchases card in Finance', () => {
  const finance = (wallet: unknown) => ({ ...baseRoutes, ...(wallet === undefined ? {} : { '/companies/c1/wallet': wallet }) })

  it('shows settled (verified) apart from committed, and the budget', async () => {
    vi.stubGlobal('fetch', mockApi(finance(activeWallet)).impl)
    renderCanvas('/c/c1?panel=finance')
    const dialog = await screen.findByRole('dialog', { name: 'Finance' })
    const card = await within(dialog).findByRole('region', { name: 'Purchases' })
    await within(card).findByText('Settled this month')
    expect(within(card).getByText('$24.50')).toBeInTheDocument()
    expect(within(card).getByText('Verified')).toBeInTheDocument()
    expect(within(card).getByText('$60.00')).toBeInTheDocument()
    expect(within(card).getByText('Approved, not charged yet')).toBeInTheDocument()
    expect(within(card).getByText('$200.00')).toBeInTheDocument()
    await userEvent.setup().click(within(card).getByRole('button', { name: 'Open agent cards' }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Agent cards' })).toBeInTheDocument())
  })

  it('says it is not built on a backend without purchasing', async () => {
    vi.stubGlobal('fetch', mockApi(finance(undefined)).impl)
    renderCanvas('/c/c1?panel=finance')
    const dialog = await screen.findByRole('dialog', { name: 'Finance' })
    const card = await within(dialog).findByRole('region', { name: 'Purchases' })
    expect(await within(card).findByText('Arrives in M15')).toBeInTheDocument()
  })
})
