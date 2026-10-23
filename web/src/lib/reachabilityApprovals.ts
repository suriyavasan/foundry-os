import type { Approval } from '@/api/types'
import { formatUsd } from './money'

/**
 * The M8 approvals (docs/specs/m8 + m6 tiers identity / external_commitment / spend). Each carries an `action` and the
 * exact scope the Founder is authorising. This reads that scope defensively into labelled rows (plain text only):
 * nothing is invented, a field the server did not send is simply not shown.
 *
 * Assumed action names: domain.register_pr, domain.purchase, dns.change, mailbox.create, hosted_ui.deploy.
 */

export type ReachabilityActionKind = 'domain_registration_pr' | 'domain_purchase' | 'dns_change' | 'mailbox_create' | 'hosted_ui_deploy'

export interface ScopeRow {
  label: string
  value: string
}

export interface ScopeFile {
  path: string
  content: string
}

export interface DnsChangeRow {
  op: string
  type: string
  name: string
  value: string | null
  ttl: number | null
}

export interface ReachabilityScope {
  kind: ReachabilityActionKind
  heading: string
  /** One line saying what happens if approved. */
  effect: string
  rows: ScopeRow[]
  /** The exact registration file of a pull request. */
  file: ScopeFile | null
  /** The exact pull request text. */
  prBody: string | null
  dns: DnsChangeRow[]
}

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

const ACTIONS: Record<string, ReachabilityActionKind> = {
  'domain.register_pr': 'domain_registration_pr',
  'domain.register': 'domain_registration_pr',
  'domain.purchase': 'domain_purchase',
  'dns.change': 'dns_change',
  'dns.update': 'dns_change',
  'dns.records': 'dns_change',
  'mailbox.create': 'mailbox_create',
  'email.create_mailbox': 'mailbox_create',
  'hosted_ui.deploy': 'hosted_ui_deploy',
  'vercel.deploy': 'hosted_ui_deploy',
}

/** Which M8 action an approval authorises, or null for any other approval. */
export function reachabilityActionOf(a: Pick<Approval, 'action' | 'scope'>): ReachabilityActionKind | null {
  const scope = obj(a.scope)
  const name = a.action ?? str(scope?.action) ?? str(scope?.kind)
  return name ? (ACTIONS[name] ?? null) : null
}

const add = (rows: ScopeRow[], label: string, value: string | null) => {
  if (value) rows.push({ label, value })
}

export function readReachabilityScope(a: Pick<Approval, 'action' | 'scope' | 'amount_micros' | 'recurrence' | 'vendor'>): ReachabilityScope | null {
  const kind = reachabilityActionOf(a)
  if (!kind) return null
  const s = obj(a.scope) ?? {}
  const rows: ScopeRow[] = []
  let file: ScopeFile | null = null
  let prBody: string | null = null
  const dns: DnsChangeRow[] = []

  switch (kind) {
    case 'domain_registration_pr': {
      add(rows, 'Domain', str(s.fqdn) ?? str(s.domain))
      add(rows, 'Registry repository', str(s.repo))
      add(rows, 'Branch', str(s.branch))
      add(rows, 'Pull request title', str(s.pr_title))
      add(rows, 'Published as', str(s.publishes_as) ?? str(s.github_login))
      const path = str(s.file_path)
      const content = str(s.file_content)
      if (path && content) file = { path, content }
      prBody = str(s.pr_body)
      return {
        kind,
        heading: 'Pull request that will be opened',
        effect: 'Opens this pull request under your GitHub identity and accepts the registry’s terms.',
        rows,
        file,
        prBody,
        dns,
      }
    }
    case 'domain_purchase': {
      add(rows, 'Domain', str(s.domain))
      add(rows, 'Registrar', str(s.registrar) ?? a.vendor)
      const years = num(s.years)
      add(rows, 'Term', years ? `${years} year${years === 1 ? '' : 's'}` : null)
      const price = num(s.price_micros) ?? a.amount_micros
      add(rows, 'Price', price != null ? `${formatUsd(price)} (quoted by the registrar)` : null)
      const max = num(s.max_price_micros)
      add(rows, 'Most it may charge', max != null ? formatUsd(max) : null)
      add(rows, 'Renews at', num(s.renewal_price_micros) != null ? `${formatUsd(num(s.renewal_price_micros)!)} / year` : null)
      return { kind, heading: 'Domain purchase', effect: 'Buys this domain with the connected registrar account: a real charge.', rows, file, prBody, dns }
    }
    case 'dns_change': {
      add(rows, 'Zone', str(s.zone) ?? str(s.domain))
      add(rows, 'DNS provider', str(s.provider))
      const changes = Array.isArray(s.changes) ? s.changes : Array.isArray(s.records) ? s.records : []
      for (const c of changes) {
        const o = obj(c)
        const type = str(o?.type)
        const name = str(o?.name)
        if (!o || !type || !name) continue
        dns.push({ op: str(o.op) ?? str(o.action) ?? 'set', type, name, value: str(o.value) ?? str(o.content), ttl: num(o.ttl) })
      }
      return { kind, heading: 'DNS changes', effect: 'Changes where your domain points. Visitors and email for the domain follow these records.', rows, file, prBody, dns }
    }
    case 'mailbox_create': {
      add(rows, 'Address', str(s.address))
      add(rows, 'Owner', str(s.owner_name) ?? str(s.owner_agent_name) ?? str(s.owner_agent_id))
      add(rows, 'Provider', str(s.provider))
      return { kind, heading: 'New mailbox', effect: 'Creates this mailbox. Inbound mail is treated as untrusted input; outbound mail goes through review and carries the AI disclosure.', rows, file, prBody, dns }
    }
    case 'hosted_ui_deploy': {
      add(rows, 'Host', str(s.provider) ?? 'vercel')
      add(rows, 'Project', str(s.project) ?? str(s.project_name))
      add(rows, '/api/* is forwarded to', str(s.proxy_target))
      add(rows, 'Result URL', str(s.url))
      return { kind, heading: 'Hosted control UI', effect: 'Deploys the web UI to the connected Vercel account. It talks to this device only through the public URL above.', rows, file, prBody, dns }
    }
  }
}
