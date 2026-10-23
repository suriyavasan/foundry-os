import { describe, expect, it } from 'vitest'
import { emptyConnectForm, isSafeAuthorizeUrl, providerDef, scrubSecrets, sortAccounts, toConnectedAccountBody, toSecretCredentialRequests, typedSecrets, unconnectedProviders, validateConnectForm } from './connectedAccounts'
import { makeAccount } from '@/test/m8'

const def = (id: string) => providerDef(id)!
const fill = (id: string, fields: Record<string, string>, label = '') => ({ label, fields: { ...emptyConnectForm(def(id)).fields, ...fields } })

describe('connect form rules', () => {
  it('lists all eight providers of the spec', () => {
    for (const id of ['cloudflare', 'vercel', 'github', 'agentmail', 'resend', 'tailscale', 'porkbun', 'namecheap']) expect(providerDef(id)).not.toBeNull()
  })
  it('requires the secret and rejects pasted whitespace', () => {
    expect(validateConnectForm(def('github'), fill('github', {}))).toEqual({ token: 'Personal access token is required' })
    expect(validateConnectForm(def('github'), fill('github', { token: 'abc def' })).token).toMatch(/no spaces/)
    expect(validateConnectForm(def('github'), fill('github', { token: 'ghp_abcdef' }))).toEqual({})
  })
  it('requires both Porkbun keys and Namecheap’s plain fields', () => {
    expect(Object.keys(validateConnectForm(def('porkbun'), fill('porkbun', { api_key: 'pk1_x1234' })))).toEqual(['secret_key'])
    const e = validateConnectForm(def('namecheap'), fill('namecheap', { api_key: 'abcd1234', client_ip: '999.1.1.1' }))
    expect(Object.keys(e).sort()).toEqual(['api_user', 'client_ip'])
  })
  it('does not ask again for a secret the server already holds', () => {
    expect(validateConnectForm(def('github'), fill('github', {}), { savedKeys: ['token'] })).toEqual({})
  })
})

describe('request bodies', () => {
  it('sends each secret once, to the credential store only', () => {
    const v = fill('porkbun', { api_key: ' pk1_aaaa ', secret_key: 'sk1_bbbb' })
    const reqs = toSecretCredentialRequests(def('porkbun'), v)
    expect(reqs.map((r) => [r.key, r.body.value])).toEqual([['api_key', 'pk1_aaaa'], ['secret_key', 'sk1_bbbb']])
    expect(reqs[0]!.body.name).toBe('porkbun-api-key')
  })
  it('the account body references credential ids and carries no secret', () => {
    const v = fill('porkbun', { api_key: 'pk1_aaaa', secret_key: 'sk1_bbbb' }, 'Main')
    const body = toConnectedAccountBody(def('porkbun'), v, { api_key: 'cr1', secret_key: 'cr2' })
    expect(body).toEqual({ provider: 'porkbun', label: 'Main', credential_id: 'cr1', extra_credential_ids: { secret_key: 'cr2' } })
    expect(JSON.stringify(body)).not.toMatch(/pk1_aaaa|sk1_bbbb/)
  })
  it('plain fields go to metadata, label defaults to the provider name', () => {
    const body = toConnectedAccountBody(def('cloudflare'), fill('cloudflare', { token: 'tok_12345', account_id: ' acc1 ' }), { token: 'cr9' })
    expect(body).toEqual({ provider: 'cloudflare', label: 'Cloudflare', credential_id: 'cr9', metadata: { account_id: 'acc1' } })
  })
})

describe('secret scrubbing', () => {
  it('removes every typed secret from text', () => {
    const v = fill('porkbun', { api_key: 'pk1_aaaa', secret_key: 'sk1_bbbb' })
    const secrets = typedSecrets(def('porkbun'), v)
    expect(scrubSecrets('bad key pk1_aaaa and sk1_bbbb; pk1_aaaa again', secrets)).toBe('bad key [redacted] and [redacted]; [redacted] again')
    expect(scrubSecrets('nothing here', secrets)).toBe('nothing here')
  })
})

describe('lists', () => {
  it('sorts live accounts first and lists providers that have no live account', () => {
    const list = [makeAccount({ id: 'r', provider: 'github', status: 'revoked' }), makeAccount({ id: 'c' }), makeAccount({ id: 'i', provider: 'vercel', status: 'invalid' })]
    expect(sortAccounts(list).map((a) => a.id)).toEqual(['c', 'i', 'r'])
    const names = unconnectedProviders(list).map((p) => p.id)
    expect(names).toContain('github')
    expect(names).not.toContain('cloudflare')
  })
  it('opens only https sign-in addresses', () => {
    expect(isSafeAuthorizeUrl('https://accounts.example.com/o')).toBe(true)
    expect(isSafeAuthorizeUrl('http://x')).toBe(false)
    expect(isSafeAuthorizeUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeAuthorizeUrl(undefined)).toBe(false)
  })
})
