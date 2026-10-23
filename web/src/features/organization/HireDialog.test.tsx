import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { jsonResponse, makeAgent, makeCompany, makeSkill, makeTemplate, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'

const founder = makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', role_title: 'Founder & CEO' })
const templates = [
  makeTemplate({ slug: 'founder-ceo', family: 'founder', default_kind: 'founder', title: 'Founder & CEO' }),
  makeTemplate({ default_skill_slugs: ['plan', 'debugging', 'ghost'] }),
  makeTemplate({ slug: 'product-designer', family: 'design', title: 'Product Designer', description: 'Designs the product.', default_department: 'Design', default_skill_slugs: ['design-md'], instructions: 'Design well.' }),
]
const skills = ['plan', 'debugging', 'design-md', 'extra'].map((slug) => makeSkill({ slug, name: slug }))

const base = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder]),
  '/companies/c1/departments': page([{ id: 'd1', company_id: 'c1', name: 'Engineering', description: null, lead_agent_id: null, agent_count: 0, created_at: 'x', updated_at: 'x' }]),
  '/companies/c1/activity': page([]),
  '/runtimes': { items: [] },
  '/companies/c1/role-templates': page(templates),
  '/companies/c1/skills': page(skills),
}

async function openHire(routes: Record<string, unknown> = {}) {
  const api = mockApi({ ...base, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(
    <Routes>
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route: '/c/c1?panel=org' },
  )
  const org = await screen.findByRole('dialog', { name: 'Organization' })
  await userEvent.click(await within(org).findByRole('button', { name: 'Hire' }))
  return { api, dialog: await screen.findByRole('dialog', { name: /Propose hire/ }) }
}

afterEach(() => vi.unstubAllGlobals())

describe('Hire dialog: role templates first', () => {
  it('offers template cards (no founder) plus a custom role, with default skills shown', async () => {
    const { dialog } = await openHire()
    const list = await within(dialog).findByRole('list', { name: 'Role templates' })
    const cards = within(list).getAllByRole('button')
    expect(cards.map((c) => c.textContent)).toEqual([expect.stringContaining('Product Designer'), expect.stringContaining('Software Engineer'), expect.stringContaining('Custom role')])
    expect(within(list).queryByText('Founder & CEO')).not.toBeInTheDocument()
    expect(within(list).getByText('Builds and ships product code.')).toBeInTheDocument()
    expect(within(list).getByText('plan')).toBeInTheDocument()
  })

  it('choosing a template pre-fills the form and posts role_template without restating defaults', async () => {
    const { api, dialog } = await openHire({ 'POST /companies/c1/agents': (_u: URL, init?: RequestInit) => jsonResponse(makeAgent({ id: 'n1', name: 'Sam', ...JSON.parse(String(init?.body)) }), 201) })
    await userEvent.click(await within(dialog).findByRole('button', { name: /Software Engineer/ }))

    expect(within(dialog).getByLabelText(/Role title/)).toHaveValue('Software Engineer')
    expect(within(dialog).getByLabelText(/Instructions/)).toHaveValue('Write clean code.')
    expect(within(dialog).getByLabelText(/Department/)).toHaveValue('d1') // "Engineering" matched by name
    expect(within(dialog).getByLabelText(/Manager/)).toHaveValue('f1')
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue('')
    const skillsList = within(dialog).getByRole('list', { name: 'Selected skills' })
    expect(within(skillsList).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringContaining('plan'), expect.stringContaining('debugging')])
    expect(within(dialog).getByText(/Not in your library, so not assigned: ghost/)).toBeInTheDocument()

    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Sam')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Propose hire' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/agents')).toBe(true))
    const body = api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/agents')!.body as Record<string, unknown>
    expect(body).toMatchObject({ name: 'Sam', role_title: 'Software Engineer', role_template: 'software-engineer', department_id: 'd1', manager_agent_id: 'f1' })
    expect(body).not.toHaveProperty('instructions')
    expect(body).not.toHaveProperty('kind')
    expect(body).not.toHaveProperty('skill_slugs')
  })

  it('lets the human edit skills: extras are sent, deselected defaults are removed after the hire', async () => {
    const { api, dialog } = await openHire({ 'POST /companies/c1/agents': (_u: URL, init?: RequestInit) => jsonResponse(makeAgent({ id: 'n1', name: 'Sam', ...JSON.parse(String(init?.body)) }), 201) })
    const deleted: string[] = []
    const inner = globalThis.fetch
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost').pathname
      if (init?.method === 'DELETE') {
        deleted.push(path)
        return new Response(null, { status: 204 })
      }
      return inner(input, init)
    })
    await userEvent.click(await within(dialog).findByRole('button', { name: /Software Engineer/ }))
    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Sam')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove debugging' }))
    await userEvent.selectOptions(within(dialog).getByLabelText('Add a skill'), 'extra')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Propose hire' }))

    await waitFor(() => expect(deleted).toEqual(['/api/companies/c1/agents/n1/skills/debugging']))
    expect((api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/agents')!.body as Record<string, unknown>).skill_slugs).toEqual(['extra'])
  })

  it('tells the human a template department does not exist yet', async () => {
    const { dialog } = await openHire()
    await userEvent.click(await within(dialog).findByRole('button', { name: /Product Designer/ }))
    expect(within(dialog).getByText(/The Design department does not exist yet and is created when you hire/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Department/)).toHaveValue('')
  })

  it('can go back and hire a custom role without a template', async () => {
    const { api, dialog } = await openHire({ 'POST /companies/c1/agents': (_u: URL, init?: RequestInit) => jsonResponse(makeAgent({ id: 'n2', ...JSON.parse(String(init?.body)) }), 201) })
    await userEvent.click(await within(dialog).findByRole('button', { name: /Custom role/ }))
    expect(within(dialog).getByLabelText(/Role title/)).toHaveValue('')
    expect(within(dialog).queryByRole('group', { name: 'Skills' })).not.toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Zed')
    await userEvent.type(within(dialog).getByLabelText(/Role title/), 'Fixer')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Propose hire' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/agents')).toBe(true))
    expect(api.calls.find((c) => c.method === 'POST' && c.path === '/companies/c1/agents')!.body).not.toHaveProperty('role_template')
  })

  it('degrades to a custom-role form when role templates are not built', async () => {
    // Remove the templates and skills routes: a pre-M4 backend.
    const { '/companies/c1/role-templates': _t, '/companies/c1/skills': _s, ...old } = base
    void _t
    void _s
    vi.stubGlobal('fetch', mockApi(old).impl)
    renderWithProviders(
      <Routes>
        <Route path="/c/:companyId" element={<CompanyLayout />}>
          <Route index element={<CanvasPage />} />
        </Route>
      </Routes>,
      { route: '/c/c1?panel=org' },
    )
    const org = await screen.findByRole('dialog', { name: 'Organization' })
    await userEvent.click(await within(org).findByRole('button', { name: 'Hire' }))
    const dialog = await screen.findByRole('dialog', { name: /Propose hire/ })
    expect(await within(dialog).findByText(/Role templates are not built yet/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /Custom role/ })).toBeInTheDocument()
  })
})
