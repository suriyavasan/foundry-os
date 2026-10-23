import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeApproval, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const human = makeApproval({ id: 'h1', title: 'Register acme.io', kind: 'external_purchase', tier: 'external_commitment', approver: 'human', amount_micros: 12_000_000 })
const cofounder = makeApproval({ id: 'f1', title: 'Buy a stock photo', kind: 'external_purchase', tier: 'spend', approver: 'founder', amount_micros: 4_000_000 })
const peer = makeApproval({
  id: 'p1',
  title: 'Send investor email',
  tier: 'external_communication',
  approver: 'peer',
  reviewer_agent_id: 'a2',
  action: 'email.send',
  outbound: { channel: 'email', to: 'dana@investor.example', subject: 'Intro', body: 'Hi Dana,\nHere is our pitch.\n<b>not html</b>' },
  dlp_findings: [{ kind: 'email', severity: 'medium', description: 'Personal email address', count: 2, match: 'dana.private@gmail.com' } as never],
})
const routes = { ...baseRoutes, '/companies/c1/approvals': page([human, cofounder, peer]) }

describe('Approvals panel grouped by who decides and risk tier', () => {
  it('has a lane per decider, with the tier shown inside each', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const you = await within(dialog).findByRole('region', { name: 'Waiting for you' })
    expect(within(you).getByText(/You decide \(1\)/)).toBeInTheDocument()
    expect(within(within(you).getByRole('group', { name: 'Tier external_commitment' })).getByText('External commitment')).toBeInTheDocument()
    expect(within(you).getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(within(you).getByRole('button', { name: 'Request changes' })).toBeInTheDocument()
    expect(within(you).getByRole('button', { name: 'Reject' })).toBeInTheDocument()

    const co = within(dialog).getByRole('region', { name: 'Decided by the AI Co-founder' })
    expect(within(co).getByText(/AI Co-founder decides \(1\)/)).toBeInTheDocument()
    expect(within(co).getByText('Spend')).toBeInTheDocument()
    expect(within(co).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()

    const pr = within(dialog).getByRole('region', { name: 'Waiting for a peer reviewer' })
    expect(within(pr).getByText(/Peer review \(1\)/)).toBeInTheDocument()
    expect(within(pr).getByText('External communication')).toBeInTheDocument()
  })

  it('shows a peer review’s exact outbound text and DLP findings, read-only', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const pr = await within(dialog).findByRole('region', { name: 'Waiting for a peer reviewer' })
    const content = within(pr).getByRole('group', { name: 'Outbound content for Send investor email' })
    expect(within(content).getByText(/Exact outbound content · Email to dana@investor.example/)).toBeInTheDocument()
    expect(within(content).getByText('Intro')).toBeInTheDocument()
    // the body is plain text: markup in it is shown, not interpreted
    const pre = content.querySelector('pre')!
    expect(pre.textContent).toBe('Hi Dana,\nHere is our pitch.\n<b>not html</b>')
    expect(pre.querySelector('b')).toBeNull()
    // findings say what was found, never the matched text
    expect(within(content).getByText('Personal email address')).toBeInTheDocument()
    expect(within(content).getByText('2 matches')).toBeInTheDocument()
    expect(within(content).getByText('medium severity')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('dana.private@gmail.com')
    // informational for the human: no decision buttons
    expect(within(pr).queryByRole('button', { name: /Approve|Reject|Request changes/ })).not.toBeInTheDocument()
    expect(within(pr).getByText(/peer reviewer agent checks this content first/)).toBeInTheDocument()
  })

  it('says "No findings" when the scan was clean', async () => {
    const clean = makeApproval({ id: 'p2', title: 'Post update', approver: 'peer', outbound: { body: 'We shipped v2.' }, dlp_findings: [] })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([clean]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    expect(await within(dialog).findByText('No findings.')).toBeInTheDocument()
  })

  it('keeps approve / reject / request-changes working for human approvals', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/approvals/h1/approve': () => new Response(JSON.stringify({ ...human, status: 'approved' }), { status: 200, headers: { 'Content-Type': 'application/json' } }) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: 'Approve' }))
    const decision = await screen.findByRole('dialog', { name: /Approve: Register acme.io/ })
    await user.click(within(decision).getByRole('button', { name: 'Approve' }))
    await vi.waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/approvals/h1/approve')).toBe(true))
  })

  it('does not show a peer lane when there are no peer reviews', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([human]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    await within(dialog).findByRole('region', { name: 'Waiting for you' })
    expect(within(dialog).queryByRole('region', { name: 'Waiting for a peer reviewer' })).not.toBeInTheDocument()
  })
})

describe('Approvals widget', () => {
  it('counts peer reviews separately from what needs you', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1')
    const box = await screen.findByRole('region', { name: 'Approvals' })
    expect(await within(box).findByText('1 need you · 1 in peer review')).toBeInTheDocument()
    expect(within(box).getByRole('region', { name: 'Waiting for a peer reviewer' })).toBeInTheDocument()
    expect(within(box).getByRole('group', { name: 'Outbound content for Send investor email' })).toBeInTheDocument()
  })
})
