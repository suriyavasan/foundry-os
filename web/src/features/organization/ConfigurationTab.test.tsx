import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { makeAgent, makeCompany, mockApi, page } from '@/test/factories'
import { renderWithProviders } from '@/test/render'

const founder = makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder', role_title: 'Founder & CEO', runtime: 'unassigned' })
const provider = { id: 'p1', name: 'Local Ollama', kind: 'ollama', base_url: 'http://127.0.0.1:11434', credential_id: null, default_model: 'qwen2.5:7b', context_length: null, is_local: true, enabled: true, input_price_per_mtok_micros: null, output_price_per_mtok_micros: null }
const runtimes = { items: [{ id: 'company_agent', display_name: 'Company Agent (Hermes fork)', kind: 'company_agent', capabilities: [], health: { status: 'ok', detail: '', checked_at: '2026-10-23T12:00:00Z' }, config_hint: null }] }
const base = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder]),
  '/companies/c1/agents/f1': founder,
  '/companies/c1/departments': page([]),
  '/companies/c1/activity': page([]),
  '/runtimes': runtimes,
}

async function openConfig(routes: Record<string, unknown>) {
  const api = mockApi({ ...base, ...routes })
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(
    <Routes>
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route: '/c/c1?panel=agent:f1' },
  )
  const panel = await screen.findByRole('dialog', { name: /Atlas/ })
  await userEvent.click(await within(panel).findByRole('tab', { name: 'Configuration' }))
  return { api, panel }
}

afterEach(() => vi.unstubAllGlobals())

describe('Agent configuration: runtime and model provider', () => {
  it('offers registered runtimes by name and registered providers, and fills the default model', async () => {
    const { api, panel } = await openConfig({ '/companies/c1/model-providers': page([provider]), 'PATCH /companies/c1/agents/f1': { ...founder, runtime: 'company_agent', model_provider: 'p1', model_name: 'qwen2.5:7b' } })
    const runtime = await within(panel).findByLabelText('Runtime')
    expect(within(runtime).getByRole('option', { name: 'Company Agent (Hermes fork)' })).toBeInTheDocument()
    const select = await within(panel).findByLabelText('Model provider')
    expect(select.tagName).toBe('SELECT')
    await userEvent.selectOptions(runtime, 'company_agent')
    await userEvent.selectOptions(select, 'p1')
    expect(within(panel).getByLabelText('Model name')).toHaveValue('qwen2.5:7b')
    await userEvent.click(within(panel).getByRole('button', { name: 'Save configuration' }))
    await waitFor(() => expect(api.calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ runtime: 'company_agent', model_provider: 'p1', model_name: 'qwen2.5:7b' }))
  })

  it('falls back to free text when no providers are registered (or the endpoint does not exist yet)', async () => {
    const { panel } = await openConfig({})
    const field = await within(panel).findByLabelText('Model provider')
    expect(field.tagName).toBe('INPUT')
    expect(within(panel).getByText(/Register one in Integrations/)).toBeInTheDocument()
  })
})
