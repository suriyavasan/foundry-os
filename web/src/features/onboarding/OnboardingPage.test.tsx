import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { OnboardingPage } from './OnboardingPage'

afterEach(() => vi.unstubAllGlobals())

describe('OnboardingPage', () => {
  it('blocks submit and shows an error when the name is empty', async () => {
    const fetchMock = vi.fn((url: string, _init?: RequestInit) =>
      Promise.resolve(url.startsWith('/api/companies') ? jsonResponse({ items: [], next_cursor: null }) : jsonResponse({})),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderWithProviders(<OnboardingPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Create company' }))
    expect(await screen.findByText('Company name is required')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false)
  })

  it('shows server 422 details on the offending field', async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return Promise.resolve(
          jsonResponse(
            { error: { code: 'validation_error', message: 'Invalid', details: [{ loc: ['body', 'name'], msg: 'Name already taken' }] } },
            422,
          ),
        )
      }
      return Promise.resolve(url.startsWith('/api/companies') ? jsonResponse({ items: [], next_cursor: null }) : jsonResponse({}))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderWithProviders(<OnboardingPage />)
    await userEvent.type(screen.getByLabelText(/Company name/), 'Acme')
    await userEvent.click(screen.getByRole('button', { name: 'Create company' }))
    await waitFor(() => expect(screen.getByText('Name already taken')).toBeInTheDocument())
    expect(screen.getByLabelText(/Company name/)).toHaveAttribute('aria-invalid', 'true')
  })
})
