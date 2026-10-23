import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeAgent, makeGuardrailEvent, makeRateLimit, makeRule, mockApi, page } from '@/test/factories'
import { baseRoutes, iris, loc, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const idle = { active: false, activated_at: null, activated_by: null, reason: null }
const viewerMe = { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'viewer' }] }

const ruleDeny = makeRule({ id: 'r1', tier: 'external_communication', decision: 'deny', conditions: { max_per_day: 5, max_amount_micros: 50_000_000 } })
const ruleAction = makeRule({ id: 'r2', tier: 'external_communication', action: 'email.send', scope_type: 'agent', scope_value: 'a2', decision: 'allow', updated_at: '2026-10-24T10:00:00Z' })

const m6 = (extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/system/status': idle,
  '/companies/c1/guardrails/rules': page([ruleDeny, ruleAction]),
  '/companies/c1/guardrails/events': page([]),
  '/companies/c1/guardrails/rate-limits': { items: [] },
  ...extra,
})

const openPanel = async (routes: Record<string, unknown>) => {
  const api = mockApi(routes)
  vi.stubGlobal('fetch', api.impl)
  renderCanvas('/c/c1?panel=guardrails')
  const dialog = await screen.findByRole('dialog', { name: 'Guardrails' })
  return { api, dialog }
}

describe('Guardrails panel on a backend without M6', () => {
  it('says "Arrives in M6" instead of inventing anything', async () => {
    const { dialog } = await openPanel(baseRoutes)
    await waitFor(() => expect(within(dialog).getAllByText(/Arrives in M6/).length).toBeGreaterThanOrEqual(3))
    expect(within(dialog).queryByRole('button', { name: /Add rule/ })).not.toBeInTheDocument()
    // the emergency stop cannot be started when its endpoint is not there
    expect(within(dialog).queryByRole('button', { name: /Emergency stop…/ })).not.toBeInTheDocument()
  })
})

describe('Rules', () => {
  it('lists rules with decision chips, scope and conditions', async () => {
    const { dialog } = await openPanel(m6({ '/companies/c1/agents/a2': iris }))
    const rules = await within(dialog).findByRole('table', {}, { timeout: 3000 }).catch(() => null)
    expect(rules).toBeTruthy()
    const row = (await within(dialog).findByText('email.send')).closest('tr')!
    expect(within(row).getByText('Allow')).toBeInTheDocument()
    expect(within(row).getByText('Iris')).toBeInTheDocument()
    const tierRow = (await within(dialog).findByText('Any external communication action')).closest('tr')!
    expect(within(tierRow).getByText('Deny')).toBeInTheDocument()
    expect(within(tierRow).getByText('All agents')).toBeInTheDocument()
    expect(within(tierRow).getByText(/up to \$50\.00 · 5\/day/)).toBeInTheDocument()
  })

  it('shows the built-in defaults with an overridden marker only on tiers that have rules', async () => {
    const { dialog } = await openPanel(m6())
    const defaults = (await within(dialog).findByText('Built-in defaults')).closest('section')!
    const comm = within(defaults).getByText('External communication').closest('tr')!
    expect(within(comm).getByText('Peer review')).toBeInTheDocument()
    expect(within(comm).getByText('Overridden by 2 rules')).toBeInTheDocument()
    const destructive = within(defaults).getByText('Destructive').closest('tr')!
    expect(within(destructive).getByText('Default applies')).toBeInTheDocument()
    expect(within(destructive).getByText('Human approval')).toBeInTheDocument()
  })

  it('previews the decision for a tier, action and agent', async () => {
    const { dialog } = await openPanel(m6({ '/companies/c1/agents/a2': iris }))
    const user = userEvent.setup()
    const tryIt = (await within(dialog).findByText('Try it')).closest('section')!
    await user.selectOptions(within(tryIt).getByLabelText('Tier'), 'external_communication')
    expect(within(tryIt).getByRole('status')).toHaveTextContent(/Deny.*Rule for this tier/)
    await user.type(within(tryIt).getByLabelText('Action (optional)'), 'email.send')
    await user.selectOptions(within(tryIt).getByLabelText('Agent (optional)'), 'a2')
    expect(within(tryIt).getByRole('status')).toHaveTextContent(/Allow.*Rule for this action/)
  })

  it('is read-only for a viewer', async () => {
    const { dialog } = await openPanel(m6({ '/me': viewerMe }))
    await within(dialog).findByText('Any external communication action')
    expect(within(dialog).getByText(/only owners and admins can change rules/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Add rule/ })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Edit rule/ })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Delete rule/ })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('columnheader', { name: 'Actions' })).not.toBeInTheDocument()
  })

  it('lets an owner add a rule, sending exact micros', async () => {
    const created = makeRule({ id: 'r9' })
    const { api, dialog } = await openPanel(m6({ 'POST /companies/c1/guardrails/rules': () => jsonResponse(created, 201) }))
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: 'Add rule' }))
    const form = await screen.findByRole('dialog', { name: 'Add guardrail rule' })
    // nothing chosen yet: validation blocks the request
    await user.click(within(form).getByRole('button', { name: 'Add rule' }))
    expect(await within(form).findByText('Pick a tier')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await user.selectOptions(within(form).getByLabelText(/^Tier/), 'spend')
    await user.selectOptions(within(form).getByLabelText('What happens'), 'allow')
    expect(within(form).getByText(/More permissive than the built-in default for Spend/)).toBeInTheDocument()
    await user.type(within(form).getByLabelText('Amount up to (USD)'), '19.99')
    await user.type(within(form).getByLabelText('At most per day'), '3')
    await user.click(within(form).getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/guardrails/rules')).toBe(true))
    const post = api.calls.find((c) => c.method === 'POST')!
    expect(post.body).toEqual({ tier: 'spend', action: null, scope_type: 'all', scope_value: null, decision: 'allow', conditions: { max_amount_micros: 19_990_000, max_per_day: 3 } })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add guardrail rule' })).not.toBeInTheDocument())
  })

  it('edits and deletes a rule', async () => {
    const { api, dialog } = await openPanel(
      m6({ 'PATCH /companies/c1/guardrails/rules/r1': () => jsonResponse({ ...ruleDeny, decision: 'human_approval' }), 'DELETE /companies/c1/guardrails/rules/r1': () => new Response(null, { status: 204 }) }),
    )
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: /Edit rule for Any external communication action/ }))
    const form = await screen.findByRole('dialog', { name: 'Edit guardrail rule' })
    expect(within(form).getByLabelText('What happens')).toHaveValue('deny')
    await user.selectOptions(within(form).getByLabelText('What happens'), 'human_approval')
    await user.click(within(form).getByRole('button', { name: 'Save rule' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ decision: 'human_approval', tier: 'external_communication', scope_type: 'all' }))

    await user.click(await within(dialog).findByRole('button', { name: /Delete rule for Any external communication action/ }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete this rule?' })
    await user.click(within(confirm).getByRole('button', { name: 'Delete rule' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/companies/c1/guardrails/rules/r1')).toBe(true))
  })
})

describe('Guardrail events', () => {
  const events = [
    makeGuardrailEvent({ id: 'e1', decision: 'deny', action: 'domain.register', tier: 'external_commitment', rule_id: 'r1', agent_id: 'a2' }),
    makeGuardrailEvent({ id: 'e2', decision: 'peer_review', action: 'email.send', run_id: 'run1', task_id: 't1', approval_id: 'ap1' }),
  ]

  it('shows time, agent, action, tier, decision, and links to the rule and the run', async () => {
    const { dialog } = await openPanel(m6({ '/companies/c1/guardrails/events': page(events), '/companies/c1/agents/a2': iris }))
    const row1 = (await within(dialog).findByText('domain.register')).closest('tr')!
    expect(within(row1).getByText('Deny')).toBeInTheDocument()
    expect(within(row1).getByText('External commitment')).toBeInTheDocument()
    expect(within(row1).getByRole('button', { name: 'View rule' })).toBeInTheDocument()
    const row2 = within(dialog).getByText('email.send', { selector: 'td' }).closest('tr')!
    expect(within(row2).getByText('Default')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(within(row2).getByRole('button', { name: 'Run' }))
    expect(loc()).toBe('?panel=task:t1&run=run1')
  })

  it('filters on the server by decision, tier and agent', async () => {
    const urls: string[] = []
    const { dialog } = await openPanel(
      m6({
        '/companies/c1/guardrails/events': (url: URL) => {
          urls.push(url.search)
          return jsonResponse(page(events))
        },
      }),
    )
    const user = userEvent.setup()
    await within(dialog).findByText('domain.register')
    await user.selectOptions(within(dialog).getByLabelText('Filter by decision'), 'deny')
    await waitFor(() => expect(urls.some((u) => u.includes('decision=deny'))).toBe(true))
    await user.selectOptions(within(dialog).getByLabelText('Filter by tier'), 'spend')
    await waitFor(() => expect(urls.some((u) => u.includes('decision=deny') && u.includes('tier=spend'))).toBe(true))
    await user.selectOptions(within(dialog).getByLabelText('Filter by agent'), 'a2')
    await waitFor(() => expect(urls.some((u) => u.includes('agent_id=a2'))).toBe(true))
  })

  it('says so when nothing matches', async () => {
    const { dialog } = await openPanel(m6())
    expect(await within(dialog).findByText('No guardrail events yet')).toBeInTheDocument()
  })
})

describe('Rate limits', () => {
  it('shows each agent’s usage against its limits', async () => {
    const { dialog } = await openPanel(
      m6({
        '/companies/c1/guardrails/rate-limits': { items: [makeRateLimit({ agent_id: 'a2', key: 'api_calls_per_min', used: 60, limit: 60 }), makeRateLimit({ agent_id: 'a2', key: 'hires_per_day', used: 1, limit: 4 })] },
        '/companies/c1/agents/a2': iris,
      }),
    )
    const bar = await within(dialog).findByRole('progressbar', { name: /API calls per minute: 60 of 60, 100%/ })
    expect(bar).toBeInTheDocument()
    expect(within(dialog).getByText('at limit')).toBeInTheDocument()
    expect(within(dialog).getByRole('progressbar', { name: /Hires per day: 1 of 4, 25%/ })).toBeInTheDocument()
  })
})

describe('Emergency stop', () => {
  it('stays disabled until the exact word STOP is typed, then stops everything', async () => {
    let status: Record<string, unknown> = idle
    const { api, dialog } = await openPanel(
      m6({
        '/system/status': () => jsonResponse(status),
        'POST /system/emergency-stop': () => {
          status = { active: true, activated_at: '2026-10-24T09:00:00Z', activated_by: 'Sam', reason: null, runs_cancelled: 2 }
          return jsonResponse(status)
        },
      }),
    )
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: /Emergency stop…/ }))
    const confirm = await screen.findByRole('dialog', { name: 'Emergency stop' })
    expect(within(confirm).getByText(/cancels every run in every company/)).toBeInTheDocument()
    const stop = within(confirm).getByRole('button', { name: /Stop everything/ })
    expect(stop).toBeDisabled()
    const input = within(confirm).getByLabelText(/Type STOP to confirm/)
    await user.type(input, 'stop')
    expect(stop).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'STO')
    expect(stop).toBeDisabled()
    await user.type(input, 'P')
    expect(stop).toBeEnabled()
    await user.click(stop)
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/system/emergency-stop')).toBe(true))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Emergency stop' })).not.toBeInTheDocument())
    // the top bar now carries the red banner, and the panel offers to resume
    await waitFor(() => expect(document.querySelector('header.sticky [role="alert"]')).toHaveTextContent(/Emergency stop active\. Every company is halted/))
    expect(await within(dialog).findByRole('button', { name: /Resume all companies…/ })).toBeInTheDocument()
  })

  it('does not post anything when Enter is pressed before STOP is typed', async () => {
    const { api, dialog } = await openPanel(m6())
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: /Emergency stop…/ }))
    const confirm = await screen.findByRole('dialog', { name: 'Emergency stop' })
    await user.type(within(confirm).getByLabelText(/Type STOP to confirm/), 'nope{Enter}')
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('shows a red banner in the top bar while a stop is active, and resumes from it', async () => {
    let status: Record<string, unknown> = { active: true, activated_at: '2026-10-24T09:00:00Z', activated_by: 'Sam', reason: 'runaway spend', runs_cancelled: 4 }
    const api = mockApi({
      ...m6(),
      '/system/status': () => jsonResponse(status),
      'POST /system/resume': () => {
        status = idle
        return jsonResponse(status)
      },
    })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    const banner = await screen.findByRole('alert', {}, { timeout: 3000 })
    expect(banner).toHaveTextContent(/Emergency stop active\. Every company is halted/)
    expect(banner).toHaveTextContent(/4 runs cancelled/)
    expect(banner).toHaveTextContent(/runaway spend/)
    // it lives in the sticky top bar, above the bar's own row
    expect(banner.closest('header')).toHaveClass('sticky')
    const user = userEvent.setup()
    await user.click(within(banner).getByRole('button', { name: 'Resume…' }))
    const confirm = await screen.findByRole('dialog', { name: 'Resume all companies?' })
    await user.click(within(confirm).getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/system/resume' && c.method === 'POST')).toBe(true))
    await waitFor(() => expect(screen.queryByText(/Emergency stop active\./)).not.toBeInTheDocument())
  })

  it('shows no banner when nothing is stopped or M6 is missing', async () => {
    vi.stubGlobal('fetch', mockApi(m6()).impl)
    const { unmount } = renderCanvas('/c/c1')
    await screen.findByText('Atlas')
    expect(screen.queryByText(/Emergency stop active\./)).not.toBeInTheDocument()
    unmount()
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1')
    await screen.findByText('Atlas')
    expect(screen.queryByText(/Emergency stop active\./)).not.toBeInTheDocument()
  })

  it('is reachable from the command palette, always through the typed confirmation', async () => {
    const api = mockApi(m6())
    vi.stubGlobal('fetch', api.impl)
    renderCanvas('/c/c1')
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'emergency')
    await user.click(screen.getByRole('option', { name: /Emergency stop…/ }))
    const confirm = await screen.findByRole('dialog', { name: 'Emergency stop' })
    expect(within(confirm).getByRole('button', { name: /Stop everything/ })).toBeDisabled()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('opens guardrails and finance from the palette, and hides the stop without M6', async () => {
    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas('/c/c1')
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await waitFor(() => expect(true).toBe(true))
    await user.keyboard('{Control>}k{/Control}')
    const box = await screen.findByRole('combobox', { name: 'Search commands' })
    await user.type(box, 'emergency')
    await waitFor(() => expect(screen.queryByRole('option', { name: /Emergency stop…/ })).not.toBeInTheDocument())
    await user.clear(box)
    await user.type(box, 'open guardrails')
    await user.keyboard('{Enter}')
    expect(loc()).toBe('?panel=guardrails')
  })
})

describe('Agent panel: Permissions tab', () => {
  const agent = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Market Researcher', manager_agent_id: 'f1', tool_permissions: ['web.search'] })
  const effective = {
    agent_id: 'a2',
    tiers: [
      { tier: 'external_communication', decision: 'deny', source: 'rule', rule_id: 'r1' },
      { tier: 'read', decision: 'allow', source: 'default', rule_id: null },
    ],
    tools: [
      { name: 'web.search', tier: 'external_read', decision: 'allow', rule_id: null },
      { name: 'email.send', tier: 'external_communication', decision: 'deny', rule_id: 'r1' },
    ],
  }

  async function openTab(routes: Record<string, unknown>) {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/agents/a2': agent, ...routes }).impl)
    renderCanvas('/c/c1?panel=agent:a2')
    const dialog = await screen.findByRole('dialog', { name: /Iris/ })
    await userEvent.setup().click(await within(dialog).findByRole('tab', { name: 'Permissions' }))
    return dialog
  }

  it('shows the decision per tier, per tool, and this agent’s rate-limit usage', async () => {
    const dialog = await openTab({
      '/companies/c1/agents/a2/effective-permissions': effective,
      '/companies/c1/guardrails/rate-limits': { items: [makeRateLimit({ agent_id: 'a2', key: 'web_fetches_per_hour', used: 9, limit: 10 }), makeRateLimit({ agent_id: 'other', key: 'hires_per_day', used: 1, limit: 2 })] },
    })
    const card = (await within(dialog).findByText('Effective permissions')).closest('section')!
    const comm = (await within(card).findAllByText('External communication'))[0]!.closest('tr')!
    expect(within(comm).getByText('Deny')).toBeInTheDocument()
    expect(within(comm).getByText('Company rule')).toBeInTheDocument()
    const read = within(card).getByText('Read').closest('tr')!
    expect(within(read).getByText('Built-in default')).toBeInTheDocument()
    expect(within(card).getByText('email.send')).toBeInTheDocument()
    expect(await within(card).findByRole('progressbar', { name: /Web fetches per hour: 9 of 10/ })).toBeInTheDocument()
    expect(within(card).queryByRole('progressbar', { name: /Hires per day/ })).not.toBeInTheDocument()
  })

  it('says "Arrives in M6" without the backend, and keeps the stored tool permissions editable', async () => {
    const dialog = await openTab({})
    expect(await within(dialog).findByText(/Arrives in M6/)).toBeInTheDocument()
    expect(within(dialog).getByText('Tool permissions')).toBeInTheDocument()
  })
})
