import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeOverview } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { CommandCenterPage } from './CommandCenterPage'

afterEach(() => vi.unstubAllGlobals())

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/c/:companyId/command-center" element={<CommandCenterPage />} />
    </Routes>,
    { route: '/c/c1/command-center' },
  )
}

describe('CommandCenterPage', () => {
  it('renders unbuilt subsystems as not available with their milestone, and real counts', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(makeOverview()))))
    renderPage()
    expect(await screen.findByText('Ship the thing')).toBeInTheDocument()
    expect(screen.getAllByText('Arrives in M3').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Arrives in M6')).toHaveLength(2)
    expect(screen.getByText('Arrives in M7')).toBeInTheDocument()
    expect(screen.getByText('Arrives in M5')).toBeInTheDocument()
    expect(screen.getByText('Not tracked until M3')).toBeInTheDocument()
    expect(screen.getByText('Atlas')).toBeInTheDocument()
    expect(screen.getByText('Not assigned')).toBeInTheDocument()
  })

  it('shows an error state with retry when the API fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse({ error: { code: 'internal_error', message: 'Boom', details: null } }, 500))),
    )
    renderPage()
    expect(await screen.findByText('Boom', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
