import type { ConnectedAccount, ReachabilityStatus } from '@/api/types'

/** A reachability payload as the (assumed) M8 backend would send it; override per test. */
export function makeReach(over: Omit<Partial<ReachabilityStatus>, 'tunnel' | 'lan'> & { tunnel?: Partial<ReachabilityStatus['tunnel']>; lan?: Partial<ReachabilityStatus['lan']> } = {}): ReachabilityStatus {
  const { tunnel, lan, ...rest } = over
  return {
    mode: 'local',
    device_name: 'Sam’s MacBook',
    hostname: 'sams-macbook',
    auth_mode: 'password',
    local_urls: ['http://127.0.0.1:8700'],
    lan: { enabled: false, mdns_name: 'sams-macbook.local', mdns_url: 'http://sams-macbook.local:8700', addresses: [{ address: '192.168.1.20', family: 'ipv4', interface: 'en0', url: 'http://192.168.1.20:8700' }], ...lan },
    tunnel: {
      provider: null,
      state: 'stopped',
      url: null,
      hostname: null,
      last_health_check_at: null,
      healthy: null,
      restarts: 0,
      started_at: null,
      error: null,
      ephemeral: false,
      last_url: null,
      url_changed_at: null,
      binary: { name: 'cloudflared', found: true, version: '2026.1.0', path: '/usr/local/bin/cloudflared', install_instructions: [], download_offered: false, download_url: null, download_sha256: null, download_size_bytes: null },
      providers: [
        { kind: 'cloudflare_quick', requires_account: null, available: true, unavailable_reason: null },
        { kind: 'cloudflare_named', requires_account: 'cloudflare', available: true, unavailable_reason: null },
        { kind: 'tailscale_funnel', requires_account: 'tailscale', available: true, unavailable_reason: null },
      ],
      ...tunnel,
    },
    active_url: 'http://127.0.0.1:8700',
    public_exposure_blocked_reason: null,
    public_exposure_blocked_code: null,
    domain: null,
    ...rest,
  }
}

export function makeAccount(over: Partial<ConnectedAccount> = {}): ConnectedAccount {
  return { id: 'ca1', company_id: 'c1', provider: 'cloudflare', label: 'Cloudflare', credential_id: 'cr1', scopes: ['dns:edit'], status: 'connected', last_checked_at: '2026-10-24T10:00:00Z', metadata: {}, created_at: '2026-10-24T09:00:00Z', ...over }
}
