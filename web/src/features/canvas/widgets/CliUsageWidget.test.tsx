import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CliUsage, CliUsageRow } from '@/api/cliUsage'
import { makeAgent, makeCompany, mockApi, page } from '@/test/factories'
import { makeCli } from '@/test/m10'
import { renderWithProviders } from '@/test/render'
import { CanvasProvider } from '../CanvasContext'
import { CliUsageWidget } from './CliUsageWidget'

const row = (over: Partial<CliUsageRow>): CliUsageRow => ({
  runtime: 'cli:claude_code',
  runs: 0,
  succeeded: 0,
  failed: 0,
  input_tokens: 0,
  output_tokens: 0,
  duration_seconds: 0,
  verified_micros: 0,
  estimated_micros: 0,
  unknown_micros: 0,
  subscription_runs: 0,
  agents: [],
  ...over,
})
const body = (items: CliUsageRow[]): CliUsage => ({ from: '2026-10-01', to: '2026-10-28', timezone: 'UTC', items })

const routes = (usage: unknown) => ({
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/companies/c1/agents': page([makeAgent({ id: 'a1', name: 'Iris' })]),
  '/cli-runtimes': [
    makeCli({ id: 'claude_code', display_name: 'Claude Code', auth: { mode: 'api_key', configured: true, credential_id: null, last_test: null } }),
    makeCli({ id: 'cursor', display_name: 'Cursor', installed: false }),
  ],
  '/companies/c1/finance/cli-usage': usage,
})

function renderWidget() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/c/:companyId"
        element={
          <CanvasProvider companyId="c1">
            <CliUsageWidget />
          </CanvasProvider>
        }
      />
    </Routes>,
    { route: '/c/c1' },
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('CliUsageWidget', () => {
  it('shows estimate before charged, subscription label, and a catalog CLI with zero usage', async () => {
    const usage = body([
      row({ runs: 3, succeeded: 2, failed: 1, input_tokens: 1200, output_tokens: 300, duration_seconds: 125, estimated_micros: 2_500_000, verified_micros: 1_000_000, subscription_runs: 1, agents: ['a1'] }),
      row({ runtime: 'cli:cursor' }),
    ])
    vi.stubGlobal('fetch', mockApi(routes(usage)).impl)
    renderWidget()
    const claude = (await screen.findByText('Claude Code')).closest('li')!
    const cost = within(claude).getByText(/est\./)
    expect(cost.textContent).toMatch(/^≈ \$2\.50 est\. · \$1\.00 charged$/)
    expect(within(claude).getByText(/subscription — not billed here/)).toBeInTheDocument()
    expect(within(claude).queryByText(/\$0 charged/)).not.toBeInTheDocument()
    expect(within(claude).getByText('✓ 2')).toBeInTheDocument()
    expect(within(claude).getByText('✗ 1')).toBeInTheDocument()
    expect(within(claude).getByText(/1,200 in \/ 300 out/)).toBeInTheDocument()
    expect(within(claude).getByText('2m 5s spent')).toBeInTheDocument()
    expect(within(claude).getByText('Connected')).toBeInTheDocument()
    expect(within(claude).getByText('API key')).toBeInTheDocument()
    expect(within(claude).getByText('Iris')).toBeInTheDocument()
    const cursor = screen.getByText('Cursor').closest('li')!
    expect(within(cursor).getByText('Not installed')).toBeInTheDocument()
    expect(within(cursor).getByText(/0 runs/)).toBeInTheDocument()
  })

  it('subscription-only usage never shows a dollar amount', async () => {
    vi.stubGlobal('fetch', mockApi(routes(body([row({ runs: 2, succeeded: 2, subscription_runs: 2, input_tokens: 5 })]))).impl)
    renderWidget()
    const li = (await screen.findByText('Claude Code')).closest('li')!
    expect(within(li).getByText(/2 runs: subscription — not billed here/)).toBeInTheDocument()
    expect(li.textContent).not.toMatch(/\$/)
    expect(li.textContent).not.toMatch(/Unpriced/)
  })

  it('explains how to start when no CLI has run, and the period toggle refetches', async () => {
    const api = mockApi(routes(body([row({}), row({ runtime: 'cli:cursor' })])))
    vi.stubGlobal('fetch', api.impl)
    renderWidget()
    expect(await screen.findByText(/No CLI runs this month/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Today' }))
    expect(await screen.findByText(/No CLI runs today/)).toBeInTheDocument()
    await waitFor(() => expect(api.calls.filter((c) => c.path.endsWith('/finance/cli-usage')).length).toBeGreaterThanOrEqual(2))
  })

  it('says not available on an older backend (404)', async () => {
    const r = routes(undefined) as Record<string, unknown>
    delete r['/companies/c1/finance/cli-usage']
    vi.stubGlobal('fetch', mockApi(r).impl)
    renderWidget()
    expect(await screen.findByText(/Not built yet/)).toBeInTheDocument()
  })

  it('shows an error with retry', async () => {
    const r = routes(() => new Response(JSON.stringify({ error: { code: 'boom', message: 'Boom', details: null } }), { status: 500, headers: { 'content-type': 'application/json' } }))
    vi.stubGlobal('fetch', mockApi(r).impl)
    renderWidget()
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
