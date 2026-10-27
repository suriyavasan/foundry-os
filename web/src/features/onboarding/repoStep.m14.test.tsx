import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { makeInspect, makeRepo } from '@/test/m14'
import { renderWithProviders } from '@/test/render'
import { RepoStep } from './steps/RepoStep'

afterEach(() => vi.unstubAllGlobals())

const TOKEN = 'github_pat_very_secret_token'
const err = (status: number, code: string, message: string, details: unknown = null) => jsonResponse({ error: { code, message, details } }, status)

interface Backend {
  available?: boolean
  statusReply?: Response
  accounts?: unknown[]
  inspect?: (body: Record<string, unknown>) => Response
  create?: (body: Record<string, unknown>) => Response
  repoStates?: Array<ReturnType<typeof makeRepo>>
}

function backend(b: Backend = {}) {
  let reads = 0
  const api = mockApi({
    '/companies/c1/repositories/status': () => b.statusReply ?? jsonResponse({ available: b.available ?? true, reason: null, git_version: '2.45.0', milestone: 'M14' }),
    '/companies/c1/connected-accounts': page(b.accounts ?? []),
    'POST /companies/c1/credentials': () => jsonResponse({ id: 'cr1', name: 'GitHub token', kind: 'api_key' }, 201),
    'POST /companies/c1/connected-accounts': () => jsonResponse({ id: 'ca1', company_id: 'c1', provider: 'github', label: 'GitHub', credential_id: 'cr1', scopes: [], status: 'connected', last_checked_at: null, metadata: { login: 'octo' }, created_at: '2026-10-27T09:00:00Z' }, 201),
    'POST /companies/c1/github/inspect': (_u: URL, init?: RequestInit) => (b.inspect ?? (() => jsonResponse(makeInspect())))(JSON.parse(String(init?.body))),
    'POST /companies/c1/repositories': (_u: URL, init?: RequestInit) => (b.create ?? (() => jsonResponse(makeRepo({ status: 'creating' }), 201)))(JSON.parse(String(init?.body))),
    '/companies/c1/repositories/r1': () => {
      const states = b.repoStates ?? [makeRepo({ status: 'active' })]
      return jsonResponse(states[Math.min(reads++, states.length - 1)])
    },
  })
  vi.stubGlobal('fetch', api.impl)
  return api
}

function renderStep(onDone = vi.fn()) {
  renderWithProviders(<RepoStep companyId="c1" companyName="Acme Labs" idea="Sell lemonade" onDone={onDone} />)
  return onDone
}

async function tokenCheck(u: ReturnType<typeof userEvent.setup>) {
  await u.type(await screen.findByLabelText(/GitHub token/), TOKEN)
  await u.click(screen.getByRole('button', { name: 'Check token' }))
  await screen.findByText(/Signed in as/)
}

describe('RepoStep: the choices', () => {
  it('offers three choices with none pre-selected, and "No GitHub" needs no network', async () => {
    const api = backend()
    const onDone = renderStep()
    expect(await screen.findByRole('heading', { name: 'Where should your code live?' })).toBeInTheDocument()
    const newBtn = screen.getByRole('button', { name: /Create a new repository/ })
    const existingBtn = screen.getByRole('button', { name: /Connect an existing repository/ })
    await waitFor(() => expect(newBtn).toBeEnabled())
    expect(existingBtn).toBeEnabled()
    expect(screen.queryByLabelText(/GitHub token/)).not.toBeInTheDocument() // nothing chosen yet
    await userEvent.click(screen.getByRole('button', { name: /No GitHub for now/ }))
    expect(onDone).toHaveBeenCalledWith(null)
    expect(api.calls.filter((c) => c.method !== 'GET')).toEqual([])
  })

  it('says plainly when git is missing here, disables the GitHub choices and still lets the person skip', async () => {
    backend({ available: false, statusReply: jsonResponse({ available: false, reason: 'git is not installed', git_version: null, milestone: 'M14' }) })
    const onDone = renderStep()
    expect(await screen.findByText(/git is not installed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Connect an existing repository/ })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /No GitHub for now/ }))
    expect(onDone).toHaveBeenCalledWith(null)
  })

  it('treats a 501 {available:false} answer as "not available", not as an error', async () => {
    backend({ statusReply: err(501, 'not_available', 'Git is not available on this machine') })
    renderStep()
    expect(await screen.findByText(/Git is not available on this machine/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeDisabled()
  })
})

describe('RepoStep: a new repository', () => {
  it('stores the token once, drops it from the page, shows the checklist and creates the repository', async () => {
    const api = backend({ repoStates: [makeRepo({ status: 'creating' }), makeRepo({ status: 'active' })] })
    const onDone = renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))

    // The token field is a password field, and the checklist for this choice (with Administration) is shown first.
    const field = await screen.findByLabelText(/GitHub token/)
    expect(field).toHaveAttribute('type', 'password')
    const list = screen.getByRole('list', { name: 'Token permissions' })
    expect(within(list).getByText('Administration')).toBeInTheDocument()
    expect(within(list).getByText('Contents')).toBeInTheDocument()
    expect(screen.getByText(/machine-user/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open GitHub’s token page/ })).toHaveAttribute('href', 'https://github.com/settings/personal-access-tokens/new')

    await u.type(field, TOKEN)
    await u.click(screen.getByRole('button', { name: 'Check token' }))
    expect(await screen.findByText(/Signed in as/)).toBeInTheDocument()
    expect(screen.getByText('octo', { selector: 'strong' })).toBeInTheDocument()

    // credential -> connected account -> inspect, and the token left the page
    const posts = api.calls.filter((c) => c.method === 'POST').map((c) => c.path)
    expect(posts).toEqual(['/companies/c1/credentials', '/companies/c1/connected-accounts', '/companies/c1/github/inspect'])
    expect(api.calls.find((c) => c.path.endsWith('/credentials'))!.body).toMatchObject({ name: 'GitHub token', kind: 'api_key', value: TOKEN })
    expect(api.calls.find((c) => c.path.endsWith('/connected-accounts') && c.method === 'POST')!.body).toMatchObject({ provider: 'github', credential_id: 'cr1' })
    expect(api.calls.find((c) => c.path.endsWith('/github/inspect'))!.body).toMatchObject({ connected_account_id: 'ca1', mode: 'create' })
    expect(screen.queryByDisplayValue(TOKEN)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(TOKEN)

    // Defaults: the company's slug as name, the idea as description, private, no policy fields sent.
    expect(screen.getByLabelText(/Repository name/)).toHaveValue('acme-labs')
    expect(screen.getByLabelText('Description')).toHaveValue('Sell lemonade')
    expect(screen.getByLabelText('Owner')).toHaveValue('octo')
    expect(screen.getByRole('radio', { name: /Private/ })).toBeChecked()
    expect(screen.getByLabelText('How changes are merged')).toHaveValue('')
    expect(screen.getByRole('option', { name: /Recommended: merge automatically/ })).toBeInTheDocument()

    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    const create = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')!
    expect(create.body).toEqual({ mode: 'create', connected_account_id: 'ca1', project_name: 'acme-labs', name: 'acme-labs', owner: 'octo', visibility: 'private', description: 'Sell lemonade' })

    expect(await screen.findByText(/Creating the repository/)).toBeInTheDocument()
    const cont = await screen.findByRole('button', { name: 'Continue' }, { timeout: 6000 })
    expect(screen.getByText('The repository is ready.')).toBeInTheDocument()
    await u.click(cont)
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1', status: 'active' }))
  }, 20_000)

  it('does not create a public repository until the person confirms', async () => {
    const api = backend()
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))
    await tokenCheck(u)
    await u.click(screen.getByRole('radio', { name: 'Public' }))
    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    expect(await screen.findByText(/Confirm that your agents’ code will be public/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')).toBe(false)
    await u.click(screen.getByRole('checkbox', { name: /my agents’ code will be public/ }))
    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')!.body).toMatchObject({ visibility: 'public', confirm_public: true }))
  })

  it('validates the name locally and sends only the policy fields that were chosen', async () => {
    const api = backend()
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))
    await tokenCheck(u)
    const name = screen.getByLabelText(/Repository name/)
    await u.clear(name)
    await u.type(name, 'bad name!')
    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    expect(await screen.findByText(/Use letters, digits/)).toBeInTheDocument()
    await u.clear(name)
    await u.type(name, 'lemonade')
    await u.selectOptions(screen.getByLabelText('How changes are merged'), 'pr_human_merge')
    await u.selectOptions(screen.getByLabelText('Issue intake'), 'labeled')
    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')).toBe(true))
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')!.body).toMatchObject({ name: 'lemonade', policy: { merge_mode: 'pr_human_merge', issue_intake: 'labeled', intake_label: 'foundry' } })
  })

  it('explains a refused creation in plain words and keeps the form', async () => {
    backend({ create: () => err(409, 'administration_permission_missing', 'The token cannot create repositories') })
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))
    await tokenCheck(u)
    await u.click(screen.getByRole('button', { name: 'Create the repository' }))
    expect(await screen.findByText(/lacks Administration permission/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create the repository' })).toBeEnabled()
  })

  it('shows what the token check found: missing permissions, warnings, the SSO link', async () => {
    backend({ inspect: () => jsonResponse(makeInspect({ missing_permissions: ['Administration: Read and write'], warnings: ['The token expires in 3 days'], sso_url: 'https://github.com/orgs/acme/sso', token_expires_at: new Date(Date.now() + 3 * 86_400_000 + 3_600_000).toISOString() })) })
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))
    await tokenCheck(u)
    expect(screen.getByText('The token is missing permissions')).toBeInTheDocument()
    expect(screen.getByText('Administration: Read and write')).toBeInTheDocument()
    expect(screen.getByText('The token expires in 3 days')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Authorise the token' })).toHaveAttribute('href', 'https://github.com/orgs/acme/sso')
    expect(screen.getByText(/^Expires in 3 days \(/)).toBeInTheDocument()
  })

  it('reports a bad token without moving on', async () => {
    backend({ inspect: () => err(401, 'unauthorized', 'GitHub rejected the token') })
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Create a new repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Create a new repository/ }))
    await u.type(await screen.findByLabelText(/GitHub token/), TOKEN)
    await u.click(screen.getByRole('button', { name: 'Check token' }))
    expect(await screen.findByText('GitHub rejected the token')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create the repository' })).not.toBeInTheDocument()
  })
})

describe('RepoStep: an existing repository', () => {
  it('reuses a connected account, checks the repository and connects with the human-merge default', async () => {
    const inspected = makeInspect({
      repository: { full_name: 'octo/legacy', exists: true, accessible: true, archived: false, empty: false, size_kb: 20480, visibility: 'private', default_branch: 'develop', html_url: null, protected: true, protection: { branch: 'develop', protected: true, require_pr: true, required_checks: ['ci'], required_approvals: 1, rules_known: true }, has_workflows: true, can_push: true, capabilities: { contents: true, pull_requests: true, issues: false } },
    })
    const api = backend({
      accounts: [{ id: 'ca1', company_id: 'c1', provider: 'github', label: 'GitHub', credential_id: 'cr1', scopes: [], status: 'connected', last_checked_at: null, metadata: { login: 'octo' }, created_at: '2026-10-27T09:00:00Z' }],
      inspect: (body) => jsonResponse(body.full_name ? inspected : makeInspect()),
      create: () => jsonResponse(makeRepo({ id: 'r1', status: 'cloning', origin: 'connected', onboarding_status: 'pending' }), 201),
      repoStates: [makeRepo({ status: 'cloning', origin: 'connected', onboarding_status: 'pending' })],
    })
    const onDone = renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Connect an existing repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Connect an existing repository/ }))
    expect(await screen.findByRole('radio', { name: /Use the connected account “GitHub” \(octo\)/ })).toBeChecked()
    expect(screen.queryByLabelText(/GitHub token/)).not.toBeInTheDocument()
    const list = screen.getByRole('list', { name: 'Token permissions' })
    expect(within(list).queryByText('Administration')).not.toBeInTheDocument() // an existing repository needs no Administration
    await u.click(screen.getByRole('button', { name: 'Check token' }))
    await screen.findByText(/Signed in as/)
    expect(api.calls.some((c) => c.path.endsWith('/credentials'))).toBe(false)

    await u.type(screen.getByLabelText(/^Repository/), 'https://github.com/octo/legacy.git')
    await u.click(screen.getByRole('button', { name: 'Check repository' }))
    expect(await screen.findByText(/Default branch develop/)).toBeInTheDocument()
    expect(screen.getByText('develop is protected')).toBeInTheDocument()
    expect(screen.getByText('Required checks: ci')).toBeInTheDocument()
    expect(api.calls.filter((c) => c.path.endsWith('/github/inspect')).at(-1)!.body).toMatchObject({ mode: 'connect', full_name: 'https://github.com/octo/legacy.git' })
    expect(screen.getByRole('option', { name: /Recommended: a human merges/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Recommended: required \(this repository has workflows\)/ })).toBeInTheDocument()
    // The default branch is protected and needs pull requests: direct push cannot be chosen.
    expect(screen.getByRole('option', { name: /Push straight to the default branch/ })).toBeDisabled()

    await u.click(screen.getByRole('button', { name: 'Connect the repository' }))
    const create = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/repositories')!
    expect(create.body).toEqual({ mode: 'connect', connected_account_id: 'ca1', project_name: 'legacy', full_name: 'https://github.com/octo/legacy.git' })
    expect(await screen.findByText('Cloning the repository…')).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: 'Continue while it finishes' }))
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1' }))
  }, 20_000)

  it('rejects an unparsable repository locally and explains a refused one', async () => {
    backend({ create: () => err(409, 'repository_not_accessible', 'no access') })
    renderStep()
    const u = userEvent.setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Connect an existing repository/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Connect an existing repository/ }))
    await tokenCheck(u)
    await u.type(screen.getByLabelText(/^Repository/), 'not a repo')
    await u.click(screen.getByRole('button', { name: 'Connect the repository' }))
    expect(await screen.findByText(/Expected owner\/name/)).toBeInTheDocument()
    await u.clear(screen.getByLabelText(/^Repository/))
    await u.type(screen.getByLabelText(/^Repository/), 'octo/secret')
    await u.click(screen.getByRole('button', { name: 'Connect the repository' }))
    expect(await screen.findByText(/The token cannot see this repository/)).toBeInTheDocument()
  })

  it('goes back to the choices without losing the step', async () => {
    backend()
    renderStep()
    await waitFor(() => expect(screen.getByRole('button', { name: /Connect an existing repository/ })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: /Connect an existing repository/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Choose differently' }))
    expect(await screen.findByRole('heading', { name: 'Where should your code live?' })).toBeInTheDocument()
  })
})
