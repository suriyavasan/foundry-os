import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UpdateStatus } from '@/api/types'
import { AuthShell } from '@/features/auth/AuthShell'
import { WelcomeStep } from '@/features/onboarding/steps/WelcomeStep'
import { jsonResponse, mockApi } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import indexHtml from '../../../index.html?raw'
import { UpdateBanner } from './UpdateBanner'

const status = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  enabled: true,
  current_version: '0.1.0',
  latest_version: '0.2.0',
  update_available: true,
  checked_at: '2026-10-24T10:00:00Z',
  release_url: 'https://github.com/suriyavasan/foundry-os/releases/tag/v0.2.0',
  install_kind: 'installer',
  update_command: 'foundryos update',
  error: null,
  ...over,
})

function boot(routes: Record<string, unknown>) {
  const api = mockApi(routes)
  vi.stubGlobal('fetch', api.impl)
  renderWithProviders(<UpdateBanner />)
  return api
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('update banner', () => {
  it('announces a newer release with the command for this kind of install', async () => {
    boot({ '/system/update': status() })
    expect(await screen.findByText(/Foundry AI OS 0.2.0 is available/)).toBeInTheDocument()
    expect(screen.getByText('foundryos update')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /What’s new/ })).toHaveAttribute('href', expect.stringContaining('/releases/tag/v0.2.0'))
  })

  it('shows the Homebrew command for a Homebrew install', async () => {
    boot({ '/system/update': status({ install_kind: 'homebrew', update_command: 'brew upgrade foundry-os && brew services restart foundry-os' }) })
    expect(await screen.findByText(/brew upgrade foundry-os/)).toBeInTheDocument()
  })

  it('renders nothing for a non-owner (403)', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', async (...args: Parameters<typeof fetch>) => {
      calls.push(String(args[0]))
      return jsonResponse({ error: { code: 'forbidden', message: 'Only the installation owner', details: null } }, 403)
    })
    renderWithProviders(<UpdateBanner />)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    expect(screen.queryByRole('status', { name: 'Update available' })).not.toBeInTheDocument()
  })

  it('renders nothing when there is no update', async () => {
    const api = boot({ '/system/update': status({ update_available: false, latest_version: '0.1.0' }) })
    await waitFor(() => expect(api.calls.length).toBe(1))
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
  })

  it('dismissing hides this version and remembers it, but a newer one shows again', async () => {
    const user = userEvent.setup()
    boot({ '/system/update': status() })
    await screen.findByText(/0.2.0 is available/)
    await user.click(screen.getByRole('button', { name: 'Dismiss update notice' }))
    expect(screen.queryByText(/0.2.0 is available/)).not.toBeInTheDocument()
    expect(localStorage.getItem('company-os.update-dismissed')).toBe('0.2.0')
  })

  it('stays hidden after a reload once dismissed', async () => {
    localStorage.setItem('company-os.update-dismissed', '0.2.0')
    const api = boot({ '/system/update': status() })
    await waitFor(() => expect(api.calls.length).toBe(1))
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
  })

  it('shows again for a newer release than the dismissed one', async () => {
    localStorage.setItem('company-os.update-dismissed', '0.2.0')
    boot({ '/system/update': status({ latest_version: '0.3.0' }) })
    expect(await screen.findByText(/0.3.0 is available/)).toBeInTheDocument()
  })

  it('turns the daily check off through the API', async () => {
    const user = userEvent.setup()
    const api = boot({
      '/system/update': status(),
      'PUT /system/update/settings': status({ enabled: false, update_available: false }),
    })
    await screen.findByText(/0.2.0 is available/)
    await user.click(screen.getByRole('button', { name: 'Turn off update checks' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT' && c.path === '/system/update/settings')).toBe(true))
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ enabled: false })
    await waitFor(() => expect(screen.queryByText(/0.2.0 is available/)).not.toBeInTheDocument())
  })
})

describe('Foundry AI OS branding', () => {
  it('the page title and welcome copy say Foundry AI OS', () => {
    expect(indexHtml).toContain('<title>Foundry AI OS</title>')
    expect(indexHtml).not.toMatch(/Company OS/)
    renderWithProviders(<WelcomeStep onStart={() => {}} />)
    expect(screen.getByText('Foundry AI OS')).toBeInTheDocument()
  })

  it('the sign-in shell says Foundry AI OS', () => {
    renderWithProviders(<AuthShell><p>child</p></AuthShell>)
    expect(screen.getByText('Foundry AI OS')).toBeInTheDocument()
    expect(screen.queryByText('Company OS')).not.toBeInTheDocument()
  })
})
