import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeAgent, makeMember, makeTask, mockApi, page } from '@/test/factories'
import { makeHumanWork, makeJob } from '@/test/m22'
import { apiError, baseRoutes, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const me = makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'owner' })
const role = (r: string) => ({ user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: r }] })
const routes = (over: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/companies/c1/members': page([me]),
  '/companies/c1/human-work': page([]),
  '/companies/c1/outsource-jobs': page([]),
  ...over,
})
const peopleOps = () => screen.findByRole('dialog', { name: 'People ops' })

async function open(over: Record<string, unknown> = {}, route = '/c/c1?panel=people-ops') {
  const api = mockApi(routes(over))
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(route)
  return { api, user: userEvent.setup() }
}

describe('People ops: human work', () => {
  it('lists open requests with category, due text, who asked and the instructions as plain text', async () => {
    await open({ '/companies/c1/human-work': page([makeHumanWork({ instructions: 'Sign <b>page 3</b>', title: 'Sign the NDA' })]) })
    const d = await peopleOps()
    const row = await within(d).findByTestId('human-work-item')
    expect(within(row).getByText('Sign the NDA')).toBeInTheDocument()
    expect(within(row).getByText('Signature')).toBeInTheDocument()
    expect(within(row).getByText(/Due in/)).toBeInTheDocument()
    expect(within(row).getByText(/Asked by Iris/)).toBeInTheDocument()
    expect(within(row).getByText(/In the pool/)).toBeInTheDocument()
    // plain text: the markup is shown, not rendered
    expect(within(row).getByTestId('human-instructions')).toHaveTextContent('Sign <b>page 3</b>')
    expect(row.querySelector('b')).toBeNull()
  })

  it('says plainly when nobody is waiting for a person', async () => {
    await open()
    expect(await within(await peopleOps()).findByText('No AI employee is waiting for a person')).toBeInTheDocument()
  })

  it('is "not built yet" on a backend without human work, not an error', async () => {
    const api = mockApi({ ...baseRoutes, '/companies/c1/members': page([me]) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=people-ops')
    const d = await peopleOps()
    expect(await within(d).findByText('Human work is not built yet')).toBeInTheDocument()
    expect(within(d).getByText(/Arrives in M22/)).toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  it('shows an error with retry on a server failure', async () => {
    await open({ '/companies/c1/human-work': () => apiError(500, 'boom') })
    expect(await within(await peopleOps()).findByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('lets a member take a pooled task ("I\'ll do it") and shows the server conflict when somebody was faster', async () => {
    const item = makeHumanWork()
    const taken = vi.fn(() => jsonResponse({ ...item, pool: false, assignee_ref: 'member:m-me', assignee_name: 'Sam' }))
    const { api, user } = await open({ '/companies/c1/human-work': page([item]), 'POST /companies/c1/human-work/h1/claim': taken }, '/c/c1?panel=people-ops')
    const d = await peopleOps()
    await user.click(await within(d).findByRole('button', { name: "I'll do it" }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/human-work/h1/claim')).toBe(true))
  })

  it('says so when a second claim is refused (409)', async () => {
    const { user } = await open({ '/companies/c1/human-work': page([makeHumanWork()]), 'POST /companies/c1/human-work/h1/claim': () => apiError(409, 'Somebody already took this task') })
    await user.click(await within(await peopleOps()).findByRole('button', { name: "I'll do it" }))
    expect(await screen.findAllByText(/Somebody already took/)).not.toHaveLength(0)
  })

  it('does not offer taking, completing or declining to a viewer', async () => {
    await open({ '/me': role('viewer'), '/companies/c1/human-work': page([makeHumanWork()]) })
    const d = await peopleOps()
    await within(d).findByTestId('human-work-item')
    expect(within(d).queryByRole('button', { name: "I'll do it" })).not.toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /Decline/ })).not.toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /Complete/ })).not.toBeInTheDocument()
  })

  it('does not offer decline to a plain member', async () => {
    const member = makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'member' })
    await open({ '/me': role('member'), '/companies/c1/members': page([member]), '/companies/c1/human-work': page([makeHumanWork()]) })
    const d = await peopleOps()
    await within(d).findByTestId('human-work-item')
    expect(within(d).getByRole('button', { name: "I'll do it" })).toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: /Decline/ })).not.toBeInTheDocument()
  })

  it('declines with a reason', async () => {
    const decline = vi.fn(() => jsonResponse(makeHumanWork({ status: 'cancelled' })))
    const { api, user } = await open({ '/companies/c1/human-work': page([makeHumanWork()]), 'POST /companies/c1/human-work/h1/decline': decline })
    const d = await peopleOps()
    await user.click(await within(d).findByRole('button', { name: 'Decline Sign the NDA' }))
    const dialog = await screen.findByRole('dialog', { name: 'Decline this request' })
    await user.click(within(dialog).getByRole('button', { name: 'Decline' }))
    expect(await within(dialog).findByText(/Say why/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    await user.type(within(dialog).getByLabelText(/Reason/), 'We never sign by e-mail')
    await user.click(within(dialog).getByRole('button', { name: 'Decline' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ reason: 'We never sign by e-mail' }))
  })

  it('completes a task that is mine with a comment (the existing complete endpoint)', async () => {
    const mine = makeHumanWork({ pool: false, assignee_ref: 'member:m-me', assignee_name: 'Sam', status: 'ready' })
    const complete = vi.fn(() => jsonResponse(makeTask({ id: 'h1', status: 'done' })))
    const { api, user } = await open({ '/companies/c1/human-work': page([mine]), 'POST /companies/c1/tasks/h1/complete': complete })
    const d = await peopleOps()
    expect(within(d).queryByRole('button', { name: "I'll do it" })).not.toBeInTheDocument()
    await user.click(await within(d).findByRole('button', { name: 'Complete Sign the NDA' }))
    const dialog = await screen.findByRole('dialog', { name: 'Complete task' })
    await user.type(within(dialog).getByLabelText(/Comment/), 'Signed')
    await user.click(within(dialog).getByRole('button', { name: 'Mark complete' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/companies/c1/tasks/h1/complete')?.body).toEqual({ comment: 'Signed' }))
  })

  it('filters to mine and to the pool', async () => {
    const mine = makeHumanWork({ id: 'h2', title: 'Call the bank', category: 'call', pool: false, assignee_ref: 'member:m-me', assignee_name: 'Sam' })
    const { user } = await open({ '/companies/c1/human-work': page([makeHumanWork(), mine]) })
    const d = await peopleOps()
    await within(d).findAllByTestId('human-work-item')
    await user.click(within(d).getByRole('button', { name: 'Mine' }))
    expect(within(d).getAllByTestId('human-work-item')).toHaveLength(1)
    expect(within(d).getByText('Call the bank')).toBeInTheDocument()
    await user.click(within(d).getByRole('button', { name: 'Pool' }))
    expect(within(d).getByText('Sign the NDA')).toBeInTheDocument()
    expect(within(d).queryByText('Call the bank')).not.toBeInTheDocument()
  })

  it('marks an overdue request', async () => {
    await open({ '/companies/c1/human-work': page([makeHumanWork({ overdue: true, due_at: new Date(Date.now() - 5 * 3_600_000).toISOString() })]) })
    const row = await within(await peopleOps()).findByTestId('human-work-item')
    expect(row).toHaveAttribute('data-overdue', 'true')
    expect(within(row).getByText(/Overdue by/)).toBeInTheDocument()
  })
})

describe('People ops: freelancers', () => {
  const clickTab = async (user: ReturnType<typeof userEvent.setup>) => user.click(await within(await peopleOps()).findByRole('tab', { name: 'Freelancers' }))

  it('is empty with an honest explanation', async () => {
    const { user } = await open()
    await clickTab(user)
    expect(await within(await peopleOps()).findByText('No freelance jobs yet')).toBeInTheDocument()
    expect(within(await peopleOps()).getByText(/Foundry never posts or pays on a marketplace/)).toBeInTheDocument()
  })

  it('lists open and finished jobs and opens one', async () => {
    const { user } = await open({
      '/companies/c1/outsource-jobs': page([makeJob({ id: 'j1' }), makeJob({ id: 'j2', title: 'Old job', status: 'closed' })]),
      '/companies/c1/outsource-jobs/j1': makeJob({ id: 'j1' }),
    })
    await clickTab(user)
    const d = await peopleOps()
    const open_ = await within(d).findByRole('region', { name: 'Open jobs' })
    expect(within(open_).getByText('Logo for Acme')).toBeInTheDocument()
    expect(within(open_).getByText('Approved')).toBeInTheDocument()
    expect(within(open_).getByText(/Budget \$50\.00/)).toBeInTheDocument()
    expect(within(within(d).getByRole('region', { name: 'Finished' })).getByText('Old job')).toBeInTheDocument()
    await user.click(within(open_).getByRole('button', { name: 'Open job Logo for Acme' }))
    await waitFor(() => expect(loc()).toBe('?panel=outsource:j1'))
  })

  it('is "not built yet" without outsourcing', async () => {
    const api = mockApi({ ...baseRoutes, '/companies/c1/members': page([me]), '/companies/c1/human-work': page([]) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=people-ops')
    await userEvent.click(await within(await peopleOps()).findByRole('tab', { name: 'Freelancers' }))
    expect(await within(await peopleOps()).findByText('Outsourcing is not built yet')).toBeInTheDocument()
  })
})

describe('freelance job panel', () => {
  const jobRoutes = (j = makeJob(), over: Record<string, unknown> = {}) => ({ '/companies/c1/outsource-jobs/j1': j, ...over })
  const panel = () => screen.findByRole('dialog', { name: /Logo for Acme|Freelance job/ })

  it('shows the brief as plain text, the status, the approval and honest next steps', async () => {
    await open(jobRoutes(makeJob({ brief: 'Draw <img src=x onerror=alert(1)> a logo', status: 'approved' })), '/c/c1?panel=outsource:j1')
    const d = await panel()
    const brief = await within(d).findByTestId('job-brief')
    expect(brief).toHaveTextContent('Draw <img src=x onerror=alert(1)> a logo')
    expect(brief.querySelector('img')).toBeNull()
    expect(within(d).getByText(/Post it on Fiverr yourself/)).toBeInTheDocument()
    expect(within(d).getByText(/Foundry never posts, messages, hires or pays on a marketplace/)).toBeInTheDocument()
    expect(within(d).getByText(/the most you approved/)).toBeInTheDocument()
  })

  it('offers an https helper link that opens in a new tab and copies the brief', async () => {
    const { user } = await open(jobRoutes(), '/c/c1?panel=outsource:j1')
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    const d = await panel()
    const link = await within(d).findByRole('link', { name: /Open Fiverr/ })
    expect(link).toHaveAttribute('href', 'https://www.fiverr.com/search/gigs?query=Logo')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    await user.click(within(d).getByRole('button', { name: 'Copy brief' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(String((writeText.mock.calls as unknown as string[][])[0]![0])).toContain('SVG and PNG, three drafts.')
  })

  it('never renders a non-https helper link', async () => {
    await open(jobRoutes(makeJob({ open_url: 'javascript:alert(1)' })), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await within(d).findByTestId('job-brief')
    expect(within(d).queryByRole('link', { name: /Open Fiverr/ })).not.toBeInTheDocument()
  })

  it('checks the posted link against the marketplace domain before sending anything', async () => {
    const { api, user } = await open(jobRoutes(), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await user.click(await within(d).findByRole('button', { name: 'I posted it' }))
    const dialog = await screen.findByRole('dialog', { name: 'I posted it' })
    await user.type(within(dialog).getByLabelText(/Link to the posted job/), 'https://fiverr.com.evil.example/x')
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    expect(await within(dialog).findByText(/not a Fiverr link/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('records the post and shows the new status', async () => {
    let current = makeJob()
    const posted = makeJob({ status: 'posted', listing_url: 'https://www.fiverr.com/gigs/logo' })
    const { api, user } = await open(jobRoutes(current, { '/companies/c1/outsource-jobs/j1': () => jsonResponse(current), 'POST /companies/c1/outsource-jobs/j1/posted': () => { current = posted; return jsonResponse(posted) } }), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await user.click(await within(d).findByRole('button', { name: 'I posted it' }))
    const dialog = await screen.findByRole('dialog', { name: 'I posted it' })
    await user.type(within(dialog).getByLabelText(/Link to the posted job/), 'https://www.fiverr.com/gigs/logo')
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    await waitFor(() => expect(api.calls.find((c) => c.path.endsWith('/posted'))?.body).toEqual({ listing_url: 'https://www.fiverr.com/gigs/logo' }))
    expect(await within(d).findByText('Posted', { selector: 'span' })).toBeInTheDocument()
  })

  it('refuses an agreed price above the approved budget and points to raising it', async () => {
    const { api, user } = await open(jobRoutes(makeJob({ status: 'posted' })), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await user.click(await within(d).findByRole('button', { name: 'I hired someone' }))
    const dialog = await screen.findByRole('dialog', { name: 'I hired someone' })
    await user.type(within(dialog).getByLabelText(/Freelancer/), 'Pat Designer')
    await user.type(within(dialog).getByLabelText(/Agreed price/), '75')
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    expect(await within(dialog).findByText(/above the approved budget/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Raise budget/)).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('sends dollars for the hire and shows a server refusal as it is', async () => {
    const { api, user } = await open(jobRoutes(makeJob({ status: 'posted' }), { 'POST /companies/c1/outsource-jobs/j1/hired': () => apiError(409, 'A payment is already recorded') }), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await user.click(await within(d).findByRole('button', { name: 'I hired someone' }))
    const dialog = await screen.findByRole('dialog', { name: 'I hired someone' })
    await user.type(within(dialog).getByLabelText(/Freelancer/), 'Pat Designer')
    await user.type(within(dialog).getByLabelText(/Agreed price/), '45.50')
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    expect(await within(dialog).findByText('A payment is already recorded')).toBeInTheDocument()
    expect(api.calls.find((c) => c.path.endsWith('/hired'))?.body).toEqual({ freelancer_name: 'Pat Designer', freelancer_profile_url: null, agreed_price_usd: 45.5 })
  })

  it('records a payment as a verified expense and says Foundry did not pay', async () => {
    const hired = makeJob({ status: 'hired', freelancer_name: 'Pat', agreed_price_micros: 45_000_000 })
    const paid = makeJob({ status: 'hired', freelancer_name: 'Pat', agreed_price_micros: 45_000_000, paid_micros: 45_000_000, cost_kind: 'verified', ledger_entry_id: 'e1' })
    let current = hired
    const { api, user } = await open(jobRoutes(hired, { '/companies/c1/outsource-jobs/j1': () => jsonResponse(current), 'POST /companies/c1/outsource-jobs/j1/paid': () => { current = paid; return jsonResponse(paid) } }), '/c/c1?panel=outsource:j1')
    const d = await panel()
    await user.click(await within(d).findByRole('button', { name: 'I paid' }))
    const dialog = await screen.findByRole('dialog', { name: 'I paid' })
    expect(within(dialog).getByText(/only records it as a verified expense/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    await waitFor(() => expect(api.calls.find((c) => c.path.endsWith('/paid'))?.body).toEqual({ amount_usd: 45 }))
    expect(await within(d).findByText(/recorded by a person, not paid by Foundry/)).toBeInTheDocument()
    expect(within(d).queryByRole('button', { name: 'I paid' })).not.toBeInTheDocument()
  })

  it('links an outsourcing approval to its job', async () => {
    const approval = { id: 'ap1', company_id: 'c1', kind: 'outsource_job', status: 'pending', approver: 'human', requested_by_agent_id: 'a2', task_id: null, title: 'Outsource on Fiverr: Logo for Acme', reason: 'Brief', amount_micros: 50_000_000, vendor: 'Fiverr', recurrence: null, risk: 'medium', decided_by_type: null, decided_by_id: null, decided_at: null, decision_note: null, consumed_at: null, created_at: '2026-11-01T09:00:00Z', updated_at: '2026-11-01T09:00:00Z', subject_type: 'outsource_job', subject_id: 'j1' }
    const { user } = await open({ '/companies/c1/approvals': page([approval]), ...jobRoutes() }, '/c/c1?panel=approvals')
    const d = await screen.findByRole('dialog', { name: 'Approvals' })
    expect(await within(d).findByText('Outsource on Fiverr: Logo for Acme')).toBeInTheDocument()
    await user.click(within(d).getByRole('button', { name: 'Open the freelance job' }))
    await waitFor(() => expect(loc()).toBe('?panel=outsource:j1'))
  })

  it('does not offer steps to a viewer', async () => {
    await open({ '/me': role('viewer'), ...jobRoutes() }, '/c/c1?panel=outsource:j1')
    const d = await panel()
    await within(d).findByTestId('job-brief')
    expect(within(d).queryByRole('region', { name: 'Record a step' })).not.toBeInTheDocument()
  })

  it('is "not available" for a job that does not exist, not an error', async () => {
    await open({}, '/c/c1?panel=outsource:nope')
    const d = await screen.findByRole('dialog', { name: 'Freelance job' })
    expect(await within(d).findByText('This job is not available')).toBeInTheDocument()
  })
})

describe('the chat: a human-work request', () => {
  const asker = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Market Researcher', manager_agent_id: 'f1' })
  it('appears as a bubble from the agent that asked, with "I\'ll do it" and "Open"', async () => {
    const item = makeHumanWork({ title: 'Sign the NDA' })
    const claimed = vi.fn(() => jsonResponse({ ...item, pool: false, assignee_ref: 'member:m-me', assignee_name: 'Sam' }))
    const api = mockApi(routes({ '/companies/c1/agents': page([makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true }), asker]), '/companies/c1/command-center/feed': page([]), '/companies/c1/human-work': page([item]), 'POST /companies/c1/human-work/h1/claim': claimed }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const bubble = await screen.findByTestId('human-work')
    expect(within(bubble).getByRole('group', { name: 'Iris' })).toBeInTheDocument()
    expect(within(bubble).getByText(/Iris needs a person:/)).toBeInTheDocument()
    expect(within(bubble).getByText(/Sign the NDA/)).toBeInTheDocument()
    await userEvent.click(within(bubble).getByRole('button', { name: "I'll do it" }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/human-work/h1/claim')).toBe(true))
    await userEvent.click(within(bubble).getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(loc()).toBe('?panel=people-ops'))
  })

  it('does not offer taking it to a viewer, and is silent on a backend without human work', async () => {
    const api = mockApi(routes({ '/me': role('viewer'), '/companies/c1/command-center/feed': page([]), '/companies/c1/human-work': page([makeHumanWork()]) }))
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const bubble = await screen.findByTestId('human-work')
    expect(within(bubble).queryByRole('button', { name: "I'll do it" })).not.toBeInTheDocument()
    expect(within(bubble).getByRole('button', { name: 'Open' })).toBeInTheDocument()
  })
})

describe('the HR agent', () => {
  it('opens People ops from the HR agent', async () => {
    const hr = makeAgent({ id: 'hr1', name: 'Hana', role_title: 'HR Partner', role_family: 'hr' } as never)
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/agents': page([makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', reports_to_human: true, role_title: 'Founder & CEO' }), hr]), '/companies/c1/agents/hr1': hr })).impl)
    renderCanvas('/c/c1?panel=agent:hr1')
    const d = await screen.findByRole('dialog', { name: /Hana/ })
    await userEvent.click(await within(d).findByRole('button', { name: 'Open People ops' }))
    await waitFor(() => expect(loc()).toBe('?panel=people-ops'))
  })
})

describe('the org list: contractors', () => {
  const atlas = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
  const org = () => screen.getByRole('region', { name: 'Organization' })
  async function openList(over: Record<string, unknown>) {
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/agents': page([atlas]), ...over })).impl)
    renderCanvas()
    await screen.findByRole('region', { name: 'Organization' })
    const user = userEvent.setup()
    await user.click(within(org()).getByRole('button', { name: 'List' }))
    return user
  }

  it('groups hired and delivered freelancers under Contractors (not on the map) and opens the job', async () => {
    const user = await openList({
      '/companies/c1/outsource-jobs': page([
        makeJob({ id: 'j1', status: 'hired', freelancer_name: 'Pat Designer', title: 'Logo for Acme' }),
        makeJob({ id: 'j2', status: 'delivered', freelancer_name: 'Lee Writer', title: 'Docs', marketplace: 'upwork' }),
        makeJob({ id: 'j3', status: 'posted', freelancer_name: null, title: 'Not hired yet' }),
        makeJob({ id: 'j4', status: 'closed', freelancer_name: 'Gone', title: 'Old' }),
      ]),
    })
    const group = await within(org()).findByRole('list', { name: 'Contractors' })
    expect(within(group).getAllByRole('listitem')).toHaveLength(2)
    expect(within(group).getByText('Pat Designer')).toBeInTheDocument()
    expect(within(group).getByText(/Logo for Acme · Fiverr/)).toBeInTheDocument()
    expect(within(group).getByText('Hired')).toBeInTheDocument()
    expect(within(group).getByText('Delivered')).toBeInTheDocument()
    expect(within(group).queryByText('Gone')).not.toBeInTheDocument()
    await user.click(within(group).getByRole('button', { name: /Pat Designer, contractor/ }))
    await waitFor(() => expect(loc()).toBe('?panel=outsource:j1'))
  })

  it('shows no Contractors group when there are none or the backend has no outsourcing', async () => {
    await openList({ '/companies/c1/outsource-jobs': page([makeJob({ status: 'posted' })]) })
    await within(org()).findByRole('list', { name: 'People' })
    expect(within(org()).queryByRole('list', { name: 'Contractors' })).not.toBeInTheDocument()
  })
})

describe('My work: human tasks', () => {
  it('shows the category and the instructions of a task an AI employee asked a person for', async () => {
    const t = makeTask({ id: 't1', title: 'Sign the NDA', assignee_ref: 'member:m-me', assignee_agent_id: null, status: 'ready', requires_human: true, human_category: 'signature', human_instructions: 'Sign page 3 <b>today</b>' })
    vi.stubGlobal('fetch', mockApi(routes({ '/companies/c1/tasks': page([t]), '/companies/c1/approvals': page([]) })).impl)
    renderCanvas('/c/c1?panel=my-work')
    const d = await screen.findByRole('dialog', { name: 'My work' })
    const assigned = await within(d).findByRole('region', { name: 'Assigned to me' })
    expect(within(assigned).getByText('Signature')).toBeInTheDocument()
    expect(within(assigned).getByTestId('human-instructions')).toHaveTextContent('Sign page 3 <b>today</b>')
    expect(assigned.querySelector('b')).toBeNull()
  })
})

describe('the freelancer limit', () => {
  const policy = (over: Record<string, unknown> = {}) => ({ company_id: 'c1', outsource_max_job_micros: 200_000_000, ...over })
  it('shows the per-job limit and lets an owner change it (dollars to micros)', async () => {
    const saved = vi.fn(() => jsonResponse(policy({ outsource_max_job_micros: 300_000_000 })))
    const { api, user } = await open({ '/companies/c1/policy': policy(), 'PATCH /companies/c1/policy': saved, '/companies/c1/outsource-jobs': page([makeJob()]) })
    await user.click(await within(await peopleOps()).findByRole('tab', { name: 'Freelancers' }))
    const d = await peopleOps()
    expect(await within(d).findByText(/HR may draft jobs up to/)).toHaveTextContent('$200.00')
    await user.click(within(d).getByRole('button', { name: 'Change limit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Limit per freelance job' })
    const field = within(dialog).getByLabelText(/Limit per job/)
    await user.clear(field)
    await user.type(field, 'lots')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByText(/between 0 and 10,000/)).toBeInTheDocument()
    await user.clear(field)
    await user.type(field, '300')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ outsource_max_job_micros: 300_000_000 }))
  })

  it('does not offer the change to a member, and hides the line without the policy field', async () => {
    const member = makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'member' })
    const { user } = await open({ '/me': role('member'), '/companies/c1/members': page([member]), '/companies/c1/policy': policy(), '/companies/c1/outsource-jobs': page([makeJob()]) })
    await user.click(await within(await peopleOps()).findByRole('tab', { name: 'Freelancers' }))
    expect(await within(await peopleOps()).findByText(/HR may draft jobs up to/)).toBeInTheDocument()
    expect(within(await peopleOps()).queryByRole('button', { name: 'Change limit' })).not.toBeInTheDocument()
  })
})
