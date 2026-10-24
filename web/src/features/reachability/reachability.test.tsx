import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, page } from '@/test/factories'
import { baseRoutes, loc, renderCanvas } from '@/test/m5'
import { makeAccount, makeReach } from '@/test/m8'

afterEach(() => vi.unstubAllGlobals())
beforeEach(() => localStorage.clear())

const publicTunnel = { provider: 'cloudflare_quick' as const, state: 'running' as const, url: 'https://calm-otter.trycloudflare.com', healthy: true, last_health_check_at: '2026-10-24T10:00:00Z' }
const m8 = (reach = makeReach(), extra: Record<string, unknown> = {}) => ({
  ...baseRoutes,
  '/reachability': reach,
  '/companies/c1/connected-accounts': page([]),
  ...extra,
})
const panel = (name = 'Reachability') => screen.findByRole('dialog', { name })

function boot(routes: Record<string, unknown>, route = '/c/c1?panel=reachability') {
  const api = mockApi(routes)
  vi.stubGlobal('fetch', api.impl)
  renderCanvas(route)
  return api
}

describe('M8 endpoints missing', () => {
  it('shows "Arrives in M8" in the panel and Integrations, and draws nothing in the top bar, canvas or palette', async () => {
    boot(baseRoutes)
    const p = await panel()
    expect(await within(p).findByText('Reachability is not built yet')).toBeInTheDocument()
    expect(within(p).getByText(/Arrives in M8/)).toBeInTheDocument()
    expect(screen.queryByTestId('reachability-indicator')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'How to reach your company' })).not.toBeInTheDocument()
  })

  it('Integrations shows connected accounts as not built yet', async () => {
    boot(baseRoutes, '/c/c1?panel=integrations')
    const p = await panel('Integrations')
    expect(await within(p).findByText('Connected accounts are not built yet')).toBeInTheDocument()
  })

  it('hides the reachability palette commands', async () => {
    boot(baseRoutes, '/c/c1')
    const user = userEvent.setup()
    await screen.findByText('Atlas')
    await new Promise((r) => setTimeout(r, 50))
    await user.keyboard('{Control>}k{/Control}')
    await user.type(await screen.findByRole('combobox', { name: 'Search commands' }), 'public url')
    await waitFor(() => expect(screen.queryByRole('option', { name: /Copy public URL/ })).not.toBeInTheDocument())
  })
})

describe('top-bar globe', () => {
  it.each([
    ['local', makeReach(), 'Reachability: Local only, http://127.0.0.1:8700. Open details'],
    ['lan', makeReach({ lan: { enabled: true } }), 'Reachability: Local network, http://sams-macbook.local:8700. Open details'],
    ['public', makeReach({ tunnel: publicTunnel }), 'Reachability: Public, https://calm-otter.trycloudflare.com. Open details'],
  ])('shows the %s state and opens the panel', async (mode, reach, label) => {
    boot(m8(reach), '/c/c1')
    const btn = await screen.findByRole('button', { name: label })
    expect(screen.getByTestId('reachability-indicator')).toHaveAttribute('data-mode', mode)
    await userEvent.setup().click(btn)
    expect(loc()).toBe('?panel=reachability')
  })

  it('copies the active address', async () => {
    boot(m8(makeReach({ tunnel: publicTunnel })), '/c/c1')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Copy active address' }))
    expect(await navigator.clipboard.readText()).toBe('https://calm-otter.trycloudflare.com')
  })
})

describe('first-run card', () => {
  it('shows the three ways in, opens the panel, and stays dismissed', async () => {
    boot(m8(makeReach({ lan: { enabled: true } })), '/c/c1')
    const user = userEvent.setup()
    const card = await screen.findByRole('region', { name: 'How to reach your company' })
    expect(within(card).getByText('http://127.0.0.1:8700')).toBeInTheDocument()
    expect(within(card).getByText('http://sams-macbook.local:8700')).toBeInTheDocument()
    expect(within(card).getByText('No public address yet.')).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: 'QR code for http://sams-macbook.local:8700' })).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Open reachability' }))
    expect(loc()).toBe('?panel=reachability')
    await user.click(within(card).getAllByRole('button', { name: 'Dismiss' })[0]!)
    expect(screen.queryByRole('region', { name: 'How to reach your company' })).not.toBeInTheDocument()
    expect(localStorage.getItem('company-os.reach-card-dismissed.c1')).toBe('1')
  })
})

describe('device card: LAN', () => {
  it('is disabled with the server’s explanation while password auth is not active', async () => {
    boot(m8(makeReach({ public_exposure_blocked_reason: 'Password sign-in is not active yet.', public_exposure_blocked_code: 'auth_local' })))
    const p = await panel()
    const sw = await within(p).findByRole('switch', { name: 'Enable LAN access' })
    expect(sw).toBeDisabled()
    expect(sw).toHaveAttribute('aria-checked', 'false')
    expect(within(p).getAllByText('Password sign-in is not active yet.').length).toBeGreaterThan(0)
    await userEvent.setup().click(within(within(p).getByRole('region', { name: 'On your network' })).getByRole('button', { name: 'Open Account' }))
    await waitFor(() => expect(loc()).toBe('?panel=account'))
  })

  it('turns on after a confirmation, then shows LAN URLs with QR codes', async () => {
    let enabled = false
    const api = boot(
      m8(makeReach(), {
        '/reachability': () => jsonResponse(makeReach({ lan: { enabled } })),
        'PATCH /reachability/lan': (_u: URL, init?: RequestInit) => {
          enabled = JSON.parse(String(init?.body)).enabled
          return jsonResponse(makeReach({ lan: { enabled } }))
        },
      }),
    )
    const user = userEvent.setup()
    const p = await panel()
    const sw = await within(p).findByRole('switch', { name: 'Enable LAN access' })
    expect(sw).toBeEnabled()
    await user.click(sw)
    await user.click(await screen.findByRole('button', { name: 'Turn on' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH' && c.path === '/reachability/lan' && (c.body as { enabled: boolean }).enabled === true)).toBe(true))
    const net = await within(p).findByRole('region', { name: 'On your network' })
    await waitFor(() => expect(within(net).getByText('http://192.168.1.20:8700')).toBeInTheDocument())
    expect(within(net).getByText('http://sams-macbook.local:8700')).toBeInTheDocument()
    // the first (best) address shows its QR code, the other one on request
    expect(within(net).getByRole('img', { name: 'QR code for http://sams-macbook.local:8700' })).toBeInTheDocument()
    expect(within(net).queryByRole('img', { name: 'QR code for http://192.168.1.20:8700' })).not.toBeInTheDocument()
    await user.click(within(net).getByRole('button', { name: 'Show QR code for http://192.168.1.20:8700' }))
    expect(within(net).getByRole('img', { name: 'QR code for http://192.168.1.20:8700' })).toBeInTheDocument()
  })

  it('turning it off needs no confirmation', async () => {
    const api = boot(m8(makeReach({ lan: { enabled: true } }), { 'PATCH /reachability/lan': () => jsonResponse(makeReach()) }))
    const p = await panel()
    await userEvent.setup().click(await within(p).findByRole('switch', { name: 'Enable LAN access' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PATCH' && (c.body as { enabled: boolean }).enabled === false)).toBe(true))
  })
})

describe('tunnel card', () => {
  const tunnelCard = async () => within(await panel()).findByLabelText('Public URL')

  it('starts a quick tunnel after a confirmation', async () => {
    const api = boot(m8(makeReach(), { 'POST /reachability/tunnel/start': () => jsonResponse(makeReach({ tunnel: { state: 'starting', provider: 'cloudflare_quick' } }).tunnel) }))
    const user = userEvent.setup()
    const card = await tunnelCard()
    await user.click(await within(card).findByRole('button', { name: 'Start tunnel' }))
    const confirm = await screen.findByRole('dialog', { name: 'Make this app reachable from the internet?' })
    await user.click(within(confirm).getByRole('button', { name: 'Start tunnel' }))
    await waitFor(() => expect(api.calls.find((c) => c.path === '/reachability/tunnel/start')?.body).toEqual({ provider: 'cloudflare_quick' }))
  })

  it('refuses to start while exposure is blocked, saying why', async () => {
    boot(m8(makeReach({ public_exposure_blocked_reason: 'The owner password is too weak.' })))
    const card = await tunnelCard()
    expect(await within(card).findByRole('button', { name: 'Start tunnel' })).toBeDisabled()
    expect(within(card).getByTestId('start-blocked')).toHaveTextContent('The owner password is too weak.')
  })

  it('running: shows the public URL with health and offers Stop', async () => {
    const api = boot(m8(makeReach({ tunnel: publicTunnel }), { 'POST /reachability/tunnel/stop': () => jsonResponse(makeReach().tunnel) }))
    const user = userEvent.setup()
    const card = await tunnelCard()
    expect(await within(card).findByText('https://calm-otter.trycloudflare.com')).toBeInTheDocument()
    expect(within(card).getByText('Running')).toBeInTheDocument()
    expect(within(card).getByText(/Health checked/)).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Start tunnel' })).not.toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Stop tunnel' }))
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/reachability/tunnel/stop')).toBe(true))
  })

  it('error: shows the error and offers to start again', async () => {
    boot(m8(makeReach({ tunnel: { state: 'error', error: 'cloudflared exited with code 1', restarts: 3 } })))
    const card = await tunnelCard()
    expect(await within(card).findByRole('alert')).toHaveTextContent('cloudflared exited with code 1')
    expect(within(card).getByRole('button', { name: 'Start again' })).toBeEnabled()
    expect(within(card).getByRole('button', { name: 'Stop tunnel' })).toBeEnabled()
  })

  it('not installed: shows install steps and the verified download with its checksum', async () => {
    const sha = 'a'.repeat(64)
    const api = boot(
      m8(makeReach({ tunnel: { binary: { name: 'cloudflared', found: false, version: null, path: null, install_instructions: ['Run: brew install cloudflared'], download_offered: true, download_url: 'https://github.com/cloudflare/cloudflared/releases/x', download_sha256: sha, download_size_bytes: 36_000_000 } } }), {
        'POST /reachability/tunnel/install': () => jsonResponse(makeReach().tunnel),
      }),
    )
    const user = userEvent.setup()
    const card = await tunnelCard()
    const block = await within(card).findByRole('region', { name: 'cloudflared is not installed' })
    expect(within(block).getByText('Run: brew install cloudflared')).toBeInTheDocument()
    expect(within(block).getByText(sha)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Start tunnel' })).toBeDisabled()
    await user.click(within(block).getByRole('button', { name: 'Download and verify cloudflared' }))
    await user.click(await screen.findByRole('button', { name: 'Download and verify' }))
    await waitFor(() => expect(api.calls.some((c) => c.path === '/reachability/tunnel/install' && (c.body as { confirm: boolean }).confirm === true)).toBe(true))
  })
})

describe('checklist', () => {
  it('reflects real account status and the AI Co-founder follow-up', async () => {
    boot(m8(makeReach(), { '/companies/c1/connected-accounts': page([makeAccount(), makeAccount({ id: 'g', provider: 'github', status: 'invalid' })]) }))
    const list = await within(await panel()).findByRole('region', { name: 'Your one-time steps' })
    await waitFor(() => expect(list.querySelector('[data-step="cloudflare"]')).toHaveAttribute('data-status', 'done'))
    expect(list.querySelector('[data-step="github"]')).toHaveAttribute('data-status', 'attention')
    expect(list.querySelector('[data-step="email"]')).toHaveAttribute('data-status', 'todo')
    expect(within(list).getByText('2 of 4 done')).toBeInTheDocument()
    expect(within(list).getAllByText('Then the AI Co-founder:').length).toBe(5)
  })
})
