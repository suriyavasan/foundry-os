import type {
  ConnectedAccount,
  ConnectedProvider,
  EmailIdentity,
  LanAddress,
  ReachabilityMode,
  ReachabilityStatus,
  TunnelBinary,
  TunnelConfig,
  TunnelProviderKind,
  TunnelProviderOption,
  TunnelState,
  TunnelStatus,
} from '@/api/types'
import { qrMatrix } from './qr'

/**
 * Reachability (M8). Pure helpers: defensive normalising of `GET /reachability` (the backend did not exist when this
 * was written), the mode / active URL derivation, the LAN toggle gate, the tunnel control state machine and the
 * "one-time steps" checklist. Nothing here invents a URL: every URL shown comes from the server.
 */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.trim() !== '') : [])

/* ------------------------------------ normalising ------------------------------------ */

const TUNNEL_STATES: readonly TunnelState[] = ['stopped', 'starting', 'running', 'error']
const TUNNEL_KINDS: readonly TunnelProviderKind[] = ['cloudflare_quick', 'cloudflare_named', 'tailscale_funnel']

function portOf(urls: string[]): string {
  for (const u of urls) {
    try {
      const p = new URL(u).port
      if (p) return p
    } catch {
      /* try the next */
    }
  }
  return ''
}

function normalizeBinary(raw: unknown): TunnelBinary | null {
  const o = obj(raw)
  if (!o) return null
  return {
    name: str(o.name) ?? 'cloudflared',
    found: o.found === true,
    version: str(o.version),
    path: str(o.path),
    install_instructions: strs(o.install_instructions),
    download_offered: o.download_offered === true,
    download_url: str(o.download_url),
    download_sha256: str(o.download_sha256),
    download_size_bytes: num(o.download_size_bytes),
  }
}

function normalizeProviderOption(raw: unknown): TunnelProviderOption | null {
  const o = obj(raw)
  const kind = str(o?.kind)
  if (!o || !kind || !(TUNNEL_KINDS as readonly string[]).includes(kind)) return null
  return { kind: kind as TunnelProviderKind, requires_account: str(o.requires_account), available: o.available !== false, unavailable_reason: str(o.unavailable_reason) }
}

export function normalizeTunnel(raw: unknown): TunnelStatus {
  const o = obj(raw) ?? {}
  const state = str(o.state)
  const provider = str(o.provider)
  return {
    provider: provider && (TUNNEL_KINDS as readonly string[]).includes(provider) ? (provider as TunnelProviderKind) : null,
    state: state && (TUNNEL_STATES as readonly string[]).includes(state) ? (state as TunnelState) : 'stopped',
    url: str(o.url),
    hostname: str(o.hostname),
    last_health_check_at: str(o.last_health_check_at),
    healthy: typeof o.healthy === 'boolean' ? o.healthy : null,
    restarts: num(o.restarts) ?? 0,
    started_at: str(o.started_at),
    error: str(o.error),
    binary: normalizeBinary(o.binary),
    providers: (Array.isArray(o.providers) ? o.providers : []).map(normalizeProviderOption).filter((p): p is TunnelProviderOption => p !== null),
  }
}

function normalizeAddress(raw: unknown, port: string): LanAddress | null {
  const o = obj(raw)
  const address = str(o?.address)
  if (!o || !address) return null
  const family = o.family === 'ipv6' || address.includes(':') ? 'ipv6' : 'ipv4'
  const url = str(o.url) ?? (port ? `http://${family === 'ipv6' ? `[${address}]` : address}:${port}` : null)
  return url ? { address, family, interface: str(o.interface), url } : null
}

export function normalizeReachability(raw: unknown): ReachabilityStatus {
  const o = obj(raw) ?? {}
  const local = strs(o.local_urls)
  const lan = obj(o.lan) ?? {}
  const port = portOf(local)
  const domain = obj(o.domain)
  const dnsState = (v: unknown) => (v === 'pending' || v === 'ok' || v === 'error' ? v : 'unknown')
  const tunnel = normalizeTunnel(o.tunnel)
  const status: ReachabilityStatus = {
    mode: o.mode === 'lan' || o.mode === 'public' ? o.mode : 'local',
    device_name: str(o.device_name) ?? str(o.hostname) ?? 'This device',
    hostname: str(o.hostname) ?? '',
    auth_mode: str(o.auth_mode) ?? '',
    local_urls: local,
    lan: {
      enabled: lan.enabled === true,
      mdns_name: str(lan.mdns_name),
      mdns_url: str(lan.mdns_url),
      addresses: (Array.isArray(lan.addresses) ? lan.addresses : []).map((a) => normalizeAddress(a, port)).filter((a): a is LanAddress => a !== null),
    },
    tunnel,
    active_url: str(o.active_url),
    public_exposure_blocked_reason: str(o.public_exposure_blocked_reason),
    public_exposure_blocked_code: str(o.public_exposure_blocked_code),
    domain: domain ? { domain: str(domain.domain), dns_status: dnsState(domain.dns_status), tls_status: dnsState(domain.tls_status) } : null,
  }
  return status
}

/* ------------------------------------ mode & URLs ------------------------------------ */

/** The public URL while a tunnel is actually running, else null (a stopped or failed tunnel is not a public URL). */
export function publicUrlOf(s: Pick<ReachabilityStatus, 'tunnel'> | null | undefined): string | null {
  const t = s?.tunnel
  return t && t.state === 'running' && t.url ? t.url : null
}

/** LAN URLs, name first (it survives a changing DHCP address), then IPv4, then IPv6. */
export function lanUrls(s: Pick<ReachabilityStatus, 'lan'>): Array<{ label: string; url: string }> {
  const out: Array<{ label: string; url: string }> = []
  if (s.lan.mdns_url) out.push({ label: s.lan.mdns_name ?? 'Network name', url: s.lan.mdns_url })
  const v4 = s.lan.addresses.filter((a) => a.family === 'ipv4')
  const v6 = s.lan.addresses.filter((a) => a.family === 'ipv6')
  for (const a of [...v4, ...v6]) out.push({ label: a.interface ? `${a.address} (${a.interface})` : a.address, url: a.url })
  return out
}

/** What is reachable, from the facts (a running tunnel, LAN bound). The server's own `mode` is not trusted over them. */
export function deriveMode(s: ReachabilityStatus): ReachabilityMode {
  if (publicUrlOf(s)) return 'public'
  if (s.lan.enabled) return 'lan'
  return 'local'
}

export function activeUrlOf(s: ReachabilityStatus): string | null {
  const mode = deriveMode(s)
  if (mode === 'public') return publicUrlOf(s)
  if (mode === 'lan') return lanUrls(s)[0]?.url ?? s.active_url
  return s.local_urls[0] ?? s.active_url
}

export const MODE_LABEL: Record<ReachabilityMode, string> = { local: 'Local only', lan: 'Local network', public: 'Public' }

export type GlobeTone = 'neutral' | 'info' | 'success' | 'warning'

export interface GlobeModel {
  /** null while the status is unknown (loading or failed). */
  mode: ReachabilityMode | null
  label: string
  tone: GlobeTone
  url: string | null
  /** One line for the tooltip: label, then the active URL when there is one. */
  tooltip: string
  /** A problem worth a second glance (a tunnel in error, an unhealthy public URL). */
  problem: string | null
}

/** The top-bar globe: `status` null means "unknown" (still loading, or the request failed). */
export function globeModel(status: ReachabilityStatus | null): GlobeModel {
  if (!status) return { mode: null, label: 'Reachability unknown', tone: 'neutral', url: null, tooltip: 'Reachability: unknown', problem: null }
  const mode = deriveMode(status)
  const url = activeUrlOf(status)
  const t = status.tunnel
  const problem = t.state === 'error' ? (t.error ?? 'The tunnel stopped with an error') : mode === 'public' && t.healthy === false ? 'The public URL failed its last health check' : null
  const tone: GlobeTone = problem ? 'warning' : mode === 'public' ? 'success' : mode === 'lan' ? 'info' : 'neutral'
  const label = MODE_LABEL[mode]
  return { mode, label, tone, url, tooltip: [label, url, problem].filter(Boolean).join(' · '), problem }
}

/* ------------------------------------ the LAN toggle ------------------------------------ */

export interface LanToggleModel {
  checked: boolean
  /** Switching on is not allowed; switching off always is. */
  disabled: boolean
  /** Why it is disabled: the server's own words when it gave any. */
  reason: string | null
  /** The server is listening on the network right now. */
  live: boolean
  /** The saved setting is not in effect yet; restarting the server applies it. */
  restartRequired: boolean
}

const BLOCK_FALLBACK: Record<string, string> = {
  auth_local: 'Password sign-in is not active yet. Turn it on in Account before exposing this device to your network.',
  no_password: 'The owner account has no password yet. Set one in Account first.',
  weak_password: 'The owner password is too weak to expose this device. Choose a stronger one in Account.',
}

/** The explanation for a blocked exposure: the server's text, else a fixed one for its code, else a generic one. */
export function blockedReason(s: Pick<ReachabilityStatus, 'public_exposure_blocked_reason' | 'public_exposure_blocked_code'>): string | null {
  if (s.public_exposure_blocked_reason) return s.public_exposure_blocked_reason
  const code = s.public_exposure_blocked_code
  if (code && code in BLOCK_FALLBACK) return BLOCK_FALLBACK[code]!
  return null
}

export function lanToggle(s: ReachabilityStatus): LanToggleModel {
  const reason = blockedReason(s)
  // The switch reflects the saved preference; the server can only rebind on restart.
  const wanted = s.lan.requested ?? s.lan.enabled
  const restartRequired = s.lan.restart_required ?? wanted !== s.lan.enabled
  if (wanted) return { checked: true, disabled: false, reason: null, live: s.lan.enabled, restartRequired }
  return { checked: false, disabled: reason !== null && !s.lan.enabled, reason, live: s.lan.enabled, restartRequired }
}

/* ------------------------------------ the tunnel ------------------------------------ */

export const TUNNEL_PROVIDER_LABEL: Record<TunnelProviderKind, string> = {
  cloudflare_quick: 'Cloudflare quick tunnel',
  cloudflare_named: 'Cloudflare named tunnel',
  tailscale_funnel: 'Tailscale Funnel',
}

export const TUNNEL_PROVIDER_HELP: Record<TunnelProviderKind, string> = {
  cloudflare_quick: 'Free, no account. A random *.trycloudflare.com address that changes every time. Good for trying it out.',
  cloudflare_named: 'Stable address on your own domain. Needs a connected Cloudflare account.',
  tailscale_funnel: 'Public HTTPS through Tailscale. Needs Tailscale installed and a connected Tailscale account.',
}

export const TUNNEL_STATE_LABEL: Record<TunnelState, string> = { stopped: 'Stopped', starting: 'Starting', running: 'Running', error: 'Error' }

export type AccountLookup = 'connected' | 'invalid' | 'missing' | 'unknown'

/** Whether an account for `provider` is usable. `accounts` null = the list is unavailable (unknown, not missing). */
export function accountState(accounts: ConnectedAccount[] | null | undefined, provider: string): AccountLookup {
  if (!accounts) return 'unknown'
  const mine = accounts.filter((a) => a.provider === provider && a.status !== 'revoked')
  if (mine.some((a) => a.status === 'connected')) return 'connected'
  return mine.length ? 'invalid' : 'missing'
}

export function accountFor(accounts: ConnectedAccount[] | null | undefined, provider: string): ConnectedAccount | null {
  return accounts?.find((a) => a.provider === provider && a.status === 'connected') ?? accounts?.find((a) => a.provider === provider && a.status !== 'revoked') ?? null
}

/** The sign-in rules a hostname must satisfy to be sent to the server (it validates again). */
export function validateHostname(value: string): string | null {
  const v = value.trim().toLowerCase()
  if (!v) return 'Enter the hostname to serve, e.g. app.example.com'
  if (v.length > 253 || !/^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/.test(v)) return 'Enter a hostname like app.example.com (no https://, no path)'
  return null
}

export interface TunnelControls {
  /** Which button leads: nothing to do while starting. */
  primary: 'start' | 'retry' | 'stop' | 'none'
  canStart: boolean
  /** Why starting is not possible right now (null when it is). */
  startBlockedReason: string | null
  canStop: boolean
  /** `cloudflared` is missing: show install instructions (and the download offer) instead of a start button. */
  needsInstall: boolean
}

/**
 * The tunnel state machine, as pure data.
 *   stopped  -> start
 *   starting -> stop (cancel); start is unavailable
 *   running  -> stop; start is unavailable
 *   error    -> retry (start again) or stop
 * Starting is further gated by public-exposure rules (password), the binary, the provider's connected account and,
 * for a named tunnel or Funnel, a hostname.
 */
export function tunnelControls(
  status: ReachabilityStatus,
  opts: { provider: TunnelProviderKind; hostname: string; accounts: ConnectedAccount[] | null | undefined },
): TunnelControls {
  const t = status.tunnel
  const busy = t.state === 'starting' || t.state === 'running'
  const needsBinary = opts.provider !== 'tailscale_funnel'
  const needsInstall = needsBinary && t.binary !== null && !t.binary.found
  const option = t.providers.find((p) => p.kind === opts.provider)
  let reason: string | null = null
  if (busy) reason = t.state === 'starting' ? 'The tunnel is starting' : 'The tunnel is already running'
  else if (blockedReason(status)) reason = blockedReason(status)
  else if (needsInstall) reason = `${t.binary?.name ?? 'cloudflared'} is not installed`
  else if (option && !option.available) reason = option.unavailable_reason ?? 'This provider is not available on this device'
  else {
    const needs = option?.requires_account ?? (opts.provider === 'cloudflare_named' ? 'cloudflare' : opts.provider === 'tailscale_funnel' ? 'tailscale' : null)
    if (needs && accountState(opts.accounts, needs) === 'missing') reason = `Connect a ${needs[0]!.toUpperCase()}${needs.slice(1)} account first`
    else if (needs && accountState(opts.accounts, needs) === 'invalid') reason = `The connected ${needs[0]!.toUpperCase()}${needs.slice(1)} account is not working. Test or reconnect it.`
    else if (opts.provider !== 'cloudflare_quick' && validateHostname(opts.hostname)) reason = validateHostname(opts.hostname)
  }
  const canStart = reason === null
  return {
    primary: t.state === 'starting' ? 'none' : t.state === 'running' ? 'stop' : t.state === 'error' ? 'retry' : 'start',
    canStart,
    startBlockedReason: canStart ? null : reason,
    canStop: busy || t.state === 'error',
    needsInstall,
  }
}

/** The body for `POST /reachability/tunnel/start`: a hostname only goes with the providers that serve one. */
export function tunnelStartBody(provider: TunnelProviderKind, hostname: string): TunnelConfig {
  return provider === 'cloudflare_quick' ? { provider } : { provider, hostname: hostname.trim().toLowerCase() }
}

/* ------------------------------------ the checklist ------------------------------------ */

export type ChecklistStatus = 'done' | 'todo' | 'attention' | 'unknown'

export interface ChecklistStep {
  id: 'password' | 'cloudflare' | 'github' | 'vercel' | 'email'
  title: string
  description: string
  /** What the AI Co-founder does on its own once this is done. */
  after: string
  status: ChecklistStatus
  optional: boolean
  /** The accounts that complete this step (any one of them). */
  providers: ConnectedProvider[]
  action: { kind: 'connect'; provider: ConnectedProvider } | { kind: 'panel'; panel: 'account' | 'integrations' }
}

export const EMAIL_PROVIDERS: ConnectedProvider[] = ['agentmail', 'resend', 'gmail', 'outlook']

function connectStatus(accounts: ConnectedAccount[] | null | undefined, providers: ConnectedProvider[]): ChecklistStatus {
  const states = providers.map((p) => accountState(accounts, p))
  if (states.includes('connected')) return 'done'
  if (states.includes('invalid')) return 'attention'
  if (states.every((s) => s === 'unknown')) return 'unknown'
  return 'todo'
}

/** "Your one-time steps": derived from what is really connected, nothing is ticked by hand. */
export function buildChecklist(input: { reachability: ReachabilityStatus | null; accounts: ConnectedAccount[] | null | undefined; email?: EmailIdentity | null }): ChecklistStep[] {
  const { reachability, accounts } = input
  const passwordOk = reachability ? blockedReason(reachability) === null : null
  const steps: ChecklistStep[] = [
    {
      id: 'password',
      title: 'Protect sign-in with a strong password',
      description: 'Required before this device can be reached from your network or the internet.',
      after: 'Unlocks LAN access and the public URL. Two-step verification is recommended as well.',
      status: passwordOk === null ? 'unknown' : passwordOk ? 'done' : 'todo',
      optional: false,
      providers: [],
      action: { kind: 'panel', panel: 'account' },
    },
    {
      id: 'cloudflare',
      title: 'Connect Cloudflare',
      description: 'For a stable public address, DNS records and company email routing.',
      after: 'Starts and watches the tunnel, creates the DNS records for your domain and routes incoming mail.',
      status: connectStatus(accounts, ['cloudflare']),
      optional: false,
      providers: ['cloudflare'],
      action: { kind: 'connect', provider: 'cloudflare' },
    },
    {
      id: 'github',
      title: 'Connect GitHub',
      description: 'For a free subdomain from a registry that takes a pull request.',
      after: 'Prepares the registration file and opens the pull request under your name, after you approve its exact content. Then it points DNS at the tunnel.',
      status: connectStatus(accounts, ['github']),
      optional: false,
      providers: ['github'],
      action: { kind: 'connect', provider: 'github' },
    },
    {
      id: 'vercel',
      title: 'Connect Vercel',
      description: 'Optional. Host the control UI on Vercel while the data stays on this device.',
      after: 'Deploys the web UI to Vercel with a rewrite to your public URL and redeploys it when that changes (an approval is asked first).',
      status: connectStatus(accounts, ['vercel']),
      optional: true,
      providers: ['vercel'],
      action: { kind: 'connect', provider: 'vercel' },
    },
    {
      id: 'email',
      title: 'Connect an email provider',
      description: 'AgentMail, Resend (with Cloudflare), or an existing Gmail or Outlook account.',
      after: 'Creates company@, cofounder@ and a mailbox for each agent that needs one, and keeps them healthy.',
      status: connectStatus(accounts, EMAIL_PROVIDERS),
      optional: false,
      providers: EMAIL_PROVIDERS,
      action: { kind: 'connect', provider: 'agentmail' },
    },
  ]
  return steps
}

export interface ChecklistProgress {
  done: number
  total: number
  /** Every required step is done. */
  complete: boolean
}

export function checklistProgress(steps: ChecklistStep[]): ChecklistProgress {
  const required = steps.filter((s) => !s.optional)
  const done = required.filter((s) => s.status === 'done').length
  return { done, total: required.length, complete: done === required.length }
}

export const CHECKLIST_STATUS_LABEL: Record<ChecklistStatus, string> = { done: 'Done', todo: 'To do', attention: 'Needs attention', unknown: 'Checking' }

/* ------------------------------------ QR codes ------------------------------------ */

/** Whether a QR code can be drawn for this URL (http/https and small enough to encode). Never throws. */
export function canRenderQr(url: string | null | undefined): url is string {
  if (!url || url.length > 512) return false
  try {
    const u = new URL(url)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
    qrMatrix(url)
    return true
  } catch {
    return false
  }
}

/* ------------------------------------ first-run card ------------------------------------ */

const dismissKey = (cid: string) => `company-os.reach-card-dismissed.${cid}`

export function isReachCardDismissed(cid: string): boolean {
  try {
    return localStorage.getItem(dismissKey(cid)) === '1'
  } catch {
    return false
  }
}

export function dismissReachCard(cid: string): void {
  try {
    localStorage.setItem(dismissKey(cid), '1')
  } catch {
    /* storage unavailable: the card returns next visit */
  }
}
