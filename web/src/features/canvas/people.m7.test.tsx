import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeAgent, makeMember, makeTask, mockApi, page } from '@/test/factories'
import { CreateTaskDialog } from '@/features/work/CreateDialogs'
import { AssigneeSelect } from '@/features/shared/AssigneeSelect'
import { baseRoutes, founder, iris, loc, renderCanvas } from '@/test/m5'
import { renderWithProviders } from '@/test/render'
import { CanvasProvider } from './CanvasContext'
import { OrgTree } from './OrgTree'

afterEach(() => vi.unstubAllGlobals())

const atlas = makeAgent({ id: 'f', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
const dev = makeAgent({ id: 'd', name: 'Dev', role_title: 'Backend Engineer', manager_ref: 'member:m1', manager_agent_id: null })
const maya = makeMember({ id: 'm1', display_name: 'Maya Chen', username: 'maya', title: 'Head of Product', manager_ref: 'agent:f' })
const ben = makeMember({ id: 'm2', display_name: 'Ben Ray', username: 'ben', manager_ref: 'member:m1' })
const sam = makeMember({ id: 'm-me', user_id: 'u', display_name: 'Sam', role: 'owner' })

function tree(members: ReturnType<typeof makeMember>[] | undefined, onMember = vi.fn()) {
  vi.stubGlobal(
    'fetch',
    mockApi({
      '/companies/c1/tasks': page([
        makeTask({ id: 't1', title: 'Draft the roadmap', assignee_ref: 'member:m1', assignee_agent_id: null, status: 'in_progress' }),
        makeTask({ id: 't2', title: 'Already shipped', assignee_ref: 'member:m1', assignee_agent_id: null, status: 'done' }),
      ]),
    }).impl,
  )
  renderWithProviders(
    <CanvasProvider companyId="c1">
      <div style={{ width: 900, height: 600 }}>
        <OrgTree agents={[atlas, dev]} members={members} selfUserId="u" ownerName="Sam" onOpenAgent={vi.fn()} onOpenMember={onMember} />
      </div>
    </CanvasProvider>,
  )
  return onMember
}

describe('human members in the org tree', () => {
  it('draws humans with initials and a human glyph, inside the agent hierarchy', async () => {
    tree([sam, maya, ben])
    const chart = screen.getByRole('tree', { name: 'Organization chart' })
    expect(within(chart).getByText('Maya Chen')).toBeInTheDocument()
    expect(within(chart).getByText('Ben Ray')).toBeInTheDocument()
    expect(within(chart).getByText('Head of Product')).toBeInTheDocument()
    // Sam is the signed-in Founder: the You root, not a second node
    expect(within(chart).getAllByText('Sam')).toHaveLength(1)
    const avatars = [...chart.querySelectorAll('[data-family="member"]')]
    expect(avatars).toHaveLength(2)
    expect(avatars[0]!.textContent).toContain('MC')
    expect(avatars[0]!.querySelector('.cos-avatar-badge')).not.toBeNull()
    // an agent reporting to a human is nested under them
    const items = within(chart).getAllByRole('treeitem')
    const level = (name: RegExp) => Number(items.find((i) => within(i).queryByRole('button', { name }))!.getAttribute('aria-level'))
    expect(level(/^Dev,/)).toBe(level(/^Maya Chen,/) + 1)
    expect(level(/^Ben Ray,/)).toBe(level(/^Maya Chen,/) + 1)
    expect(level(/^Maya Chen,/)).toBe(level(/^Atlas,/) + 1)
  })

  it('gives each human an accessible name with role, "Human" and open task count', async () => {
    tree([sam, maya, ben])
    await screen.findByRole('button', { name: 'Maya Chen, Head of Product, Human, 1 open task' })
    expect(screen.getByRole('button', { name: 'Ben Ray, Member, Human' })).toBeInTheDocument()
  })

  it('opens the team panel when a human is clicked', async () => {
    const onMember = tree([sam, maya, ben])
    await userEvent.click(await screen.findByRole('button', { name: /^Maya Chen,/ }))
    expect(onMember).toHaveBeenCalledWith('m1')
  })

  it('the hover card lists their open tasks only, and links to them', async () => {
    tree([sam, maya, ben])
    const user = userEvent.setup()
    await user.hover(await screen.findByRole('button', { name: /^Maya Chen,/ }))
    const card = await screen.findByText('Open tasks')
    const box = card.closest('div')!.parentElement!
    expect(await within(box).findByText('Draft the roadmap')).toBeInTheDocument()
    expect(within(box).queryByText('Already shipped')).not.toBeInTheDocument()
    expect(within(box).getByText('In progress')).toBeInTheDocument()
    expect(screen.getByText('Reports to').parentElement).toHaveTextContent('Atlas')
  })

  it('keeps the agents-only chart when the members endpoint is not there', () => {
    tree(undefined)
    const chart = screen.getByRole('tree', { name: 'Organization chart' })
    expect(within(chart).getByText('Atlas')).toBeInTheDocument()
    expect(within(chart).getByText('Dev')).toBeInTheDocument()
    expect(chart.querySelector('[data-family="member"]')).toBeNull()
    // Dev's manager_ref is ignored without members: it falls back to the agent-only rules (flagged, not lost)
    expect(within(chart).getByLabelText('No manager assigned')).toBeInTheDocument()
  })

  it('the canvas org widget includes humans when the backend has members', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        ...baseRoutes,
        '/auth/session': { state: 'authenticated', user: { id: 'u', username: 'sam', display_name: 'Sam', email: null } },
        '/companies/c1/agents': page([founder, iris]),
        '/companies/c1/members': page([sam, maya]),
        '/companies/c1/tasks': page([]),
      }).impl,
    )
    renderCanvas('/c/c1')
    const user = userEvent.setup()
    const chartBtn = await screen.findAllByRole('button', { name: /^Maya Chen,/ })
    await user.click(chartBtn[0]!)
    await waitFor(() => expect(loc()).toBe('?panel=team'))
  })

  it('the canvas org widget is unchanged without a members endpoint', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/tasks': page([]) }).impl)
    renderCanvas('/c/c1')
    await screen.findAllByRole('button', { name: /^Iris,/ })
    expect(document.querySelector('[data-family="member"]')).toBeNull()
  })
})

describe('assignee pickers include human members', () => {
  const agents = [atlas, dev]

  it('lists agents and humans in separate groups, humans as member refs', () => {
    renderWithProviders(<AssigneeSelect aria-label="Assignee" value="" agents={agents} members={[sam, maya, makeMember({ id: 'v', role: 'viewer', display_name: 'Vee' }), makeMember({ id: 'x', status: 'removed', display_name: 'Gone' })]} onChange={vi.fn()} />)
    const select = screen.getByLabelText('Assignee')
    expect(within(select).getByRole('group', { name: 'AI agents' })).toBeInTheDocument()
    const humans = within(select).getByRole('group', { name: 'Team members' })
    expect(within(humans).getAllByRole('option').map((o) => [o.textContent, (o as HTMLOptionElement).value])).toEqual([
      ['Maya Chen · Head of Product (human)', 'member:m1'],
      ['Sam · owner (human)', 'member:m-me'],
    ])
    expect(within(select).getByRole('option', { name: 'Atlas · Founder & CEO' })).toHaveValue('f')
  })

  it('renders the plain agents-only list when there are no members', () => {
    renderWithProviders(<AssigneeSelect aria-label="Assignee" value="" agents={agents} members={[]} onChange={vi.fn()} />)
    const select = screen.getByLabelText('Assignee')
    expect(within(select).queryByRole('group')).not.toBeInTheDocument()
    expect(within(select).getAllByRole('option')).toHaveLength(3)
  })

  function createDialog(extra: Record<string, unknown> = {}) {
    const api = mockApi({
      '/companies/c1/agents': page([atlas, dev]),
      '/companies/c1/projects': page([]),
      '/companies/c1/members': page([sam, maya]),
      'POST /companies/c1/tasks': (_u: URL, init?: RequestInit) => jsonResponse({ ...makeTask({ id: 'new' }), ...JSON.parse(String(init?.body)) }, 201),
      ...extra,
    })
    vi.stubGlobal('fetch', api.impl)
    renderWithProviders(
      <CanvasProvider companyId="c1">
        <CreateTaskDialog companyId="c1" onClose={vi.fn()} />
      </CanvasProvider>,
    )
    return api
  }

  it('assigning a human sends assignee_ref and no agent id', async () => {
    const api = createDialog()
    const dialog = await screen.findByRole('dialog', { name: 'Create task' })
    await userEvent.type(within(dialog).getByLabelText(/Title/), 'Call the supplier')
    const select = await within(dialog).findByLabelText('Assignee')
    await waitFor(() => expect(within(select).getByRole('group', { name: 'Team members' })).toBeInTheDocument())
    await userEvent.selectOptions(select, 'member:m1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST')).toBe(true))
    const body = api.calls.find((c) => c.method === 'POST')!.body as Record<string, unknown>
    expect(body.assignee_ref).toBe('member:m1')
    expect(body.assignee_agent_id).toBeNull()
  })

  it('assigning an agent keeps the request exactly as before (no assignee_ref)', async () => {
    const api = createDialog()
    const dialog = await screen.findByRole('dialog', { name: 'Create task' })
    await userEvent.type(within(dialog).getByLabelText(/Title/), 'Ship it')
    const select = await within(dialog).findByLabelText('Assignee')
    await waitFor(() => expect(within(select).getByRole('option', { name: /Dev/ })).toBeInTheDocument())
    await userEvent.selectOptions(select, 'd')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST')).toBe(true))
    const body = api.calls.find((c) => c.method === 'POST')!.body as Record<string, unknown>
    expect(body.assignee_agent_id).toBe('d')
    expect('assignee_ref' in body).toBe(false)
  })

  it('degrades to the agents-only picker when the members endpoint is missing', async () => {
    createDialog({ '/companies/c1/members': undefined })
    const dialog = await screen.findByRole('dialog', { name: 'Create task' })
    const select = await within(dialog).findByLabelText('Assignee')
    await waitFor(() => expect(within(select).getByRole('option', { name: /Dev/ })).toBeInTheDocument())
    expect(within(select).queryByRole('group')).not.toBeInTheDocument()
  })
})

describe('tasks assigned to humans read correctly across the UI', () => {
  it('the Tasks panel shows the human assignee, not "Unassigned", and can filter by them', async () => {
    const api = mockApi({
      ...baseRoutes,
      '/companies/c1/members': page([sam, maya]),
      '/companies/c1/tasks': page([makeTask({ id: 't1', title: 'Draft the roadmap', assignee_ref: 'member:m1', assignee_agent_id: null })]),
      '/companies/c1/projects': page([]),
    })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=tasks')
    const d = await screen.findByRole('dialog', { name: 'Tasks' })
    const row = (await within(d).findByText('Draft the roadmap')).closest('tr')!
    await within(row).findByText('Maya Chen')
    expect(within(row).queryByText('Unassigned')).not.toBeInTheDocument()
    await userEvent.selectOptions(within(d).getByLabelText('Filter by assignee'), 'member:m1')
    await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/tasks' && c.method === 'GET')).toBe(true))
  })

  it('the task panel names the human assignee', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        ...baseRoutes,
        '/companies/c1/members': page([sam, maya]),
        '/companies/c1/tasks/t1': makeTask({ id: 't1', title: 'Draft the roadmap', assignee_ref: 'member:m1', assignee_agent_id: null }),
        '/companies/c1/tasks': page([]),
        '/companies/c1/projects': page([]),
      }).impl,
    )
    renderCanvas('/c/c1?panel=task:t1')
    const d = await screen.findByRole('dialog', { name: 'Draft the roadmap' })
    expect(await within(d).findByText('Maya Chen')).toBeInTheDocument()
  })
})

describe('the owner menu', () => {
  const authed = { state: 'authenticated', user: { id: 'u', username: 'sam', display_name: 'Sam', email: null } }

  it('links to my work, team, account and log out when accounts exist', async () => {
    const api = mockApi({ ...baseRoutes, '/auth/session': authed, 'POST /auth/logout': () => new Response(null, { status: 204 }) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Account:/ }))
    expect(await screen.findByText(/@sam/)).toBeInTheDocument()
    await user.click(screen.getByRole('menuitem', { name: 'Team' }))
    await waitFor(() => expect(loc()).toBe('?panel=team'))
    await user.click(screen.getByRole('button', { name: /Account:/ }))
    await user.click(await screen.findByRole('menuitem', { name: 'Log out' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/auth/logout')).toBe(true))
  })

  it('offers none of that on the legacy owner-token backend', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1')
    await userEvent.click(await screen.findByRole('button', { name: /Account:/ }))
    expect(await screen.findByRole('menuitem', { name: /Settings/ })).toBeInTheDocument()
    for (const name of ['Team', 'Account', 'My work', 'Log out']) expect(screen.queryByRole('menuitem', { name })).not.toBeInTheDocument()
  })
})
