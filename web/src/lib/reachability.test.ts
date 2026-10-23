import { describe, expect, it } from 'vitest'
import { makeAccount, makeReach } from '@/test/m8'
import {
  activeUrlOf,
  blockedReason,
  buildChecklist,
  canRenderQr,
  checklistProgress,
  deriveMode,
  globeModel,
  lanToggle,
  lanUrls,
  normalizeReachability,
  publicUrlOf,
  tunnelControls,
  tunnelStartBody,
  validateHostname,
} from './reachability'

const running = { state: 'running' as const, provider: 'cloudflare_quick' as const, url: 'https://random-words.trycloudflare.com', healthy: true }

describe('mode and active URL', () => {
  it('is local with nothing switched on', () => {
    const s = makeReach()
    expect(deriveMode(s)).toBe('local')
    expect(activeUrlOf(s)).toBe('http://127.0.0.1:8700')
  })
  it('is LAN when bound to the network, preferring the .local name', () => {
    const s = makeReach({ lan: { enabled: true } })
    expect(deriveMode(s)).toBe('lan')
    expect(activeUrlOf(s)).toBe('http://sams-macbook.local:8700')
    expect(lanUrls(s).map((u) => u.url)).toEqual(['http://sams-macbook.local:8700', 'http://192.168.1.20:8700'])
  })
  it('is public only while the tunnel is running with a URL', () => {
    expect(deriveMode(makeReach({ lan: { enabled: true }, tunnel: running }))).toBe('public')
    expect(activeUrlOf(makeReach({ tunnel: running }))).toBe('https://random-words.trycloudflare.com')
    // starting, errored or stopped tunnels are not a public URL, even if the server left a stale one
    for (const state of ['starting', 'error', 'stopped'] as const) {
      const s = makeReach({ tunnel: { ...running, state } })
      expect(publicUrlOf(s)).toBeNull()
      expect(deriveMode(s)).toBe('local')
    }
  })
  it('ignores the server mode when the facts disagree', () => {
    expect(deriveMode(makeReach({ mode: 'public' }))).toBe('local')
  })
})

describe('the top-bar globe', () => {
  it('maps the three states with their URL in the tooltip', () => {
    expect(globeModel(makeReach())).toMatchObject({ mode: 'local', label: 'Local only', tone: 'neutral', tooltip: 'Local only · http://127.0.0.1:8700' })
    expect(globeModel(makeReach({ lan: { enabled: true } }))).toMatchObject({ mode: 'lan', label: 'Local network', tone: 'info', url: 'http://sams-macbook.local:8700' })
    expect(globeModel(makeReach({ tunnel: running }))).toMatchObject({ mode: 'public', label: 'Public', tone: 'success', url: 'https://random-words.trycloudflare.com' })
  })
  it('is unknown without a status', () => {
    expect(globeModel(null)).toMatchObject({ mode: null, label: 'Reachability unknown', url: null })
  })
  it('flags a tunnel error and an unhealthy public URL', () => {
    expect(globeModel(makeReach({ tunnel: { state: 'error', error: 'cloudflared exited' } }))).toMatchObject({ tone: 'warning', problem: 'cloudflared exited', mode: 'local' })
    expect(globeModel(makeReach({ tunnel: { ...running, healthy: false } }))).toMatchObject({ mode: 'public', tone: 'warning' })
  })
})

describe('the LAN toggle gate', () => {
  it('is allowed when nothing blocks exposure', () => {
    expect(lanToggle(makeReach())).toEqual({ checked: false, disabled: false, reason: null })
  })
  it('is disabled with the server’s own reason', () => {
    const s = makeReach({ public_exposure_blocked_reason: 'Password sign-in is not active.' })
    expect(lanToggle(s)).toEqual({ checked: false, disabled: true, reason: 'Password sign-in is not active.' })
  })
  it('falls back to a fixed text per code when the server sent only a code', () => {
    expect(blockedReason(makeReach({ public_exposure_blocked_code: 'weak_password' }))).toMatch(/too weak/)
    expect(lanToggle(makeReach({ public_exposure_blocked_code: 'auth_local' })).disabled).toBe(true)
    expect(blockedReason(makeReach({ public_exposure_blocked_code: 'something_new' }))).toBeNull()
  })
  it('can always be switched off', () => {
    const s = makeReach({ lan: { enabled: true }, public_exposure_blocked_reason: 'weak now' })
    expect(lanToggle(s)).toEqual({ checked: true, disabled: false, reason: null })
  })
})

describe('tunnel controls state machine', () => {
  const opts = { provider: 'cloudflare_quick' as const, hostname: '', accounts: [] }
  it('stopped: start is the action', () => {
    expect(tunnelControls(makeReach(), opts)).toMatchObject({ primary: 'start', canStart: true, canStop: false, startBlockedReason: null })
  })
  it('starting: only cancel; running: only stop', () => {
    expect(tunnelControls(makeReach({ tunnel: { state: 'starting' } }), opts)).toMatchObject({ primary: 'none', canStart: false, canStop: true })
    expect(tunnelControls(makeReach({ tunnel: running }), opts)).toMatchObject({ primary: 'stop', canStart: false, canStop: true })
  })
  it('error: start again or stop', () => {
    expect(tunnelControls(makeReach({ tunnel: { state: 'error', error: 'x' } }), opts)).toMatchObject({ primary: 'retry', canStart: true, canStop: true })
  })
  it('refuses to start while exposure is blocked', () => {
    const c = tunnelControls(makeReach({ public_exposure_blocked_reason: 'Set a password first.' }), opts)
    expect(c).toMatchObject({ canStart: false, startBlockedReason: 'Set a password first.' })
  })
  it('needs the binary: missing cloudflared means install, not start', () => {
    const s = makeReach({ tunnel: { binary: { name: 'cloudflared', found: false, version: null, path: null, install_instructions: ['brew install cloudflared'], download_offered: false, download_url: null, download_sha256: null, download_size_bytes: null } } })
    expect(tunnelControls(s, opts)).toMatchObject({ needsInstall: true, canStart: false, startBlockedReason: 'cloudflared is not installed' })
    // Tailscale does not use that binary
    expect(tunnelControls(s, { provider: 'tailscale_funnel', hostname: 'x.example.ts.net', accounts: [makeAccount({ provider: 'tailscale' })] }).needsInstall).toBe(false)
  })
  it('a named tunnel needs a connected account and a hostname', () => {
    const named = { provider: 'cloudflare_named' as const, hostname: '', accounts: [] }
    expect(tunnelControls(makeReach(), named).startBlockedReason).toMatch(/Connect a Cloudflare account/)
    expect(tunnelControls(makeReach(), { ...named, accounts: [makeAccount({ status: 'invalid' })] }).startBlockedReason).toMatch(/not working/)
    expect(tunnelControls(makeReach(), { ...named, accounts: [makeAccount()] }).startBlockedReason).toMatch(/hostname/)
    expect(tunnelControls(makeReach(), { ...named, hostname: 'app.example.com', accounts: [makeAccount()] }).canStart).toBe(true)
  })
  it('respects a provider the server says is unavailable', () => {
    const s = makeReach({ tunnel: { providers: [{ kind: 'tailscale_funnel', requires_account: 'tailscale', available: false, unavailable_reason: 'Tailscale is not installed' }] } })
    expect(tunnelControls(s, { provider: 'tailscale_funnel', hostname: 'a.b.ts.net', accounts: [makeAccount({ provider: 'tailscale' })] }).startBlockedReason).toBe('Tailscale is not installed')
  })
  it('builds the start body: a hostname only for providers that serve one', () => {
    expect(tunnelStartBody('cloudflare_quick', 'ignored.example.com')).toEqual({ provider: 'cloudflare_quick' })
    expect(tunnelStartBody('cloudflare_named', ' App.Example.com ')).toEqual({ provider: 'cloudflare_named', hostname: 'app.example.com' })
  })
  it('validates hostnames', () => {
    expect(validateHostname('app.example.com')).toBeNull()
    expect(validateHostname('https://app.example.com')).not.toBeNull()
    expect(validateHostname('')).not.toBeNull()
    expect(validateHostname('nodot')).not.toBeNull()
  })
})

describe('checklist derivation', () => {
  const ids = (s: ReturnType<typeof buildChecklist>) => Object.fromEntries(s.map((x) => [x.id, x.status]))
  it('starts as to-do with a password gate and no accounts', () => {
    const steps = buildChecklist({ reachability: makeReach({ public_exposure_blocked_code: 'auth_local' }), accounts: [] })
    expect(ids(steps)).toEqual({ password: 'todo', cloudflare: 'todo', github: 'todo', vercel: 'todo', email: 'todo' })
    expect(checklistProgress(steps)).toEqual({ done: 0, total: 4, complete: false })
    expect(steps.find((s) => s.id === 'vercel')!.optional).toBe(true)
    for (const s of steps) expect(s.after.length).toBeGreaterThan(10)
  })
  it('reads real account status', () => {
    const steps = buildChecklist({
      reachability: makeReach(),
      accounts: [makeAccount(), makeAccount({ id: 'g', provider: 'github', status: 'invalid' }), makeAccount({ id: 'r', provider: 'resend' }), makeAccount({ id: 'v', provider: 'vercel', status: 'revoked' })],
    })
    expect(ids(steps)).toEqual({ password: 'done', cloudflare: 'done', github: 'attention', vercel: 'todo', email: 'done' })
    expect(checklistProgress(steps)).toEqual({ done: 3, total: 4, complete: false })
  })
  it('is complete when every required step is done (Vercel is optional)', () => {
    const accounts = ['cloudflare', 'github', 'agentmail'].map((p, i) => makeAccount({ id: `a${i}`, provider: p }))
    expect(checklistProgress(buildChecklist({ reachability: makeReach(), accounts })).complete).toBe(true)
  })
  it('is unknown, not done, while the accounts or status are unavailable', () => {
    const steps = buildChecklist({ reachability: null, accounts: null })
    expect(Object.values(ids(steps))).toEqual(['unknown', 'unknown', 'unknown', 'unknown', 'unknown'])
  })
})

describe('normalizeReachability', () => {
  it('fills defaults for a sparse payload and builds missing LAN URLs from the local port', () => {
    const s = normalizeReachability({ hostname: 'box', local_urls: ['http://127.0.0.1:9000'], lan: { enabled: true, addresses: [{ address: '10.0.0.5' }, { address: 'fe80::1', family: 'ipv6' }, { nothing: true }] } })
    expect(s.device_name).toBe('box')
    expect(s.lan.addresses.map((a) => a.url)).toEqual(['http://10.0.0.5:9000', 'http://[fe80::1]:9000'])
    expect(s.tunnel).toMatchObject({ state: 'stopped', url: null, providers: [] })
    expect(s.public_exposure_blocked_reason).toBeNull()
  })
  it('survives garbage', () => {
    expect(normalizeReachability(null).local_urls).toEqual([])
    expect(normalizeReachability({ tunnel: { state: 'weird', provider: 'x' } }).tunnel).toMatchObject({ state: 'stopped', provider: null })
  })
})

describe('QR eligibility', () => {
  it('draws http(s) URLs and nothing else', () => {
    expect(canRenderQr('http://192.168.1.20:8700')).toBe(true)
    expect(canRenderQr('https://x.trycloudflare.com')).toBe(true)
    expect(canRenderQr('javascript:alert(1)')).toBe(false)
    expect(canRenderQr('not a url')).toBe(false)
    expect(canRenderQr(null)).toBe(false)
    expect(canRenderQr(`http://x/${'a'.repeat(600)}`)).toBe(false)
  })
})
