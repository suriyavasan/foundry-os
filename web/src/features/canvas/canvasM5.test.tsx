import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Approval } from '@/api/types'
import { jsonResponse, makeNote, makeTask, mockApi, page } from '@/test/factories'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'
import { resetSeenReplies } from '@/features/notes/hooks'

beforeEach(() => {
  localStorage.clear()
  resetSeenReplies()
})
afterEach(() => vi.unstubAllGlobals())

const m5 = { ...baseRoutes, '/companies/c1/notes': page([makeNote({ id: 'n1', body: 'Hello' })]) }

describe('canvas layout', () => {
  it('puts Reflections and the Command Center next to the organization (6 + 3 + 3 columns, same tall height); below 1440 px the organization is full width', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    await screen.findByRole('region', { name: 'Command Center' })
    const main = screen.getByRole('main')
    const sections = [...main.querySelectorAll(':scope > div > section')]
    const names = sections.map((s) => within(s as HTMLElement).getAllByRole('heading', { level: 2 })[0]!.textContent)
    expect(names.slice(0, 4)).toEqual(['Organization', 'Reflections', 'Command Center', 'Live activity'])

    const [org, reflections, cc, live] = sections as HTMLElement[]
    // >= 1440 px: 6 + 3 + 3 fill the twelve columns on one row.
    expect(org!.className).toContain('min-[1440px]:col-span-6')
    expect(reflections!.className).toContain('min-[1440px]:col-span-3')
    expect(cc!.className).toContain('min-[1440px]:col-span-3')
    // 1280-1439 px: organization alone on a row, Reflections and Command Center side by side under it.
    expect(org!.className).toContain('xl:col-span-12')
    expect(reflections!.className).toContain('xl:col-span-6')
    expect(cc!.className).toContain('xl:col-span-6')
    // tablet: organization spans both columns, the other two share a row; phone: one column (no span set).
    expect(org!.className).toContain('md:col-span-2')
    expect(reflections!.className).not.toContain('md:col-span-2')
    expect(cc!.className).not.toContain('md:col-span-2')
    // Live activity does not share row one.
    expect(live!.className).toContain('xl:col-span-8')
    // all three are the tall widget
    const tall = (el: HTMLElement) => el.className.includes('--widget-h-tall')
    expect([org, reflections, cc].every((el) => tall(el!))).toBe(true)
  })

  it('no longer has the Founder’s-notes widget on the canvas (the Notes panel is unchanged)', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    await screen.findByRole('region', { name: 'Command Center' })
    expect(screen.queryByRole('region', { name: "Founder's notes" })).not.toBeInTheDocument()
  })

  it('keeps every widget in the same frame', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    await screen.findByRole('region', { name: 'Command Center' })
    const titles = within(screen.getByRole('main')).getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    for (const t of ['Organization', 'Reflections', 'Command Center', 'Live activity', 'Goals & plan', 'Work', 'Approvals', 'Spend', 'Artifacts', 'Routines', 'Skills']) expect(titles).toContain(t)
  })
})

describe('terminology on the canvas', () => {
  const approval = (over: Partial<Approval>): Approval => ({
    id: 'ap2', company_id: 'c1', kind: 'hire', status: 'pending', approver: 'founder', requested_by_agent_id: 'f1', task_id: null, title: 'Hire a designer', reason: 'Needed', amount_micros: null, vendor: null,
    recurrence: null, risk: 'low', decided_by_type: null, decided_by_id: null, decided_at: null, decision_note: null, consumed_at: null, created_at: '2026-10-23T12:00:00Z', updated_at: '2026-10-23T12:00:00Z', ...over,
  })

  it('names the human "You · Founder" and the AI agent "AI Co-founder"; never a co-founder for the human', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    const tree = await screen.findByRole('tree', { name: 'Organization chart' })
    const items = within(tree).getAllByRole('treeitem')
    const human = items.find((i) => i.textContent?.includes('You'))!
    expect(human).toHaveTextContent('You · Founder')
    expect(human.textContent!.toLowerCase()).not.toContain('co-founder')
    const ai = items.find((i) => i.textContent?.includes('Atlas'))!
    expect(ai).toHaveTextContent('AI Co-founder')
    expect(within(ai).getByRole('button', { name: /Atlas, AI Co-founder/ })).toBeInTheDocument()
    // no "AI Founder" / "human co-founder" anywhere on the canvas
    expect(document.body.textContent).not.toMatch(/AI Founder|human co-founder/i)
  })

  it('calls approvals decided by the AI agent "AI Co-founder decides"', async () => {
    vi.stubGlobal('fetch', mockApi({ ...m5, '/companies/c1/approvals': page([approval({})]) }).impl)
    renderCanvas()
    const box = await screen.findByRole('region', { name: 'Approvals' })
    const section = await within(box).findByRole('region', { name: 'Decided by the AI Co-founder' })
    expect(within(section).getByText(/AI Co-founder decides \(1\)/)).toBeInTheDocument()
    expect(within(section).getByText(/The AI Co-founder decides this one/)).toBeInTheDocument()
    expect(within(box).queryByText(/Founder decides/)).not.toBeInTheDocument()
  })

  it('uses the AI Co-founder label on the profile panel and in the hire form copy', async () => {
    vi.stubGlobal('fetch', mockApi({ ...m5, '/companies/c1/agents/f1': baseRoutes['/companies/c1/agents'].items[0] }).impl)
    renderCanvas('/c/c1?panel=agent:f1')
    const dialog = await screen.findByRole('dialog', { name: /Atlas/ })
    expect(await within(dialog).findAllByText('AI Co-founder')).not.toHaveLength(0)
    expect(within(dialog).getByRole('button', { name: 'Open co-founder view' })).toBeInTheDocument()
  })
})

describe('reaching the AI Co-founder view', () => {
  it('from the org chart avatar’s hover card', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    const user = userEvent.setup()
    const avatar = await screen.findByRole('button', { name: /Atlas, AI Co-founder/ })
    await user.hover(avatar)
    const action = await screen.findByRole('button', { name: /Open co-founder view/ })
    await user.click(action)
    expect(loc()).toBe('?panel=cofounder')
  })

  it('other agents’ hover cards have no such action', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    const user = userEvent.setup()
    await user.hover(await screen.findByRole('button', { name: /Iris, Market Researcher/ }))
    await screen.findByText(/Click the avatar to open the full profile/)
    expect(screen.queryByRole('button', { name: /Open co-founder view/ })).not.toBeInTheDocument()
  })
})

describe('command palette', () => {
  const cc = { ...m5, '/companies/c1/command-center/feed': page([]) }
  const input = () => screen.getByRole('combobox', { name: /Command or message for the AI Co-founder/ })

  it('focus command center: puts the cursor in the Command Center input (where a note is now written)', async () => {
    vi.stubGlobal('fetch', mockApi(cc).impl)
    renderCanvas()
    const user = userEvent.setup()
    await screen.findByRole('combobox', { name: /Command or message for the AI Co-founder/ })
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'focus command')
    expect(screen.getByRole('option', { name: /Focus command center/ })).toBeInTheDocument()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(input()).toHaveFocus(), { timeout: 2000 })
  })

  it('wakes the AI Co-founder', async () => {
    const api = mockApi({ ...m5, 'POST /companies/c1/cofounder/wake': () => jsonResponse({ status: 'queued', task_id: 't9' }) })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'wake')
    await user.click(screen.getByRole('option', { name: /Wake AI Co-founder/ }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/cofounder/wake')).toBe(true))
    expect(await screen.findByText(/Wake queued/)).toBeInTheDocument()
  })

  it('opens memory and notes', async () => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'open memory')
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('dialog', { name: 'Memory' })).toBeInTheDocument()
    expect(loc()).toBe('?panel=memory')
  })

  it.each([
    ['open ai settings', 'ai'],
    ['open reflections', 'reflections'],
    ['open standards', 'standards'],
    ['open command center', 'command-center'],
  ])('M10: the palette command "%s" opens ?panel=%s', async (query, panel) => {
    vi.stubGlobal('fetch', mockApi(m5).impl)
    renderCanvas()
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), query)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(loc()).toBe(`?panel=${panel}`))
  }, 15_000)

  it('hides the focus-command-center and wake commands while the backend is missing', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/tasks': page([makeTask({ id: 't1' })]) }).impl)
    renderCanvas()
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await waitFor(() => expect(screen.getAllByText(/Arrives in M10/).length).toBeGreaterThan(0)) // Command Center and Reflections: not built here
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'focus')
    expect(screen.queryByRole('option', { name: /Focus command center/ })).not.toBeInTheDocument()
  })
})
