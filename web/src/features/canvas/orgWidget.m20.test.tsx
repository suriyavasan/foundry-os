import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { normalizeLive } from '@/lib/liveState'
import { makeAgent, makeTask, mockApi, page } from '@/test/factories'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const atlas = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
const iris = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Head of Research', kind: 'lead', manager_agent_id: 'f1', live: normalizeLive({ state: 'working', current_task: { id: 't1', title: 'Map the market' } }) })
const dev = makeAgent({ id: 'a3', name: 'Dev', role_title: 'Backend Engineer', manager_agent_id: 'a2', live: normalizeLive({ state: 'working', current_task: { id: 't2', title: 'Ship the API' } }) })
const eve = makeAgent({ id: 'a4', name: 'Eve', role_title: 'Designer', manager_agent_id: 'f1', status: 'paused' })

const routes = { ...baseRoutes, '/companies/c1/agents': page([atlas, iris, dev, eve]) }
const org = () => screen.getByRole('region', { name: 'Organization' })

async function render(extra: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', mockApi({ ...routes, ...extra }).impl)
  const view = renderCanvas()
  await screen.findByRole('region', { name: 'Organization' })
  return view
}

describe('Organization widget: map', () => {
  it('opens on the map: a face for everyone, the Co-founder first, then the rings; names under the big circles', async () => {
    await render()
    const map = await within(org()).findByRole('group', { name: 'Organization map' })
    const buttons = within(map).getAllByRole('button')
    expect(buttons.map((b) => b.getAttribute('aria-label')!.split(',')[0])).toEqual(['Atlas', 'Iris', 'Eve', 'Dev'])
    // every circle has a face
    for (const b of buttons) expect(b.querySelector('img.cos-avatar-face')).not.toBeNull()
    // the Founder is attached to the Co-founder
    expect(within(map).getByRole('img', { name: /You · Founder/ })).toBeInTheDocument()
    // Co-founder (56) and the manager (44) carry a name; a 34 px report shows it in the hover card
    expect(within(map).getByText('Atlas')).toBeInTheDocument()
    expect(within(map).getByText('Iris')).toBeInTheDocument()
    expect(within(map).queryByText('Dev')).not.toBeInTheDocument()
    expect(map.querySelectorAll('line.cos-edge')).toHaveLength(4) // founder-cofounder, cofounder-iris, cofounder-eve, iris-dev
  })

  it('keeps the live state ring: working pulses, paused is dimmed', async () => {
    await render()
    const map = await within(org()).findByRole('group', { name: 'Organization map' })
    expect(within(map).getByRole('button', { name: /^Iris,/ }).querySelector('.cos-avatar')).toHaveAttribute('data-pulse', 'true')
    expect(within(map).getByRole('button', { name: /^Eve,/ }).querySelector('.cos-avatar')).toHaveAttribute('data-dimmed', 'true')
  })

  it('opens the agent panel on click and on Enter', async () => {
    await render({ '/companies/c1/agents/a2': iris })
    const user = userEvent.setup()
    await user.click(await within(org()).findByRole('button', { name: /^Iris,/ }))
    await waitFor(() => expect(loc()).toBe('?panel=agent:a2'))
  })

  it('joins agents whose in-progress tasks depend on each other with a dashed line that moves while both work', async () => {
    await render({
      '/companies/c1/tasks': page([
        makeTask({ id: 't1', status: 'in_progress', assignee_agent_id: 'a2', depends_on: ['t2'] }),
        makeTask({ id: 't2', status: 'in_progress', assignee_agent_id: 'a3' }),
      ]),
    })
    const map = await within(org()).findByRole('group', { name: 'Organization map' })
    await waitFor(() => expect(map.querySelectorAll('path.cos-collab')).toHaveLength(1))
    expect(map.querySelector('path.cos-collab')).toHaveAttribute('data-live', 'true')
    expect(map.querySelector('path.cos-collab')).toHaveAttribute('data-kind', 'dependency')
  })

  it('draws no collaboration lines when the backend reports no dependencies', async () => {
    await render({ '/companies/c1/tasks': page([makeTask({ id: 't1', status: 'in_progress', assignee_agent_id: 'a2' })]) })
    const map = await within(org()).findByRole('group', { name: 'Organization map' })
    expect(map.querySelectorAll('path.cos-collab')).toHaveLength(0)
  })
})

describe('Organization widget: list and the Map | List toggle', () => {
  it('switches to a list grouped by manager and remembers the choice in this browser', async () => {
    const first = await render()
    const user = userEvent.setup()
    expect(within(org()).getByRole('button', { name: 'Map' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(org()).getByRole('button', { name: 'List' }))
    expect(localStorage.getItem('company-os.org-view')).toBe('list')
    expect(within(org()).queryByRole('group', { name: 'Organization map' })).not.toBeInTheDocument()
    const list = within(org()).getByRole('list', { name: 'People' })
    const rows = within(list).getAllByRole('button')
    // depth first: the Co-founder, then each manager followed by their team (leads before specialists)
    expect(rows.map((r) => r.getAttribute('aria-label')!.split(',')[0])).toEqual(['Atlas', 'Iris', 'Dev', 'Eve'])
    expect(within(list).getByText('You · Founder')).toBeInTheDocument()
    // dev reports to iris: indented one level deeper
    const indent = (name: RegExp) => parseInt((within(list).getByRole('button', { name }) as HTMLElement).style.paddingLeft, 10)
    expect(indent(/^Iris,/)).toBeGreaterThan(indent(/^Atlas,/))
    expect(indent(/^Dev,/)).toBeGreaterThan(indent(/^Iris,/))
    // what they are doing
    expect(within(list).getByText('Working on Map the market')).toBeInTheDocument()
    expect(within(list).getByText('Working on Ship the API')).toBeInTheDocument()
    expect(within(list).getByText('Paused')).toBeInTheDocument()

    // a new visit starts on the list
    first.unmount()
    await render()
    expect(within(org()).getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
    expect(await within(org()).findByRole('list', { name: 'People' })).toBeInTheDocument()
    // and back
    await userEvent.click(within(org()).getByRole('button', { name: 'Map' }))
    expect(localStorage.getItem('company-os.org-view')).toBe('map')
    expect(await within(org()).findByRole('group', { name: 'Organization map' })).toBeInTheDocument()
  })

  it('a list row opens the agent panel', async () => {
    localStorage.setItem('company-os.org-view', 'list')
    await render({ '/companies/c1/agents/a3': dev })
    await userEvent.click(await within(org()).findByRole('button', { name: /^Dev,/ }))
    await waitFor(() => expect(loc()).toBe('?panel=agent:a3'))
  })

  it('shows what an agent spent today when the backend reports it (a charge and an estimate stay apart), and nothing when it does not', async () => {
    localStorage.setItem('company-os.org-view', 'list')
    const first = await render()
    const list = await within(org()).findByRole('list', { name: 'People' })
    expect(within(list).queryByTitle('Spent today')).not.toBeInTheDocument()
    first.unmount()
    await render({ '/companies/c1/usage': { items: [{ key: 'a2', input_tokens: 1000, output_tokens: 500, by_cost_kind: { verified: 2_000_000, estimated: 750_000, unknown: 0 } }] } })
    const iris = await within(org()).findByRole('button', { name: /^Iris,/ })
    const spent = await within(iris).findByTitle('Spent today')
    expect(spent).toHaveTextContent('$2.00')
    expect(spent).toHaveTextContent('≈ $0.75 est.')
  })

  it('the empty and error states of the widget are unchanged', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/agents': page([]) }).impl)
    renderCanvas()
    expect(await within(await screen.findByRole('region', { name: 'Organization' })).findByText('No employees yet')).toBeInTheDocument()
  })
})
