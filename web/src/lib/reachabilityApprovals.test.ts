import { describe, expect, it } from 'vitest'
import { readReachabilityScope, reachabilityActionOf } from './reachabilityApprovals'

const base = { amount_micros: null, recurrence: null, vendor: null, scope: undefined as unknown, action: null as string | null }

describe('M8 approval scopes', () => {
  it('ignores approvals that are not reachability actions', () => {
    expect(readReachabilityScope({ ...base, action: 'task.create' })).toBeNull()
    expect(readReachabilityScope(base)).toBeNull()
  })
  it('reads the registration pull request with the exact file', () => {
    const s = readReachabilityScope({ ...base, action: 'domain.register_pr', scope: { fqdn: 'acme.is-a.dev', repo: 'is-a-dev/register', file_path: 'domains/acme.json', file_content: '{"a":1}', pr_title: 'Add acme', pr_body: 'Please add', publishes_as: 'sam' } })!
    expect(s.kind).toBe('domain_registration_pr')
    expect(s.file).toEqual({ path: 'domains/acme.json', content: '{"a":1}' })
    expect(s.prBody).toBe('Please add')
    expect(s.rows).toContainEqual({ label: 'Published as', value: 'sam' })
  })
  it('reads a purchase with its price and ceiling', () => {
    const s = readReachabilityScope({ ...base, action: 'domain.purchase', amount_micros: 12_000_000, scope: { domain: 'acme.dev', registrar: 'porkbun', years: 1, max_price_micros: 12_000_000 } })!
    expect(s.rows).toContainEqual({ label: 'Domain', value: 'acme.dev' })
    expect(s.rows.find((r) => r.label === 'Price')!.value).toContain('$12')
    expect(s.rows.find((r) => r.label === 'Term')!.value).toBe('1 year')
    expect(s.effect).toMatch(/real charge/)
  })
  it('reads DNS changes and drops malformed ones', () => {
    const s = readReachabilityScope({ ...base, action: 'dns.change', scope: { zone: 'acme.dev', changes: [{ op: 'create', type: 'CNAME', name: 'app', value: 'x.trycloudflare.com', ttl: 300 }, { junk: true }] } })!
    expect(s.dns).toEqual([{ op: 'create', type: 'CNAME', name: 'app', value: 'x.trycloudflare.com', ttl: 300 }])
  })
  it('reads mailbox creation and the hosted deploy, and finds the action in the scope too', () => {
    expect(readReachabilityScope({ ...base, action: 'mailbox.create', scope: { address: 'cofounder@acme.dev', provider: 'agentmail' } })!.rows).toContainEqual({ label: 'Address', value: 'cofounder@acme.dev' })
    const d = readReachabilityScope({ ...base, scope: { action: 'hosted_ui.deploy', project: 'acme-ui', proxy_target: 'https://x.trycloudflare.com' } })!
    expect(d.kind).toBe('hosted_ui_deploy')
    expect(d.rows).toContainEqual({ label: '/api/* is forwarded to', value: 'https://x.trycloudflare.com' })
    expect(reachabilityActionOf({ action: 'unknown.thing', scope: null })).toBeNull()
  })
})
