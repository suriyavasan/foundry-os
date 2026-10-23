import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeAlert, mockApi, page } from '@/test/factories'
import { apiError, baseRoutes, loc, renderCanvas } from '@/test/m5'

afterEach(() => vi.unstubAllGlobals())

const strip = () => screen.findByRole('region', { name: 'Open alerts' })

describe('alerts strip', () => {
  it('shows nothing without alerts, and nothing when the alert system is not built yet', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/alerts': page([]) }).impl)
    const first = renderCanvas()
    await screen.findByRole('heading', { name: /canvas/, hidden: true })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Open alerts' })).not.toBeInTheDocument())
    first.unmount()

    vi.stubGlobal('fetch', mockApi(baseRoutes).impl)
    renderCanvas()
    await screen.findByText('Atlas')
    expect(screen.queryByRole('region', { name: 'Open alerts' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('lists a few alerts with severity, title, the related record and a resolve action', async () => {
    const alerts = [
      makeAlert({ id: 'a1', severity: 'critical', title: 'Run failed 3 times', body: 'Researcher is stuck', related_type: 'task', related_id: 't9' }),
      makeAlert({ id: 'a2', severity: 'info', title: 'New skill installed' }),
    ]
    let open = alerts
    const api = mockApi({
      ...baseRoutes,
      '/companies/c1/alerts': () => new Response(JSON.stringify(page(open)), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      'POST /companies/c1/alerts/a1/resolve': () => {
        open = open.filter((a) => a.id !== 'a1')
        return new Response(JSON.stringify({ ...alerts[0], status: 'resolved' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      },
    })
    vi.stubGlobal('fetch', api.impl)
    renderCanvas()
    const user = userEvent.setup()
    const region = await strip()
    const items = within(region).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]!).getByText('Critical')).toBeInTheDocument() // most severe first
    expect(within(items[0]!).getByText('Run failed 3 times')).toBeInTheDocument()
    expect(within(items[1]!).getByText('Info')).toBeInTheDocument()

    await user.click(within(items[0]!).getByRole('button', { name: /Open related task/ }))
    expect(loc()).toBe('?panel=task:t9')

    await user.click(within(items[0]!).getByRole('button', { name: 'Resolve alert: Run failed 3 times' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/companies/c1/alerts/a1/resolve')).toBe(true))
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Open alerts' })).getAllByRole('listitem')).toHaveLength(1))
  })

  it('collapses to a count when many are open and expands on request', async () => {
    const many = Array.from({ length: 5 }, (_, i) => makeAlert({ id: `a${i}`, severity: i === 0 ? 'critical' : 'warning', title: `Alert ${i}` }))
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/alerts': page(many) }).impl)
    renderCanvas()
    const user = userEvent.setup()
    const region = await strip()
    expect(within(region).getByText('5 open alerts, 1 critical')).toBeInTheDocument()
    expect(within(region).queryByText('Alert 1')).not.toBeInTheDocument()
    const toggle = within(region).getByRole('button', { name: 'Show all' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(within(region).getAllByRole('listitem')).toHaveLength(5)
    expect(within(region).getByRole('button', { name: 'Collapse' })).toHaveAttribute('aria-expanded', 'true')
    await user.click(within(region).getByRole('button', { name: 'Collapse' }))
    expect(within(region).queryByText('Alert 1')).not.toBeInTheDocument()
  })

  it('says plainly when alerts cannot be loaded', async () => {
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/alerts': () => apiError(500, 'Alerts store offline') }).impl)
    renderCanvas()
    expect(await screen.findByText(/Could not load alerts: Alerts store offline/, {}, { timeout: 6000 })).toBeInTheDocument()
  }, 10_000)
})
