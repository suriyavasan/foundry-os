import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'
import { catalog, emptyWallet, manualAccount } from './walletFixtures'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const routes = (extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/wallet': emptyWallet,
  '/companies/c1/wallet/providers': catalog,
  ...extra,
})

async function openWizard(extra: Record<string, unknown> = {}) {
  const api = mockApi(routes(extra))
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=wallet&tab=setup')
  const dialog = await screen.findByRole('dialog', { name: 'Agent cards' })
  await within(dialog).findByText('Where is your company incorporated?')
  return { api, dialog, user: userEvent.setup() }
}

async function answer(dialog: HTMLElement, user: ReturnType<typeof userEvent.setup>, kind: RegExp, opts: { mercury?: boolean } = {}) {
  await user.click(within(dialog).getByRole('radio', { name: kind }))
  if (opts.mercury) await user.click(within(dialog).getByRole('checkbox', { name: /already bank with Mercury/ }))
  await user.click(within(dialog).getByRole('button', { name: 'Next' }))
  return within(dialog).findByRole('group', { name: 'Recommendation' })
}

describe('Set up agent cards: recommendation by country', () => {
  it('needs an answer before it moves on', async () => {
    const { dialog } = await openWizard()
    expect(within(dialog).getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('recommends your own card to an India company, and says why', async () => {
    const { dialog, user } = await openWizard()
    const rec = await answer(dialog, user, /India company/)
    expect(within(rec).getByText('Your own card')).toBeInTheDocument()
    expect(within(rec).getByText('Recommended')).toBeInTheDocument()
    expect(within(rec).getByText(/No provider issues a programmable card to an India-incorporated company/)).toBeInTheDocument()
    expect(within(rec).getByText(/Works anywhere/)).toBeInTheDocument()
  })

  it.each([
    [/Not incorporated yet/, 'Your own card'],
    [/Somewhere else/, 'Your own card'],
  ])('recommends your own card for %s', async (kind, name) => {
    const { dialog, user } = await openWizard()
    const rec = await answer(dialog, user, kind)
    expect(within(rec).getByText(name)).toBeInTheDocument()
  })

  it('recommends Stripe Issuing to a US company, and Mercury if it already banks there', async () => {
    const first = await openWizard()
    const rec = await answer(first.dialog, first.user, /US company/)
    expect(within(rec).getByText('Stripe Issuing')).toBeInTheDocument()
    expect(within(first.dialog).getByRole('button', { name: 'Use Mercury instead' })).toBeInTheDocument()
    expect(within(first.dialog).queryByText('Test provider')).not.toBeInTheDocument()
  })

  it('recommends Mercury when the US company banks there', async () => {
    const { dialog, user } = await openWizard()
    const rec = await answer(dialog, user, /US company/, { mercury: true })
    expect(within(rec).getByText('Mercury')).toBeInTheDocument()
  })

  it('lets you pick another provider and shows that provider’s own guide', async () => {
    const { dialog, user } = await openWizard()
    await answer(dialog, user, /US company/)
    await user.click(within(dialog).getByRole('button', { name: 'Use Mercury instead' }))
    expect(within(dialog).getByText('You chose this one yourself.')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Show me what to do' }))
    const guide = await within(dialog).findByRole('group', { name: 'Mercury funding guide' })
    expect(within(guide).getByText(/Create an API token/)).toBeInTheDocument()
    expect(within(guide).getByText('Only you can do this')).toBeInTheDocument()
    expect(within(guide).getByText(/Not verified against the real Mercury API/)).toBeInTheDocument()
    await user.click(within(guide).getByRole('checkbox', { name: 'Done: Create an API token' }))
    expect(within(dialog).getByText(/1 of 1 done/)).toBeInTheDocument()
  })
})

describe('Set up agent cards: your own card', () => {
  async function toManualForm() {
    const ctx = await openWizard({ 'POST /companies/c1/wallet/accounts': manualAccount })
    await answer(ctx.dialog, ctx.user, /India company/)
    await ctx.user.click(within(ctx.dialog).getByRole('button', { name: 'Show me what to do' }))
    await ctx.user.click(await within(ctx.dialog).findByRole('button', { name: 'Connect' }))
    const form = await within(ctx.dialog).findByRole('form', { name: 'Register your own card' })
    return { ...ctx, form }
  }
  const posts = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/wallet/accounts')

  it('refuses a 16 digit number typed into the last 4 field, clears it and sends nothing', async () => {
    const { api, form, user } = await toManualForm()
    await user.type(within(form).getByLabelText(/Card name/), 'HDFC corporate card')
    await user.selectOptions(within(form).getByLabelText(/Card network/), 'Visa')
    await user.type(within(form).getByLabelText(/Last 4 digits/), '4242424242424242')
    await user.click(within(form).getByRole('button', { name: 'Register card' }))
    expect(await within(form).findByText(/A full card number is never accepted/)).toBeInTheDocument()
    expect(within(form).getByLabelText(/Last 4 digits/)).toHaveValue('')
    expect(posts(api)).toHaveLength(0)
  })

  it.each(['1234567890123', '4111 1111 1111 1111', '1234567890123456789'])('refuses %s (13 to 19 digits) in the card name too', async (number) => {
    const { api, form, user } = await toManualForm()
    await user.type(within(form).getByLabelText(/Card name/), number)
    await user.selectOptions(within(form).getByLabelText(/Card network/), 'Visa')
    await user.type(within(form).getByLabelText(/Last 4 digits/), '1234')
    await user.click(within(form).getByRole('button', { name: 'Register card' }))
    expect(await within(form).findByText(/A full card number is never accepted/)).toBeInTheDocument()
    expect(within(form).getByLabelText(/Card name/)).toHaveValue('')
    expect(posts(api)).toHaveLength(0)
  })

  it('wants exactly four digits', async () => {
    const { api, form, user } = await toManualForm()
    await user.type(within(form).getByLabelText(/Card name/), 'HDFC')
    await user.selectOptions(within(form).getByLabelText(/Card network/), 'Visa')
    await user.type(within(form).getByLabelText(/Last 4 digits/), '123')
    await user.click(within(form).getByRole('button', { name: 'Register card' }))
    expect(await within(form).findByText('Exactly 4 digits')).toBeInTheDocument()
    expect(posts(api)).toHaveLength(0)
  })

  it('registers a card with only label, brand and last 4, then moves to the limits', async () => {
    const { api, dialog, form, user } = await toManualForm()
    await user.type(within(form).getByLabelText(/Card name/), 'HDFC corporate card')
    await user.selectOptions(within(form).getByLabelText(/Card network/), 'Visa')
    await user.type(within(form).getByLabelText(/Last 4 digits/), '1234')
    await user.click(within(form).getByRole('button', { name: 'Register card' }))
    await within(dialog).findByRole('form', { name: 'Purchase limits' })
    expect(posts(api)).toHaveLength(1)
    expect(posts(api)[0]!.body).toEqual({ provider: 'manual', label: 'HDFC corporate card', mode: 'live', manual_brand: 'Visa', manual_last4: '1234', make_default: true })
  })
})

describe('Set up agent cards: limits', () => {
  it('turns purchasing on with the suggested limits and the plan’s safe defaults', async () => {
    const { api, dialog, user } = await openWizard({
      'POST /companies/c1/wallet/accounts': manualAccount,
      'PATCH /companies/c1/policy': {},
    })
    await answer(dialog, user, /India company/)
    await user.click(within(dialog).getByRole('button', { name: 'Show me what to do' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Connect' }))
    const form = await within(dialog).findByRole('form', { name: 'Register your own card' })
    await user.type(within(form).getByLabelText(/Card name/), 'HDFC')
    await user.selectOptions(within(form).getByLabelText(/Card network/), 'Visa')
    await user.type(within(form).getByLabelText(/Last 4 digits/), '1234')
    await user.click(within(form).getByRole('button', { name: 'Register card' }))
    const limits = await within(dialog).findByRole('form', { name: 'Purchase limits' })
    expect(within(limits).getByLabelText(/Approve automatically up to/)).toHaveValue('0')
    expect(within(limits).getByLabelText(/AI Co-founder may approve up to/)).toHaveValue('50')
    expect(within(limits).getByLabelText(/AI Co-founder total per month/)).toHaveValue('200')
    expect(within(limits).getByLabelText(/Starting a subscription needs me/)).toBeChecked()
    expect(within(limits).getByLabelText(/A new vendor needs me/)).toBeChecked()

    // a value above the code ceiling is refused here, before any request
    await user.clear(within(limits).getByLabelText(/Approve automatically up to/))
    await user.type(within(limits).getByLabelText(/Approve automatically up to/), '500')
    await user.click(within(limits).getByRole('button', { name: 'Turn on purchasing' }))
    expect(await within(limits).findByText('At most $100')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'PATCH')).toBe(false)

    await user.clear(within(limits).getByLabelText(/Approve automatically up to/))
    await user.type(within(limits).getByLabelText(/Approve automatically up to/), '0')
    await user.click(within(limits).getByRole('button', { name: 'Turn on purchasing' }))
    await within(dialog).findByText('Purchasing is on')
    const patch = api.calls.find((c) => c.method === 'PATCH' && c.path === '/companies/c1/policy')!
    expect(patch.body).toMatchObject({
      purchasing_enabled: true,
      purchase_auto_approve_micros: 0,
      purchase_cofounder_limit_micros: 50_000_000,
      purchase_cofounder_monthly_micros: 200_000_000,
      purchase_recurring_requires_human: true,
      purchase_new_vendor_requires_human: true,
    })
  })
})

describe('Set up agent cards: connecting a provider', () => {
  it('stores the key once, empties the field, connects the account and runs the live check', async () => {
    const stripe = { ...manualAccount, id: 'ca2', provider: 'stripe_issuing', label: 'Stripe Issuing agent cards', manual_brand: null, manual_last4: null, mode: 'test', connected_account_id: 'conn1' }
    const { api, dialog, user } = await openWizard({
      'POST /companies/c1/credentials': { id: 'cred1', name: 'stripe_issuing-agent-cards' },
      'POST /companies/c1/connected-accounts': { id: 'conn1' },
      'POST /companies/c1/wallet/accounts': stripe,
      'POST /companies/c1/wallet/accounts/ca2/check': { account_id: 'ca2', ok: true, mode: 'test', issuing_enabled: true, balance_micros: 1_500_000_000 },
    })
    await answer(dialog, user, /US company/)
    await user.click(within(dialog).getByRole('button', { name: 'Show me what to do' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Connect' }))
    const form = await within(dialog).findByRole('form', { name: 'Connect Stripe Issuing' })
    const key = within(form).getByLabelText(/Stripe Issuing restricted key/)
    expect(key).toHaveAttribute('type', 'password')
    await user.type(key, 'rk_test_secretsecretsecret')
    await user.click(within(form).getByRole('button', { name: 'Connect and check' }))
    await within(form).findByText(/Connected\./)
    expect(within(form).getByText(/Balance \$1,500\.00/)).toBeInTheDocument()
    expect(screen.queryByDisplayValue('rk_test_secretsecretsecret')).not.toBeInTheDocument()
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/credentials')!.body).toMatchObject({ kind: 'token', value: 'rk_test_secretsecretsecret' })
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/connected-accounts')!.body).toMatchObject({ provider: 'stripe_issuing', credential_id: 'cred1' })
    const acct = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/wallet/accounts')!.body as Record<string, unknown>
    expect(acct).toMatchObject({ provider: 'stripe_issuing', connected_account_id: 'conn1', mode: 'test' })
    await user.click(within(form).getByRole('button', { name: 'Continue' }))
    await within(dialog).findByRole('form', { name: 'Purchase limits' })
  })

  it('does not show the key if the server echoes it in an error', async () => {
    const { dialog, user } = await openWizard({
      'POST /companies/c1/credentials': () => jsonResponse({ error: { code: 'bad_request', message: 'bad key rk_test_leakleakleak', details: null } }, 400),
    })
    await answer(dialog, user, /US company/)
    await user.click(within(dialog).getByRole('button', { name: 'Show me what to do' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Connect' }))
    const form = await within(dialog).findByRole('form', { name: 'Connect Stripe Issuing' })
    await user.type(within(form).getByLabelText(/Stripe Issuing restricted key/), 'rk_test_leakleakleak')
    await user.click(within(form).getByRole('button', { name: 'Connect and check' }))
    await waitFor(() => expect(within(form).getByRole('alert')).toHaveTextContent('bad key [redacted]'))
    expect(screen.queryByText(/leakleakleak/)).not.toBeInTheDocument()
  })
})
