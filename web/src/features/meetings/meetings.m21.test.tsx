import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeAgent, mockApi, page } from '@/test/factories'
import { ana, makeMeeting, makeMeetingDetail, message, participant, raj } from '@/test/m21'
import { apiError, baseRoutes, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const viewerMe = { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'viewer' }] }

const concluded = makeMeetingDetail({
  status: 'concluded',
  round: 2,
  ended_at: '2026-10-30T09:20:00Z',
  summary: 'We will launch on the 14th.\nNot agreed (no task created): Book a venue (Atlas)',
  outcome: {
    decisions: [{ title: 'Launch on the 14th', rationale: 'Fits the press cycle' }],
    action_items: [
      { id: 'i1', title: 'Write the launch post', owner_agent_id: 'a2', accepted: true, task_id: 't9' },
      { id: 'i2', title: 'Book a venue', owner_agent_id: 'f1', accepted: false },
      { id: 'i3', title: 'Buy ads', owner_agent_id: 'a2', accepted: false, refused: 'the open-task cap was reached' },
    ],
  },
  messages: [
    message({ id: 'g1', body: 'I propose the **14th**.\n\n<img src=x onerror=alert(1)>', proposals: { proposed: [{ id: 'i1', title: 'Write the launch post', owner_agent_id: 'a2' }] } }),
    message({ id: 'g2', agent_id: 'a2', agent_name: 'Iris', body: 'Works for me.', proposals: { accepted: ['i1'] } }),
    message({ id: 'g3', agent_id: 'a2', agent_name: 'Iris', round: 2, body: '[no contribution: the turn timed out]', proposals: { pass: true, auto: true } }),
    message({ id: 'g4', round: 2, kind: 'synthesis', body: 'Wrapping up.' }),
  ],
})

async function open(detail: unknown, extra: Record<string, unknown> = {}, me?: unknown) {
  const api = mockApi({ ...baseRoutes, ...(me ? { '/me': me } : {}), '/companies/c1/meetings/m1': detail, ...extra })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=meeting:m1')
  const dialog = await screen.findByRole('dialog')
  return { api, dialog, user: userEvent.setup() }
}

describe('meeting panel', () => {
  it('shows a loading state, then the agenda and participants with faces', async () => {
    const { dialog } = await open(makeMeetingDetail())
    expect(await within(dialog).findByRole('heading', { name: 'Agenda' })).toBeInTheDocument()
    expect(within(dialog).getByText('Decide the launch date and who owns what.')).toBeInTheDocument()
    expect(within(dialog).getByText(/A date and an owner for each part/)).toBeInTheDocument()
    const people = within(dialog).getByRole('list', { name: 'Participants' })
    expect(within(people).getByText('Atlas')).toBeInTheDocument()
    expect(within(people).getByText('organizer')).toBeInTheDocument()
    expect(within(people).getByText('Market Researcher')).toBeInTheDocument()
    await waitFor(() => expect(people.querySelector('img.cos-avatar-face')).not.toBeNull())
  })

  it('is "not available" when the meeting or the meetings API does not exist (404), not an error', async () => {
    const { dialog } = await open(undefined)
    expect(await within(dialog).findByText('This meeting is not available')).toBeInTheDocument()
    expect(within(dialog).getByText(/Arrives in M21/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('shows an error with retry on a server failure', async () => {
    const { dialog } = await open(() => apiError(500, 'boom'))
    expect(await within(dialog).findByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('says nobody has spoken yet for an empty transcript, and that the outcome is pending', async () => {
    const { dialog } = await open(makeMeetingDetail({ status: 'scheduled', round: 0, started_at: null }))
    expect(await within(dialog).findByText('Nobody has spoken yet')).toBeInTheDocument()
    expect(within(dialog).getByText(/appear when the meeting ends/)).toBeInTheDocument()
  })

  it('renders the transcript as chat bubbles with faces, markdown and no raw HTML, and passes as a quiet line', async () => {
    const { dialog } = await open(concluded)
    const log = await within(dialog).findByRole('list', { name: 'Transcript' })
    await waitFor(() => expect(within(log).getAllByTestId('meeting-message')).toHaveLength(3))
    expect(within(log).getAllByRole('separator').map((s) => s.getAttribute('aria-label'))).toEqual(['Round 1', 'Round 2'])
    const first = within(log).getAllByRole('group', { name: 'Atlas' })[0]!
    expect(within(first).getByText('14th').tagName).toBe('STRONG')
    expect(first.querySelector('img[src="x"]')).toBeNull()
    expect(first.querySelector('[onerror]')).toBeNull()
    expect(within(first).getByText(/Proposes: Write the launch post \(Iris\)/)).toBeInTheDocument()
    await waitFor(() => expect(first.querySelector('img.cos-avatar-face')).not.toBeNull())
    expect(within(log).getByText('Accepted an item')).toBeInTheDocument()
    expect(within(log).getByTestId('meeting-pass')).toHaveTextContent('Iris passed · the turn timed out')
    expect(within(log).getByText('Wrap-up')).toBeInTheDocument()
  })

  it('shows the outcome: summary, decisions and what became of each action item', async () => {
    const { dialog, user } = await open(concluded)
    const decisions = await within(dialog).findByRole('region', { name: 'Decisions' })
    expect(within(decisions).getByText('Launch on the 14th')).toBeInTheDocument()
    expect(within(decisions).getByText('Fits the press cycle')).toBeInTheDocument()
    const items = within(dialog).getByRole('region', { name: 'Action items' })
    const rows = within(items).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('Task created')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Not agreed')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('No task: the open-task cap was reached')).toBeInTheDocument()
    await user.click(within(rows[0]!).getByRole('button', { name: 'Open task' }))
    await waitFor(() => expect(loc()).toBe('?panel=task:t9'))
  })

  it('shows why a cancelled meeting ended', async () => {
    const { dialog } = await open(makeMeetingDetail({ status: 'cancelled', cancel_reason: 'No longer needed' }))
    expect(await within(dialog).findByText(/This meeting was cancelled: No longer needed/)).toBeInTheDocument()
  })

  it('lets an owner cancel a running meeting after a confirmation, sending the reason', async () => {
    const cancelled = makeMeetingDetail({ status: 'cancelled', cancel_reason: 'Not needed' })
    let current = makeMeetingDetail()
    const { api, dialog, user } = await open(() => jsonResponse(current), {
      'POST /companies/c1/meetings/m1/cancel': () => {
        current = cancelled
        return jsonResponse(cancelled)
      },
    })
    await user.click(await within(dialog).findByRole('button', { name: 'Cancel meeting' }))
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
    const confirm = await screen.findByText(/nothing is agreed or created/)
    const box = confirm.closest<HTMLElement>('[role="dialog"], [role="alertdialog"]')!
    await user.type(within(box).getByLabelText(/Reason/), 'Not needed')
    await user.click(within(box).getByRole('button', { name: 'Cancel meeting' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/meetings/m1/cancel')).toBe(true))
    expect(api.calls.find((c) => c.method === 'POST')!.body).toEqual({ reason: 'Not needed' })
    expect(await within(dialog).findByText(/This meeting was cancelled: Not needed/)).toBeInTheDocument()
  })

  it('shows the server message when the cancel is refused (409)', async () => {
    const { dialog, user } = await open(makeMeetingDetail(), { 'POST /companies/c1/meetings/m1/cancel': () => apiError(409, 'The meeting has already ended') })
    await user.click(await within(dialog).findByRole('button', { name: 'Cancel meeting' }))
    const confirm = await screen.findByText(/nothing is agreed or created/)
    await user.click(within(confirm.closest<HTMLElement>('[role="dialog"], [role="alertdialog"]')!).getByRole('button', { name: 'Cancel meeting' }))
    expect(await screen.findByText('The meeting has already ended')).toBeInTheDocument()
  })

  it('does not offer cancel to a viewer', async () => {
    const a = await open(makeMeetingDetail(), {}, viewerMe)
    await within(a.dialog).findByRole('heading', { name: 'Agenda' })
    expect(within(a.dialog).queryByRole('button', { name: 'Cancel meeting' })).not.toBeInTheDocument()
  })

  it('does not offer cancel once the meeting has concluded, even to an owner', async () => {
    const { dialog } = await open(concluded)
    await within(dialog).findByRole('heading', { name: 'Agenda' })
    expect(within(dialog).queryByRole('button', { name: 'Cancel meeting' })).not.toBeInTheDocument()
  })
})

describe('live activity: running meetings', () => {
  it('shows one line per running meeting and opens it', async () => {
    const api = mockApi({ ...baseRoutes, '/companies/c1/meetings': page([makeMeeting({ participants: [ana, raj, participant({ agent_id: 'a3', name: 'Lee', position: 2 })] })]) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const line = await screen.findByRole('button', { name: 'Meeting · Atlas, Iris, Lee · Plan the launch · round 2/2' })
    expect(api.calls.some((c) => c.path === '/companies/c1/meetings')).toBe(true)
    await userEvent.click(line)
    expect(loc()).toBe('?panel=meeting:m1')
  })

  it('shows nothing when no meeting is running or the backend has no meetings', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes }).impl)
    renderCanvas()
    await screen.findByRole('region', { name: 'Live activity' })
    expect(screen.queryByRole('list', { name: 'Meetings in progress' })).not.toBeInTheDocument()
  })
})

describe('organization map: meeting lines', () => {
  const atlas = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
  const iris = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Head of Research', kind: 'lead', manager_agent_id: 'f1' })
  const dev = makeAgent({ id: 'a3', name: 'Dev', role_title: 'Engineer', manager_agent_id: 'a2' })
  const routes = { ...baseRoutes, '/companies/c1/agents': page([atlas, iris, dev]) }

  it('draws an animated dashed line between the participants of a running meeting', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/meetings': page([makeMeeting({ participants: [ana, raj] })]) }).impl)
    renderCanvas()
    const map = await screen.findByRole('group', { name: 'Organization map' })
    await waitFor(() => expect(map.querySelectorAll('path.cos-collab[data-kind="meeting"]')).toHaveLength(1))
    expect(map.querySelector('path.cos-collab[data-kind="meeting"]')).toHaveAttribute('data-live', 'true')
    expect(map.querySelectorAll('line.cos-edge').length).toBeGreaterThan(0)
  })

  it('draws no meeting line without a running meeting', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/meetings': page([]) }).impl)
    renderCanvas()
    const map = await screen.findByRole('group', { name: 'Organization map' })
    await waitFor(() => expect(map.querySelectorAll('line.cos-edge').length).toBeGreaterThan(0))
    expect(map.querySelectorAll('path.cos-collab')).toHaveLength(0)
  })
})

describe('agent panel: Meetings tab', () => {
  it('lists the agent’s meetings filtered by agent, and opens one', async () => {
    const api = mockApi({
      ...baseRoutes,
      '/companies/c1/agents/f1': makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', reports_to_human: true }),
      '/companies/c1/meetings': page([makeMeeting({ status: 'concluded', ended_at: '2026-10-30T09:20:00Z' })]),
    })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=agent:f1')
    const dialog = await screen.findByRole('dialog', { name: /Atlas/ })
    await userEvent.click(await within(dialog).findByRole('tab', { name: 'Meetings' }))
    const list = await within(dialog).findByRole('list', { name: 'Recent meetings' })
    expect(within(list).getByText('Concluded')).toBeInTheDocument()
    expect(api.calls.some((c) => c.path === '/companies/c1/meetings')).toBe(true)
    await userEvent.click(within(list).getByRole('button', { name: /Plan the launch/ }))
    expect(loc()).toBe('?panel=meeting:m1')
  })

  it('says meetings are not built yet on a backend without them', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/agents/f1': makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', reports_to_human: true }) }).impl)
    renderCanvas('/c/c1?panel=agent:f1')
    const dialog = await screen.findByRole('dialog', { name: /Atlas/ })
    await userEvent.click(await within(dialog).findByRole('tab', { name: 'Meetings' }))
    expect(await within(dialog).findByText('Meetings are not built yet')).toBeInTheDocument()
  })
})

