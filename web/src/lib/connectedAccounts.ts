import type { ConnectedAccount, ConnectedAccountCreate, ConnectedAccountStatus, ConnectedProvider, CreateCredentialRequest, ProviderCheck } from '@/api/types'
import type { Tone } from '@/components/ui/Badge'

/**
 * Connected accounts (M8 §1). The Founder connects each provider once; the AI Co-founder then operates it within the
 * guardrails. Pure helpers: the provider catalog (which fields each provider needs), the form rules, the request
 * bodies and the secret scrubbing.
 *
 * Secrets: a token lives in the form state until submit, goes only into `toSecretCredentialRequests`, is emptied the
 * moment the server has accepted it, and never reaches a response type, a toast or an error message
 * (`scrubSecrets`). Company OS never creates accounts on these services.
 */

export const BOUNDARY_COPY = 'Company OS never creates accounts for you on these services — you connect an account once; the AI Co-founder then operates it within your guardrails.'

export interface ProviderField {
  key: string
  label: string
  /** A secret goes to the encrypted credential store and is write-only; the rest is plain account metadata. */
  secret: boolean
  required: boolean
  placeholder?: string
  hint?: string
}

export interface ProviderDef {
  id: ConnectedProvider
  label: string
  /** What the AI Co-founder uses the account for. */
  purpose: string
  auth: 'token' | 'oauth'
  fields: ProviderField[]
  /** Where the Founder creates the token (opened in a new tab by the Founder, never by the app). */
  tokenUrl?: string
  tokenHelp: string
}

export const PROVIDERS: readonly ProviderDef[] = [
  {
    id: 'cloudflare',
    label: 'Cloudflare',
    purpose: 'Tunnel, DNS records and email routing',
    auth: 'token',
    tokenUrl: 'https://dash.cloudflare.com/profile/api-tokens',
    tokenHelp: 'Create an API token limited to the zones and permissions you want the AI Co-founder to use (DNS edit, Tunnel edit).',
    fields: [
      { key: 'token', label: 'API token', secret: true, required: true, placeholder: 'paste the token' },
      { key: 'account_id', label: 'Account ID', secret: false, required: false, hint: 'Optional. Needed for tunnels when the token spans several accounts.' },
    ],
  },
  {
    id: 'vercel',
    label: 'Vercel',
    purpose: 'Host the control UI',
    auth: 'token',
    tokenUrl: 'https://vercel.com/account/tokens',
    tokenHelp: 'Create a token scoped to the team that should hold the project.',
    fields: [
      { key: 'token', label: 'Access token', secret: true, required: true, placeholder: 'paste the token' },
      { key: 'team_id', label: 'Team ID', secret: false, required: false, hint: 'Optional. Leave blank for a personal account.' },
    ],
  },
  {
    id: 'github',
    label: 'GitHub',
    purpose: 'Registration pull requests for free subdomains',
    auth: 'token',
    tokenUrl: 'https://github.com/settings/tokens',
    tokenHelp: 'A fine-grained token with access to public repositories. Pull requests are published under this account, only after you approve their content.',
    fields: [{ key: 'token', label: 'Personal access token', secret: true, required: true, placeholder: 'paste the token' }],
  },
  {
    id: 'agentmail',
    label: 'AgentMail',
    purpose: 'Programmatic inboxes for the company and each agent',
    auth: 'token',
    tokenHelp: 'Create an API key in your AgentMail account.',
    fields: [{ key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'paste the key' }],
  },
  {
    id: 'resend',
    label: 'Resend',
    purpose: 'Sending email from your own domain',
    auth: 'token',
    tokenUrl: 'https://resend.com/api-keys',
    tokenHelp: 'Create an API key with sending access for your domain.',
    fields: [{ key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'paste the key' }],
  },
  {
    id: 'tailscale',
    label: 'Tailscale',
    purpose: 'Funnel: a public address without opening ports',
    auth: 'token',
    tokenUrl: 'https://login.tailscale.com/admin/settings/keys',
    tokenHelp: 'Create an API access token. Funnel must be allowed in your tailnet policy.',
    fields: [
      { key: 'api_key', label: 'API access token', secret: true, required: true, placeholder: 'paste the token' },
      { key: 'tailnet', label: 'Tailnet', secret: false, required: false, hint: 'Optional, e.g. example.com or your-name.ts.net.' },
    ],
  },
  {
    id: 'porkbun',
    label: 'Porkbun',
    purpose: 'Search and register domains (each purchase needs your approval)',
    auth: 'token',
    tokenUrl: 'https://porkbun.com/account/api',
    tokenHelp: 'Enable API access on the account, then create an API key. Porkbun issues a key and a secret key; both are stored encrypted.',
    fields: [
      { key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'pk1_…' },
      { key: 'secret_key', label: 'Secret API key', secret: true, required: true, placeholder: 'sk1_…' },
    ],
  },
  {
    id: 'namecheap',
    label: 'Namecheap',
    purpose: 'Search and register domains (each purchase needs your approval)',
    auth: 'token',
    tokenUrl: 'https://ap.www.namecheap.com/settings/tools/apiaccess/',
    tokenHelp: 'Enable API access and allow-list this machine’s public IP address.',
    fields: [
      { key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'paste the key' },
      { key: 'api_user', label: 'API user', secret: false, required: true },
      { key: 'client_ip', label: 'Allow-listed IPv4 address', secret: false, required: true, placeholder: '203.0.113.7' },
    ],
  },
  {
    id: 'gmail',
    label: 'Gmail',
    purpose: 'Use an existing Gmail account as the company mailbox',
    auth: 'oauth',
    tokenHelp: 'You sign in with Google in a new tab and grant access. Company OS does not see your password.',
    fields: [],
  },
  {
    id: 'outlook',
    label: 'Outlook',
    purpose: 'Use an existing Outlook account as the company mailbox',
    auth: 'oauth',
    tokenHelp: 'You sign in with Microsoft in a new tab and grant access. Company OS does not see your password.',
    fields: [],
  },
] as const

export const TOKEN_PROVIDER_IDS: ConnectedProvider[] = PROVIDERS.filter((p) => p.auth === 'token').map((p) => p.id)

export function providerDef(id: string): ProviderDef | null {
  return PROVIDERS.find((p) => p.id === id) ?? null
}
export function providerLabel(id: string): string {
  return providerDef(id)?.label ?? id
}

/* ------------------------------------ the form ------------------------------------ */

export interface ConnectFormValues {
  label: string
  /** Field key -> value, secrets and plain fields alike. Secrets are write-only. */
  fields: Record<string, string>
}
export type ConnectFormErrors = Record<string, string>

export function emptyConnectForm(def: ProviderDef): ConnectFormValues {
  return { label: '', fields: Object.fromEntries(def.fields.map((f) => [f.key, ''])) }
}

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/

/** Errors keyed by field key, or `label`. Empty object = valid. */
export function validateConnectForm(def: ProviderDef, v: ConnectFormValues, opts: { savedKeys?: readonly string[] } = {}): ConnectFormErrors {
  const e: ConnectFormErrors = {}
  const label = v.label.trim()
  if (label.length > 80) e.label = 'At most 80 characters'
  for (const f of def.fields) {
    const raw = v.fields[f.key] ?? ''
    const value = raw.trim()
    if (f.secret) {
      if (opts.savedKeys?.includes(f.key)) continue
      if (!value) {
        if (f.required) e[f.key] = `${f.label} is required`
      } else if (/\s/.test(value)) e[f.key] = `${f.label} has no spaces or line breaks; check what you pasted`
      continue
    }
    if (!value) {
      if (f.required) e[f.key] = `${f.label} is required`
    } else if (value.length > 120) e[f.key] = 'At most 120 characters'
    else if (f.key === 'client_ip' && !IPV4.test(value)) e[f.key] = 'Enter an IPv4 address like 203.0.113.7'
  }
  return e
}

/** The label to save: what was typed, else the provider's name. */
export function accountLabel(def: ProviderDef, v: ConnectFormValues): string {
  return v.label.trim() || def.label
}

export interface SecretRequest {
  key: string
  body: CreateCredentialRequest
}

/** One credential request per secret field that has a value. This is the only place a secret is read. */
export function toSecretCredentialRequests(def: ProviderDef, v: ConnectFormValues): SecretRequest[] {
  const out: SecretRequest[] = []
  for (const f of def.fields) {
    if (!f.secret) continue
    const value = (v.fields[f.key] ?? '').trim()
    if (!value) continue
    out.push({ key: f.key, body: { name: `${def.id}-${f.key.replace(/_/g, '-')}`, kind: 'token', value } })
  }
  return out
}

/** The connected-account body. It references credentials by id and carries only plain metadata. */
export function toConnectedAccountBody(def: ProviderDef, v: ConnectFormValues, credentialIds: Record<string, string>): ConnectedAccountCreate {
  const secretKeys = def.fields.filter((f) => f.secret).map((f) => f.key)
  const [primary, ...rest] = secretKeys
  const metadata: Record<string, string> = {}
  for (const f of def.fields) {
    if (f.secret) continue
    const value = (v.fields[f.key] ?? '').trim()
    if (value) metadata[f.key] = value
  }
  const extra: Record<string, string> = {}
  for (const k of rest) if (credentialIds[k]) extra[k] = credentialIds[k]!
  return {
    provider: def.id,
    label: accountLabel(def, v),
    credential_id: credentialIds[primary!] ?? '',
    ...(Object.keys(extra).length ? { extra_credential_ids: extra } : null),
    ...(Object.keys(metadata).length ? { metadata } : null),
  }
}

/** Every secret currently typed into the form (for scrubbing). */
export function typedSecrets(def: ProviderDef, v: ConnectFormValues): string[] {
  return def.fields.filter((f) => f.secret).map((f) => (v.fields[f.key] ?? '').trim()).filter((s) => s.length > 0)
}

/** Never let an error or a server message echo a secret back into the UI. */
export function scrubSecrets(text: string, secrets: readonly string[]): string {
  let out = text
  for (const s of secrets) {
    const t = s.trim()
    if (t.length >= 4) out = out.split(t).join('[redacted]')
  }
  return out
}

/* ------------------------------------ display ------------------------------------ */

export const ACCOUNT_STATUS_LABEL: Record<ConnectedAccountStatus, string> = { connected: 'Connected', invalid: 'Not working', revoked: 'Revoked' }
export const ACCOUNT_STATUS_TONE: Record<ConnectedAccountStatus, Tone> = { connected: 'success', invalid: 'danger', revoked: 'neutral' }

/** Live accounts first (connected, then not working), revoked last; then by provider order in the catalog. */
export function sortAccounts(accounts: ConnectedAccount[]): ConnectedAccount[] {
  const rank = (a: ConnectedAccount) => (a.status === 'connected' ? 0 : a.status === 'invalid' ? 1 : 2)
  const order = (id: string) => {
    const i = PROVIDERS.findIndex((p) => p.id === id)
    return i === -1 ? 99 : i
  }
  return [...accounts].sort((a, b) => rank(a) - rank(b) || order(a.provider) - order(b.provider) || a.label.localeCompare(b.label))
}

/** Providers that have no live account yet: what "Connect" can still add. */
export function unconnectedProviders(accounts: ConnectedAccount[]): ProviderDef[] {
  const live = new Set(accounts.filter((a) => a.status !== 'revoked').map((a) => a.provider))
  return PROVIDERS.filter((p) => !live.has(p.id))
}

export function describeCheck(check: ProviderCheck): { ok: boolean; text: string } {
  const text = check.message?.trim() || (check.ok ? 'The provider accepted the credentials.' : 'The provider rejected the credentials.')
  return { ok: check.ok, text }
}

/** Only an https URL from the server is ever opened for sign-in. */
export function isSafeAuthorizeUrl(url: unknown): url is string {
  if (typeof url !== 'string') return false
  try {
    return new URL(url).protocol === 'https:'
  } catch {
    return false
  }
}
