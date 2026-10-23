import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiError } from '@/test/m5'
import { jsonResponse, makeApproval, mockApi, page } from '@/test/factories'
import { baseRoutes, iris, loc, renderCanvas } from '@/test/m5'
import { makeAccount, makeReach } from '@/test/m8'

afterEach(() => vi.unstubAllGlobals())
beforeEach(() => localStorage.clear())

const TOKEN = 'cf-supersecret-token-0123456789'
const publicReach = makeReach({ tunnel: { provider: 'cloudflare_quick', state: 'running', url: 'https://calm-otter.trycloudflare.com', healthy: true, last_health_check_at: '2026-10-24T10:00:00Z' } })

const registry = { id: 'is-a-dev', name: 'is-a.dev', suffix: 'is-a.dev', repo: 'is-a-dev/register', rules_url: 'https://example.org/rules', eligibility: ['Personal or open-source projects only'], review: 'human', name_min_length: 3, name_max_length: 30, name_pattern: '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$' }
const preview = { fqdn: 'acme.is-a.dev', repo: 'is-a-dev/register', branch: 'add-acme', file_path: 'domains/acme.json', file_content: '{"owner":{"username":"sam"},"record":{"CNAME":"x.trycloudflare.com"}}', pr_title: 'Register acme.is-a.dev', pr_body: 'Please register acme.is-a.dev', publishes_as: 'sam', eligible: true, ineligible_reason: null, warnings: ['Reviews can take several days.'] }
const noDomain = { domain: null, path: null, status: 'none', dns_status: 'unknown', tls_status: 'unknown', dns_records: [], steps: [], registration: null, last_checked_at: null, error: null }

function boot(routes: Record<string, unknown>, route = '/c/c1?panel=reachability') {
  const api = mockApi({ ...baseRoutes, '/reachability': publicReach, '/companies/c1/connected-accounts': page([]), '/companies/c1/domain': noDomain, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(route)
  return api
}
const reg = (over = {}) => ({ id: 'r1', company_id: 'c1', path: 'github_registry', domain: 'acme.is-a.dev', registry_id: 'is-a-dev', provider_id: null, registrar: null, status: 'awaiting_approval', approval_id: 'ap1', pr_url: null, price_micros: null, error: null, created_at: '2026-10-24T10:00:00Z', updated_at: '2026-10-24T10:00:00Z', ...over })

async function openWizard(user: ReturnType<typeof userEvent.setup>) {
  const p = await screen.findByRole('dialog', { name: 'Reachability' })
  await user.click(await within(p).findByRole('button', { name: /^Get (a|another) domain$/ }))
  return screen.findByRole('dialog', { name: 'Get a domain' })
}

describe('domain wizard', () => {
  it('offers three clearly labelled paths', async () => {
    boot({})
    const w = await openWizard(userEvent.setup())
    const radios = within(w).getAllByRole('radio')
    expect(radios.map((r) => r.getAttribute('aria-label'))).toEqual(['1. Free subdomain from a GitHub registry', '2. Free domain from a provider with a dashboard', '3. Buy a domain from a registrar'])
    expect(within(w).getByText(/published under your GitHub identity/)).toBeInTheDocument()
    expect(within(w).getByText('Paid · needs your approval')).toBeInTheDocument()
    expect(within(w).getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('registry path: previews the exact file, then creates the registration that becomes an approval', async () => {
    const api = boot({
      '/companies/c1/domain/registries': page([registry]),
      'POST /companies/c1/domain/registrations/preview': () => jsonResponse(preview),
      'POST /companies/c1/domain/registrations': () => jsonResponse(reg()),
    })
    const user = userEvent.setup()
    const w = await openWizard(userEvent.setup())
    await user.click(within(w).getByRole('radio', { name: /GitHub registry/ }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.click(await within(w).findByRole('radio', { name: 'is-a.dev' }))
    expect(within(w).getByText('Personal or open-source projects only')).toBeInTheDocument()
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    const name = await within(w).findByLabelText(/Desired name/)
    await user.type(name, 'Acme')
    expect(name).toHaveValue('acme')
    await user.click(within(w).getByRole('button', { name: 'Preview the registration' }))
    // the exact content the agent will submit
    expect(await within(w).findByLabelText('Registration file')).toHaveTextContent('"CNAME":"x.trycloudflare.com"')
    expect(within(w).getByText('domains/acme.json')).toBeInTheDocument()
    expect(within(w).getByLabelText('Pull request text')).toHaveTextContent('Please register acme.is-a.dev')
    expect(within(w).getByText(/publishing this under your GitHub identity \(@sam\)/)).toBeInTheDocument()
    expect(within(w).getByText('Reviews can take several days.')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path.endsWith('/preview'))?.body).toEqual({ registry_id: 'is-a-dev', name: 'acme' })

    await user.click(within(w).getByRole('button', { name: 'Request approval to submit' }))
    expect(await within(w).findByText('Waiting for your approval')).toBeInTheDocument()
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/domain/registrations')?.body).toEqual({ path: 'github_registry', registry_id: 'is-a-dev', name: 'acme' })
    await user.click(within(w).getByRole('button', { name: 'Open Approvals' }))
    expect(loc()).toBe('?panel=approvals')
  })

  it('does not let an ineligible request through', async () => {
    boot({
      '/companies/c1/domain/registries': page([registry]),
      'POST /companies/c1/domain/registrations/preview': () => jsonResponse({ ...preview, eligible: false, ineligible_reason: 'Commercial use is not allowed.' }),
    })
    const user = userEvent.setup()
    const w = await openWizard(user)
    await user.click(within(w).getByRole('radio', { name: /GitHub registry/ }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.click(await within(w).findByRole('radio', { name: 'is-a.dev' }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.type(await within(w).findByLabelText(/Desired name/), 'acme')
    await user.click(within(w).getByRole('button', { name: 'Preview the registration' }))
    expect(await within(w).findByText(/Commercial use is not allowed/)).toBeInTheDocument()
    expect(within(w).getByRole('button', { name: 'Request approval to submit' })).toBeDisabled()
  })

  it('assisted path: shows the steps and DNS records and verifies on request', async () => {
    const plan = { ...noDomain, domain: 'acme.dpdns.org', path: 'free_provider', status: 'awaiting_founder', dns_records: [{ type: 'CNAME', name: 'acme.dpdns.org', value: 'calm-otter.trycloudflare.com', status: 'pending' }], steps: ['Add the CNAME record at the provider.'] }
    const api = boot({
      '/companies/c1/domain/free-providers': page([{ id: 'dp', name: 'DigitalPlat', dashboard_url: 'https://dash.example.org', suffixes: ['dpdns.org'], eligibility: [], steps: ['Create an account on the dashboard.'] }]),
      'POST /companies/c1/domain/registrations': () => jsonResponse(reg({ path: 'free_provider', provider_id: 'dp', status: 'awaiting_founder', approval_id: null })),
      '/companies/c1/domain': plan,
      'POST /companies/c1/domain/verify': () => jsonResponse({ ...plan, status: 'active', dns_status: 'ok' }),
    })
    const user = userEvent.setup()
    const w = await openWizard(user)
    await user.click(within(w).getByRole('radio', { name: /provider with a dashboard/ }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.click(await within(w).findByRole('radio', { name: 'DigitalPlat' }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.type(await within(w).findByLabelText(/Domain you want/), 'acme.dpdns.org')
    await user.click(within(w).getByRole('button', { name: 'Show me the steps' }))
    const steps = await within(w).findByRole('list', { name: 'Steps' })
    expect(within(steps).getByText('Create an account on the dashboard.')).toBeInTheDocument()
    await within(w).findByText('Add the CNAME record at the provider.')
    expect(within(w).getByRole('link', { name: /Open DigitalPlat’s dashboard/ })).toHaveAttribute('href', 'https://dash.example.org')
    expect(await within(w).findByRole('table', { name: 'DNS records' })).toBeInTheDocument()
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/domain/registrations')?.body).toEqual({ path: 'free_provider', provider_id: 'dp', domain: 'acme.dpdns.org' })
    await user.click(within(w).getByRole('button', { name: 'I’ve done it, verify' }))
    expect(await within(w).findByText('DNS verified')).toBeInTheDocument()
  })

  it('paid path: shows the price and creates a human approval bound to it', async () => {
    const api = boot({
      '/companies/c1/domain/search': page([
        { domain: 'acme.dev', available: true, registrar: 'porkbun', price_micros: 12_000_000, renewal_price_micros: 15_000_000, cost_kind: 'estimated' },
        { domain: 'acme.com', available: false, registrar: 'porkbun', price_micros: null, renewal_price_micros: null },
      ]),
      'POST /companies/c1/domain/registrations': () => jsonResponse(reg({ path: 'paid_registrar', domain: 'acme.dev', price_micros: 12_000_000 })),
    })
    const user = userEvent.setup()
    const w = await openWizard(user)
    await user.click(within(w).getByRole('radio', { name: /Buy a domain/ }))
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    await user.type(await within(w).findByLabelText(/Search a name/), 'acme')
    await user.click(within(w).getByRole('button', { name: 'Search' }))
    const ok = await within(w).findByRole('radio', { name: 'acme.dev' })
    expect(ok).toHaveTextContent('$12.00 first year')
    expect(within(w).getByRole('radio', { name: 'acme.com (taken)' })).toBeDisabled()
    await user.click(ok)
    await user.click(within(w).getByRole('button', { name: 'Next' }))
    expect(within(w).getByText(/creates an approval/)).toBeInTheDocument()
    await user.click(within(w).getByRole('button', { name: 'Request purchase' }))
    expect(await within(w).findByText(/Nothing is bought until you approve it/)).toBeInTheDocument()
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/domain/registrations')?.body).toEqual({ path: 'paid_registrar', registrar: 'porkbun', domain: 'acme.dev', years: 1, max_price_micros: 12_000_000 })
  })
})

describe('connect an account', () => {
  const credBody = { id: 'cr1', name: 'cloudflare-token', kind: 'token' }
  async function openDialog(extra: Record<string, unknown>) {
    const api = boot(extra, '/c/c1?panel=integrations')
    const user = userEvent.setup()
    const p = await screen.findByRole('dialog', { name: 'Integrations' })
    await user.click(await within(p).findByRole('button', { name: 'Connect an account' }))
    const d = await screen.findByRole('dialog', { name: 'Connect an account' })
    return { api, user, d }
  }

  it('states the boundary and never echoes the token; field is emptied after the server took it, request carries it only to the credential store', async () => {
    const { api, user, d } = await openDialog({
      'POST /companies/c1/credentials': () => jsonResponse(credBody, 201),
      'POST /companies/c1/connected-accounts': () => jsonResponse({ ...makeAccount(), id: 'ca9' }, 201),
      'POST /companies/c1/connected-accounts/ca9/test': () => jsonResponse({ ok: true, message: null, checked_at: '2026-10-24T10:00:00Z', account_label: 'Acme', scopes: [] }),
    })
    expect(within(d).getByText(/never creates accounts for you on these services/)).toBeInTheDocument()
    const input = within(d).getByLabelText(/API token/)
    expect(input).toHaveAttribute('type', 'password')
    await user.type(input, TOKEN)
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument()
    await user.click(within(d).getByRole('button', { name: 'Connect Cloudflare' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Connect an account' })).not.toBeInTheDocument())
    const cred = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/credentials')!
    expect((cred.body as { value: string }).value).toBe(TOKEN)
    const account = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/connected-accounts')!
    expect(JSON.stringify(account.body)).not.toContain(TOKEN)
    expect(account.body).toMatchObject({ provider: 'cloudflare', credential_id: 'cr1' })
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it('scrubs the token out of an error message and clears the field once stored', async () => {
    const { api, user, d } = await openDialog({
      'POST /companies/c1/credentials': () => jsonResponse(credBody, 201),
      'POST /companies/c1/connected-accounts': () => apiError(500, `Provider said: invalid token ${TOKEN}`),
    })
    await user.type(within(d).getByLabelText(/API token/), TOKEN)
    await user.click(within(d).getByRole('button', { name: 'Connect Cloudflare' }))
    const alert = await within(d).findByRole('alert')
    expect(alert).toHaveTextContent('Provider said: invalid token [redacted]')
    expect(document.body.textContent).not.toContain(TOKEN)
    // the secret field is gone (saved) so a retry cannot store it twice
    expect(within(d).queryByLabelText(/API token/)).not.toBeInTheDocument()
    expect(within(d).getByText(/will not be stored twice/)).toBeInTheDocument()
    await user.click(within(d).getByRole('button', { name: 'Connect Cloudflare' }))
    await waitFor(() => expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/connected-accounts').length).toBe(2))
    expect(api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/credentials').length).toBe(1)
  })

  it('validates required fields before sending anything', async () => {
    const { api, user, d } = await openDialog({})
    await user.click(within(d).getByRole('button', { name: 'Connect Cloudflare' }))
    expect(await within(d).findByText('API token is required')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('opens from the palette', async () => {
    boot({}, '/c/c1')
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'connect an account')
    await user.click(await screen.findByRole('option', { name: /Connect an account/ }))
    expect(await screen.findByRole('dialog', { name: 'Connect an account' })).toBeInTheDocument()
  })
})

describe('connected accounts list', () => {
  it('lists metadata only, tests and revokes', async () => {
    let accounts = [makeAccount({ metadata: { account_name: 'Acme Inc' } }), makeAccount({ id: 'ca2', provider: 'github', label: 'GitHub', status: 'invalid', scopes: [] })]
    const api = boot(
      {
        '/companies/c1/connected-accounts': () => jsonResponse(page(accounts)),
        '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
        'POST /companies/c1/connected-accounts/ca1/test': () => jsonResponse({ ok: true, message: 'Token is active', checked_at: '2026-10-24T11:00:00Z', account_label: 'Acme', scopes: ['dns:edit'] }),
        'DELETE /companies/c1/connected-accounts/ca1': () => {
          accounts = accounts.filter((a) => a.id !== 'ca1')
          return new Response(null, { status: 204 })
        },
      },
      '/c/c1?panel=integrations',
    )
    const user = userEvent.setup()
    const list = await within(await screen.findByRole('dialog', { name: 'Integrations' })).findByRole('table', { name: 'Connected accounts list' })
    expect(within(list).getByText('Acme Inc')).toBeInTheDocument()
    expect(within(list).getByText('Not working')).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: 'Test Cloudflare' }))
    expect(await within(list).findByText('Token is active')).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: 'Revoke Cloudflare' }))
    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/connected-accounts/ca1')).toBe(true))
    await waitFor(() => expect(within(screen.getByRole('dialog', { name: 'Integrations' })).queryByText('Acme Inc')).not.toBeInTheDocument())
  })
})

describe('approvals', () => {
  it('renders the exact scope of the new action kinds', async () => {
    boot(
      {
        '/companies/c1/approvals': page([
          makeApproval({ id: 'a1', title: 'Register acme.is-a.dev', action: 'domain.register_pr', tier: 'external_commitment', scope: { fqdn: 'acme.is-a.dev', repo: 'is-a-dev/register', file_path: 'domains/acme.json', file_content: '{"x":1}', pr_title: 'Register acme', pr_body: 'Body text', publishes_as: 'sam' } }),
          makeApproval({ id: 'a2', title: 'Buy acme.dev', action: 'domain.purchase', tier: 'spend', amount_micros: 12_000_000, scope: { domain: 'acme.dev', registrar: 'porkbun', years: 1, max_price_micros: 12_000_000 } }),
          makeApproval({ id: 'a3', title: 'Point DNS at the tunnel', action: 'dns.change', tier: 'identity', scope: { zone: 'acme.dev', changes: [{ op: 'create', type: 'CNAME', name: 'app.acme.dev', value: 'x.trycloudflare.com', ttl: 300 }] } }),
          makeApproval({ id: 'a4', title: 'Create cofounder@', action: 'mailbox.create', tier: 'identity', scope: { address: 'cofounder@acme.dev', provider: 'agentmail' } }),
          makeApproval({ id: 'a5', title: 'Deploy the UI', action: 'hosted_ui.deploy', tier: 'identity', scope: { provider: 'vercel', project: 'acme-ui', proxy_target: 'https://x.trycloudflare.com' } }),
        ]),
      },
      '/c/c1?panel=approvals',
    )
    const p = await screen.findByRole('dialog', { name: 'Approvals' })
    const pr = await within(p).findByRole('group', { name: 'Exact scope of Register acme.is-a.dev' })
    expect(within(pr).getByText('domains/acme.json')).toBeInTheDocument()
    expect(within(pr).getByText('{"x":1}')).toBeInTheDocument()
    expect(within(pr).getByText('Body text')).toBeInTheDocument()
    expect(within(pr).getByText(/under your GitHub identity/)).toBeInTheDocument()
    const buy = within(p).getByRole('group', { name: 'Exact scope of Buy acme.dev' })
    expect(within(buy).getByText(/\$12\.00 \(quoted by the registrar\)/)).toBeInTheDocument()
    const dns = within(p).getByRole('group', { name: 'Exact scope of Point DNS at the tunnel' })
    expect(within(within(dns).getByRole('list', { name: 'DNS changes' })).getByText('app.acme.dev')).toBeInTheDocument()
    expect(within(within(p).getByRole('group', { name: 'Exact scope of Create cofounder@' })).getByText('cofounder@acme.dev')).toBeInTheDocument()
    expect(within(within(p).getByRole('group', { name: 'Exact scope of Deploy the UI' })).getByText('acme-ui')).toBeInTheDocument()
  })
})

describe('hosted UI and company email', () => {
  const hostedRoutes = (accounts: unknown[]) => ({
    '/companies/c1/connected-accounts': page(accounts),
    '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
    '/companies/c1/hosted-ui': { status: 'ready', provider: 'vercel', project_name: 'acme-ui', url: 'https://acme-ui.vercel.app', proxy_target: 'https://calm-otter.trycloudflare.com', last_deployed_at: '2026-10-24T09:00:00Z', error: null },
    'POST /companies/c1/hosted-ui/deploy': () => jsonResponse({ status: 'deploying', provider: 'vercel', project_name: 'acme-ui', url: null, proxy_target: null, last_deployed_at: null, error: null }),
  })

  it('hosted UI: explains the public-URL dependency and blocks deploy without a Vercel account', async () => {
    boot(hostedRoutes([]))
    const card = await within(await screen.findByRole('dialog', { name: 'Reachability' })).findByLabelText('Hosted control UI')
    expect(await within(card).findByText('Live')).toBeInTheDocument()
    expect(within(card).getByText('https://acme-ui.vercel.app')).toBeInTheDocument()
    expect(within(card).getByText(/talks to/)).toHaveTextContent('this device')
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Redeploy' })).toBeDisabled())
    expect(within(card).getByTestId('hosted-blocked')).toHaveTextContent('Connect a Vercel account first.')
  })

  it('hosted UI: redeploys when Vercel is connected and the tunnel is up', async () => {
    const api = boot(hostedRoutes([makeAccount({ id: 'v', provider: 'vercel' })]))
    const card = await within(await screen.findByRole('dialog', { name: 'Reachability' })).findByLabelText('Hosted control UI')
    const btn = await within(card).findByRole('button', { name: 'Redeploy' })
    await waitFor(() => expect(btn).toBeEnabled())
    await userEvent.setup().click(btn)
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/hosted-ui/deploy')).toBe(true))
  })

  it('email: mailboxes with owner agent and counts, auto-creation note, disclosure footer toggle', async () => {
    const api = boot({
      '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
      '/companies/c1/email': { provider: 'agentmail', status: 'connected', domain: 'acme.agentmail.to', error: null, ai_disclosure_footer: true },
      '/companies/c1/mailboxes': page([
        { id: 'm1', company_id: 'c1', address: 'iris@acme.agentmail.to', display_name: null, owner_agent_id: iris.id, provider: 'agentmail', status: 'active', inbound_count: 7, outbound_count: 3, last_inbound_at: null, last_outbound_at: null, created_at: '2026-10-24T09:00:00Z' },
        { id: 'm0', company_id: 'c1', address: 'company@acme.agentmail.to', display_name: null, owner_agent_id: null, provider: 'agentmail', status: 'pending', inbound_count: 0, outbound_count: 0, last_inbound_at: null, last_outbound_at: null, created_at: '2026-10-24T09:00:00Z' },
      ]),
      'PATCH /companies/c1/email': () => jsonResponse({ provider: 'agentmail', status: 'connected', domain: 'acme.agentmail.to', error: null, ai_disclosure_footer: false }),
    })
    const user = userEvent.setup()
    const card = await within(await screen.findByRole('dialog', { name: 'Reachability' })).findByLabelText('Company email')
    expect(await within(card).findByText(/creates mailboxes automatically once a provider is connected/)).toBeInTheDocument()
    const table = await within(card).findByRole('table', { name: 'Mailboxes' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('company@acme.agentmail.to')
    expect(rows[1]).toHaveTextContent('iris@acme.agentmail.to')
    expect(rows[1]).toHaveTextContent('Iris')
    expect(rows[1]).toHaveTextContent('73')
    const sw = within(card).getByRole('switch', { name: 'AI disclosure footer' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    await user.click(sw)
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH' && c.path === '/companies/c1/email')?.body).toEqual({ ai_disclosure_footer: false }))
  })

  it('domain card: shows DNS and TLS status and the pending approval', async () => {
    boot({
      '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
      '/companies/c1/domain': { ...noDomain, domain: 'acme.is-a.dev', path: 'github_registry', status: 'pr_open', dns_status: 'pending', tls_status: 'unknown', registration: reg({ status: 'pr_open', pr_url: 'https://github.com/is-a-dev/register/pull/1' }), dns_records: [{ type: 'CNAME', name: 'acme.is-a.dev', value: 'x.trycloudflare.com', status: 'missing' }] },
    })
    const card = await within(await screen.findByRole('dialog', { name: 'Reachability' })).findByLabelText('Domain')
    expect((await within(card).findAllByText('acme.is-a.dev')).length).toBeGreaterThan(0)
    expect(within(card).getByText('Pull request open')).toBeInTheDocument()
    expect(within(card).getByText('Pending')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Registration pull request/ })).toHaveAttribute('href', 'https://github.com/is-a-dev/register/pull/1')
    expect(within(card).getByText('Missing')).toBeInTheDocument()
  })
})

describe('palette: copy public URL', () => {
  it('copies the running tunnel address', async () => {
    boot({}, '/c/c1')
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await screen.findByTestId('reachability-indicator')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'copy public')
    await user.click(await screen.findByRole('option', { name: /Copy public URL/ }))
    await waitFor(async () => expect(await navigator.clipboard.readText()).toBe('https://calm-otter.trycloudflare.com'))
  })

  it('without a public address it says so and opens Reachability', async () => {
    boot({ '/reachability': makeReach() }, '/c/c1')
    const user = userEvent.setup()
    await screen.findByTestId('reachability-indicator')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'copy public')
    await user.click(await screen.findByRole('option', { name: /Copy public URL/ }))
    expect((await screen.findAllByText(/no public address yet/i)).length).toBeGreaterThan(0)
    expect(loc()).toBe('?panel=reachability')
  })
})
