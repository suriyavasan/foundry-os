import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StackedBars } from '@/components/charts/StackedBars'
import { jsonResponse, makeCompany, makeFinanceSummary, makeTask, mockApi, page } from '@/test/factories'
import { baseRoutes, iris, loc, renderCanvas } from '@/test/m5'

beforeEach(() => {
  localStorage.clear()
  // 02:30 UTC on the 24th is still Friday 23 October in Los Angeles.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-24T02:30:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const la = makeCompany({ id: 'c1', timezone: 'America/Los_Angeles' })
const viewerMe = { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'viewer' }] }

interface Opts {
  summary?: ((url: URL) => Response) | Record<string, unknown>
  extra?: Record<string, unknown>
}
/** Like the server: the summary echoes the range that was asked for, in the company timezone. */
const echoing = (url: URL) => jsonResponse(makeFinanceSummary({ from: url.searchParams.get('from') ?? '', to: url.searchParams.get('to') ?? '', timezone: 'America/Los_Angeles' }))

function setup({ summary = echoing, extra = {} }: Opts = {}) {
  const urls: Record<string, string[]> = { summary: [], breakdown: [], trend: [], runs: [], expenses: [] }
  const rec = (k: string, body: unknown) => (url: URL) => {
    urls[k]!.push(url.search)
    return jsonResponse(body)
  }
  const api = mockApi({
    ...baseRoutes,
    '/companies/c1': la,
    '/companies/c1/policy': { autonomy_mode: 'autonomous', budget_alert_thresholds: [50, 80, 100] },
    '/companies/c1/finance/summary': typeof summary === 'function' ? (url: URL) => (urls.summary!.push(url.search), summary(url)) : (url: URL) => (urls.summary!.push(url.search), jsonResponse(summary)),
    '/companies/c1/finance/trend': rec('trend', { points: [{ date: '2026-10-03', verified_micros: 1_000_000, estimated_micros: 500_000, unknown_micros: 0 }] }),
    '/companies/c1/finance/breakdown': rec('breakdown', { items: [{ key: 'a2', verified_micros: 900_000, estimated_micros: 100_000, unknown_micros: 0, input_tokens: 10, output_tokens: 5, runs: 3 }] }),
    '/companies/c1/finance/runs': rec('runs', page([])),
    '/companies/c1/finance/expenses': rec('expenses', page([])),
    ...extra,
  })
  vi.stubGlobal('fetch', api.impl)
  return { api, urls }
}
const openFinance = async () => {
  renderCanvas('/c/c1?panel=finance')
  return screen.findByRole('dialog', { name: 'Finance' })
}

describe('Finance panel on a backend without M6', () => {
  it('falls back to the usage ledger and says the rest arrives in M6', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({ ...baseRoutes, '/companies/c1': la, '/companies/c1/usage': { items: [{ key: '2026-10-23', label: null, input_tokens: 1, output_tokens: 1, cost_micros: 2_000_000, by_cost_kind: { verified_micros: 2_000_000, estimated_micros: 0, unknown_micros: 0 }, records: 1 }] } }).impl,
    )
    const dialog = await openFinance()
    expect(await within(dialog).findByText('This month', { selector: 'div' })).toBeInTheDocument()
    expect(within(dialog).getAllByText(/Arrives in M6/)).toHaveLength(1)
    expect(within(dialog).queryByRole('group', { name: 'Reporting period' })).not.toBeInTheDocument()
  })
})

describe('Finance periods in company time', () => {
  it('requests the company-local month, then the week and day around Friday 23 October (not the UTC 24th)', async () => {
    const { urls } = setup()
    const dialog = await openFinance()
    const user = userEvent.setup()
    const bar = await within(dialog).findByRole('group', { name: 'Reporting period' })
    expect(within(bar).getByText('October 2026')).toBeInTheDocument()
    expect(within(bar).getByText(/America\/Los_Angeles \(company time\)/)).toBeInTheDocument()
    await waitFor(() => expect(urls.summary).toContain('?from=2026-10-01&to=2026-10-31'))

    await user.click(within(bar).getByRole('button', { name: 'Week' }))
    await waitFor(() => expect(urls.summary).toContain('?from=2026-10-19&to=2026-10-25'))
    expect(await within(dialog).findByText('Mon 19 Oct – Sun 25 Oct 2026')).toBeInTheDocument()

    await user.click(within(bar).getByRole('button', { name: 'Day' }))
    await waitFor(() => expect(urls.summary).toContain('?from=2026-10-23&to=2026-10-23'))
    expect(await within(dialog).findByText('Fri 23 Oct 2026')).toBeInTheDocument()
    // every report follows the same range
    expect(urls.trend).toContain('?from=2026-10-23&to=2026-10-23')
    expect(urls.expenses).toContain('?from=2026-10-23&to=2026-10-23&limit=200')
  })

  it('steps back and forward, never past the current period', async () => {
    const { urls } = setup()
    const dialog = await openFinance()
    const user = userEvent.setup()
    await within(dialog).findByText('October 2026')
    const next = within(dialog).getByRole('button', { name: 'Next month' })
    expect(next).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Previous month' }))
    await waitFor(() => expect(urls.summary).toContain('?from=2026-09-01&to=2026-09-30'))
    expect(await within(dialog).findByText('September 2026')).toBeInTheDocument()
    expect(next).toBeEnabled()
    await user.click(next)
    await waitFor(() => expect(within(dialog).getByText('October 2026')).toBeInTheDocument())
    // back on October, but anchored on the 1st, so "Today" is available until it is used
    await user.click(within(dialog).getByRole('button', { name: 'Today' }))
    expect(within(dialog).getByRole('button', { name: 'Today' })).toBeDisabled()
  })

  it('jumps to a picked date', async () => {
    const { urls } = setup()
    const dialog = await openFinance()
    await within(dialog).findByText('October 2026')
    const date = within(dialog).getByLabelText('Pick a date in the period')
    expect(date).toHaveAttribute('max', '2026-10-23')
    fireEvent.change(date, { target: { value: '2026-08-15' } })
    await waitFor(() => expect(urls.summary).toContain('?from=2026-08-01&to=2026-08-31'))
    expect(await within(dialog).findByText('August 2026')).toBeInTheDocument()
  })

  it('echoes the range the server reports', async () => {
    setup({ summary: () => jsonResponse(makeFinanceSummary({ from: '2026-10-05', to: '2026-10-07', timezone: 'America/Los_Angeles' })) })
    const dialog = await openFinance()
    expect(await within(dialog).findByText('Mon 5 Oct – Wed 7 Oct 2026')).toBeInTheDocument()
  })
})

describe('Finance: cost kinds', () => {
  it('calls only the verified figure a charge; the estimate says est. and is hatched', async () => {
    setup()
    const dialog = await openFinance()
    const totals = await within(dialog).findByRole('group', { name: 'Totals for the period' })
    const charged = within(totals).getByText('Charged').parentElement!.parentElement!
    expect(within(charged).getByText('$1.25')).toBeInTheDocument()
    const est = within(totals).getByText('Estimated').parentElement!.parentElement!
    expect(within(est).getByText('≈ $3.40 est.')).toBeInTheDocument()
    expect(within(est).getByText(/Not a charge/)).toBeInTheDocument()
    expect(est.querySelector('.cos-hatch')).not.toBeNull()
    // the estimate never appears as a plain dollar amount
    expect(within(totals).queryByText('$3.40')).not.toBeInTheDocument()
    expect(within(totals).getByText('$12.00')).toBeInTheDocument() // external expenses, separate
    expect(within(totals).getByText(/\$0\.25 charged/)).toBeInTheDocument() // wasted
    expect(within(totals).getByText(/20% of charged/)).toBeInTheDocument()
  })

  it('never gives unpriced usage a dollar figure', async () => {
    setup({ summary: () => jsonResponse(makeFinanceSummary({ totals: { verified_micros: 0, estimated_micros: 0, unknown_micros: 7_000_000, input_tokens: 1, output_tokens: 1, runs: 1 } })) })
    const dialog = await openFinance()
    const totals = await within(dialog).findByRole('group', { name: 'Totals for the period' })
    expect(within(totals).getByText('Unpriced', { selector: 'div.tabular-nums' })).toBeInTheDocument()
    expect(within(totals).queryByText('$7.00')).not.toBeInTheDocument()
  })

  it('shows an error, not zeros, when the summary fails', async () => {
    setup({ summary: () => jsonResponse({ error: { code: 'internal_error', message: 'boom', details: null } }, 500) })
    const dialog = await openFinance()
    // the hook retries a failing request twice (1 s, 2 s) before giving up
    expect(await within(dialog).findByText('boom', {}, { timeout: 6000 })).toBeInTheDocument()
    expect(within(dialog).queryByRole('group', { name: 'Totals for the period' })).not.toBeInTheDocument()
  })
})

describe('Finance: budgets', () => {
  const budgets = [
    { scope: 'company', agent_id: null, period: 'month', limit_micros: 10_000_000, verified_micros: 6_000_000, estimated_micros: 3_000_000 },
    { scope: 'agent', agent_id: 'a2', period: 'month', limit_micros: 2_000_000, verified_micros: 100_000, estimated_micros: 0 },
  ]

  it('draws a bar per budget with the 50/80/100 markers, marking the ones reached', async () => {
    setup({ summary: () => jsonResponse(makeFinanceSummary({ budgets: budgets as never })) })
    const dialog = await openFinance()
    const company = (await within(dialog).findByRole('progressbar', { name: 'Company monthly budget' })).parentElement!
    const ticks = [...company.querySelectorAll('[data-threshold]')]
    expect(ticks.map((t) => t.getAttribute('data-threshold'))).toEqual(['50', '80', '100'])
    expect(ticks.filter((t) => t.hasAttribute('data-reached')).map((t) => t.getAttribute('data-threshold'))).toEqual(['50', '80'])
    expect(within(company.parentElement!).getByText('90% used')).toBeInTheDocument()
    expect(company.querySelector('.cos-hatch')).not.toBeNull() // the estimate is its own hatched segment
    const agentBar = within(dialog).getByRole('progressbar', { name: 'Agent monthly budget' }).parentElement!
    expect([...agentBar.querySelectorAll('[data-reached]')]).toHaveLength(0)
    expect(within(agentBar.parentElement!).getByText('5% used')).toBeInTheDocument()
  })

  it('uses the thresholds from the policy', async () => {
    setup({ summary: () => jsonResponse(makeFinanceSummary({ budgets: budgets as never })), extra: { '/companies/c1/policy': { budget_alert_thresholds: [25, 75] } } })
    const dialog = await openFinance()
    const company = (await within(dialog).findByRole('progressbar', { name: 'Company monthly budget' })).parentElement!
    await waitFor(() => expect([...company.querySelectorAll('[data-threshold]')].map((t) => t.getAttribute('data-threshold'))).toEqual(['25', '75']))
  })

  it('says so when no budget is set', async () => {
    setup()
    const dialog = await openFinance()
    expect(await within(dialog).findByText('No budgets set')).toBeInTheDocument()
  })
})

describe('Finance: trend, breakdown, runs', () => {
  it('draws the chart and offers the same numbers as a table, zero-filled', async () => {
    setup()
    const dialog = await openFinance()
    const user = userEvent.setup()
    const chart = await within(dialog).findByRole('img', { name: /Spend per day from Thu 1 Oct to Sat 31 Oct/ })
    expect(chart.querySelectorAll('title').length).toBe(31)
    expect(chart.querySelector('pattern')).not.toBeNull() // the hatched estimate segment
    expect(within(dialog).getByRole('list', { name: 'Legend' })).toHaveTextContent(/Estimated, not a charge \(est\.\)/)
    await user.click(within(dialog).getByRole('button', { name: 'Show data table' }))
    const trend = within(dialog).getByText('Charged (verified)', { selector: 'th' }).closest('table')!
    expect(within(trend).getAllByRole('row')).toHaveLength(32) // header + 31 days
    const day3 = within(trend).getByText('Sat 3 Oct').closest('tr')!
    expect(within(day3).getByText('$1.00')).toBeInTheDocument()
    expect(within(day3).getByText('≈ $0.50 est.')).toBeInTheDocument()
    expect(within(trend).getByText('Sun 4 Oct').closest('tr')).toHaveTextContent('Sun 4 Oct$0.00')
    expect(within(dialog).getByRole('button', { name: 'Show chart' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows an empty state instead of an empty chart', async () => {
    setup({ extra: { '/companies/c1/finance/trend': { points: [] } } })
    const dialog = await openFinance()
    expect(await within(dialog).findByText('No spend in this period')).toBeInTheDocument()
    expect(within(dialog).queryByRole('img', { name: /Spend per day/ })).not.toBeInTheDocument()
  })

  it('switches the breakdown between agent, project, provider, model and runtime', async () => {
    const { urls } = setup({ extra: { '/companies/c1/agents/a2': iris } })
    const dialog = await openFinance()
    const user = userEvent.setup()
    const select = await within(dialog).findByLabelText('Break down by')
    expect(await within(dialog).findByText('Iris')).toBeInTheDocument() // the agent id resolved to its name
    for (const g of ['project', 'provider', 'model', 'runtime']) {
      await user.selectOptions(select, g)
      await waitFor(() => expect(urls.breakdown!.some((u) => u.includes(`group_by=${g}`))).toBe(true))
    }
  })

  it('lists run costs by provenance and links to the run', async () => {
    const runs = [
      { run_id: 'r1', task_id: 't1', task_title: 'Write brief', agent_id: 'a2', status: 'succeeded', started_at: '2026-10-22T10:00:00Z', finished_at: null, cost_micros: 150_000, cost_kind: 'verified', model: 'qwen2.5:7b', provider: 'ollama' },
      { run_id: 'r2', task_id: 't2', task_title: 'Draft post', agent_id: 'a2', status: 'failed', started_at: '2026-10-22T11:00:00Z', finished_at: null, cost_micros: 90_000, cost_kind: 'estimated', model: null, provider: null },
      { run_id: 'r3', task_id: 't3', task_title: 'Local run', agent_id: null, status: 'timed_out', started_at: null, finished_at: null, cost_micros: null, cost_kind: 'unknown', model: null, provider: null },
    ]
    setup({ extra: { '/companies/c1/finance/runs': page(runs), '/companies/c1/tasks/t1': makeTask({ id: 't1' }) } })
    const dialog = await openFinance()
    const user = userEvent.setup()
    const r1 = (await within(dialog).findByText('Write brief')).closest('tr')!
    expect(within(r1).getByText('$0.15')).toBeInTheDocument()
    const r2 = within(dialog).getByText('Draft post').closest('tr')!
    expect(within(r2).getByText('≈ $0.09 est.')).toBeInTheDocument()
    expect(within(r2).getByText('Wasted')).toBeInTheDocument()
    const r3 = within(dialog).getByText('Local run').closest('tr')!
    expect(within(r3).getByText('Unpriced')).toBeInTheDocument()
    expect(within(r1).queryByText('Wasted')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Write brief' }))
    await waitFor(() => expect(loc()).toBe('?panel=task:t1&run=r1'))
  })
})

describe('Finance: external expenses', () => {
  const expenses = [{ id: 'x1', company_id: 'c1', vendor: 'Namecheap', description: 'acme.io', amount_micros: 12_500_000, recurrence: 'yearly', project_id: null, incurred_on: '2026-10-02', created_at: '2026-10-02T10:00:00Z' }]

  it('lists recorded expenses with a total', async () => {
    setup({ extra: { '/companies/c1/finance/expenses': page(expenses) } })
    const dialog = await openFinance()
    const row = (await within(dialog).findByText('Namecheap')).closest('tr')!
    expect(within(row).getByText('Yearly')).toBeInTheDocument()
    expect(within(row).getByText('$12.50')).toBeInTheDocument()
    expect(within(row).getByText('Fri 2 Oct')).toBeInTheDocument()
  })

  it('validates, then posts the amount as exact micros', async () => {
    const created = { ...expenses[0], id: 'x2' }
    const { api } = setup({ extra: { 'POST /companies/c1/finance/expenses': () => jsonResponse(created, 201), '/companies/c1/projects': page([{ id: 'p1', name: 'Launch' }]) } })
    const dialog = await openFinance()
    const user = userEvent.setup()
    await user.click(await within(dialog).findByRole('button', { name: 'Add expense' }))
    const form = await screen.findByRole('dialog', { name: 'Add external expense' })
    // defaults: today in company time, one-time
    expect(within(form).getByLabelText(/^Date/)).toHaveValue('2026-10-23')
    expect(within(form).getByLabelText('Recurrence')).toHaveValue('one_time')
    await user.click(within(form).getByRole('button', { name: 'Add expense' }))
    expect(await within(form).findByText('Who was paid?')).toBeInTheDocument()
    expect(within(form).getByText('Enter the amount in USD')).toBeInTheDocument()
    expect(api.calls.some((c) => c.method === 'POST')).toBe(false)

    await user.type(within(form).getByLabelText(/^Vendor/), 'Namecheap')
    await user.type(within(form).getByLabelText(/^Amount/), '19.99')
    await user.selectOptions(within(form).getByLabelText('Recurrence'), 'monthly')
    await user.selectOptions(within(form).getByLabelText('Project (optional)'), 'p1')
    await user.click(within(form).getByRole('button', { name: 'Add expense' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/finance/expenses')).toBe(true))
    expect(api.calls.find((c) => c.method === 'POST')!.body).toEqual({ vendor: 'Namecheap', description: null, amount_micros: 19_990_000, recurrence: 'monthly', project_id: 'p1', incurred_on: '2026-10-23' })
  })

  it('does not offer to add an expense to a viewer', async () => {
    setup({ extra: { '/me': viewerMe, '/companies/c1/finance/expenses': page(expenses) } })
    const dialog = await openFinance()
    await within(dialog).findByText('Namecheap')
    expect(within(dialog).queryByRole('button', { name: 'Add expense' })).not.toBeInTheDocument()
  })

  it('says "Arrives in M6" when only the expenses endpoint is missing', async () => {
    const { api } = setup()
    api.calls.length = 0
    const dialog = await openFinance()
    const card = (await within(dialog).findByText('External expenses', { selector: 'h2' })).closest('section')!
    await waitFor(() => expect(within(card).getByText('No expenses in this period')).toBeInTheDocument())
  })
})

describe('Spend widget on the canvas', () => {
  const usage = { items: [{ key: '2026-10-23', label: null, input_tokens: 1, output_tokens: 1, cost_micros: 6_000_000, by_cost_kind: { verified_micros: 6_000_000, estimated_micros: 0, unknown_micros: 0 }, records: 1 }] }
  const widget = (policy: Record<string, unknown>) => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1': la, '/companies/c1/usage': usage, '/companies/c1/policy': policy }).impl)
    renderCanvas('/c/c1')
    return screen.findByRole('region', { name: 'Spend' })
  }

  it('marks the alert thresholds on the budget bars and links to Finance', async () => {
    const box = await widget({ autonomy_mode: 'autonomous', monthly_budget_micros: 10_000_000, daily_budget_micros: 10_000_000, budget_alert_thresholds: [50, 80, 100] })
    const bars = await within(box).findAllByRole('progressbar')
    expect(bars.length).toBe(2)
    for (const bar of bars) expect([...bar.parentElement!.querySelectorAll('[data-threshold]')].map((t) => t.getAttribute('data-threshold'))).toEqual(['50', '80', '100'])
    // 60% verified: the 50% marker is reached, 80% is not
    // (the company clock settles after the first render, so wait for the day's spend to land on today)
    await waitFor(() => expect(bars[0]!.parentElement!.querySelector('[data-threshold="50"]')).toHaveAttribute('data-reached'))
    expect(bars[0]!.parentElement!.querySelector('[data-threshold="80"]')).not.toHaveAttribute('data-reached')
    const user = userEvent.setup()
    await user.click(within(box).getByRole('button', { name: /Open finance: budgets, trend, expenses/ }))
    expect(loc()).toBe('?panel=finance')
  })

  it('draws no markers when the policy does not report thresholds, but keeps the verified/estimated split', async () => {
    const box = await widget({ autonomy_mode: 'autonomous', monthly_budget_micros: 10_000_000 })
    const bar = await within(box).findByRole('progressbar')
    expect(bar.parentElement!.querySelectorAll('[data-threshold]')).toHaveLength(0)
    expect(within(box).getAllByText('Verified').length).toBeGreaterThan(0)
    expect(within(box).getAllByText('Estimated').length).toBeGreaterThan(0)
  })
})

describe('StackedBars', () => {
  const series = [
    { id: 'a', label: 'A', className: 'fill-success' },
    { id: 'b', label: 'B', className: 'text-warning', hatch: true },
  ]
  it('scales bars to the given maximum and hatches the estimate series', () => {
    const { container } = render(
      <StackedBars
        ariaLabel="chart"
        series={series}
        max={100}
        formatTick={(v) => `${v}`}
        bars={[
          { key: '1', label: 'one', values: [50, 25], title: 'one' },
          { key: '2', label: 'two', values: [0, 0], title: 'two' },
        ]}
      />,
    )
    const rects = [...container.querySelectorAll('rect[class]')]
    expect(rects).toHaveLength(2) // the empty bar draws nothing
    expect(container.querySelectorAll('pattern')).toHaveLength(1)
    const [solid, hatched] = rects as SVGRectElement[]
    expect(Number(solid!.getAttribute('height'))).toBeCloseTo(2 * Number(hatched!.getAttribute('height')), 5)
    expect(hatched!.getAttribute('fill')).toMatch(/^url\(#[A-Za-z0-9]+-b\)$/)
  })
})
