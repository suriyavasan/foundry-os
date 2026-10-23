import { screen } from '@testing-library/react'
import { Route, Routes, useLocation } from 'react-router'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { jsonResponse, makeAgent, makeCompany, page } from './factories'
import { renderWithProviders } from './render'

export const founder = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
export const iris = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Market Researcher', manager_agent_id: 'f1' })

/** A backend without the M5 endpoints: they all 404 (the mock does that for unknown paths). */
export const baseRoutes = {
  '/companies/c1': makeCompany({ id: 'c1' }),
  '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme Labs', role: 'owner' }] },
  '/companies': page([makeCompany({ id: 'c1' })]),
  '/companies/c1/agents': page([founder, iris]),
  '/companies/c1/departments': page([]),
  '/companies/c1/activity': page([]),
  '/runtimes': { items: [] },
}

export function Probe() {
  const l = useLocation()
  return <output data-testid="loc">{l.search}</output>
}

export function renderCanvas(route = '/c/c1') {
  return renderWithProviders(
    <Routes>
      <Route
        path="/c/:companyId"
        element={
          <>
            <CompanyLayout />
            <Probe />
          </>
        }
      >
        <Route index element={<CanvasPage />} />
      </Route>
    </Routes>,
    { route },
  )
}

export const loc = () => decodeURIComponent(screen.getByTestId('loc').textContent ?? '')

/** A promise you resolve by hand, to observe the UI while a request is in flight. */
export function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

export const apiError = (status: number, message: string) => jsonResponse({ error: { code: status === 404 ? 'not_found' : 'internal_error', message, details: null } }, status)
