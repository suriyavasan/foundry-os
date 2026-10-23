import { describe, expect, it } from 'vitest'
import { makeAccount } from '@/test/m8'
import { EMAIL_PROVIDER_INFO, emailProviderInfo, emailProviderReadiness, sortMailboxes, validateLocalPart } from './email'

describe('email provider readiness', () => {
  const resend = emailProviderInfo('cloudflare_resend')!
  it('needs every account of the provider', () => {
    expect(emailProviderReadiness(resend, [makeAccount()])).toEqual({ ready: false, missing: ['resend'], failing: [] })
    expect(emailProviderReadiness(resend, [makeAccount(), makeAccount({ id: 'r', provider: 'resend', status: 'invalid' })])).toEqual({ ready: false, missing: [], failing: ['resend'] })
    expect(emailProviderReadiness(resend, [makeAccount(), makeAccount({ id: 'r', provider: 'resend' })]).ready).toBe(true)
  })
  it('is never ready while the account list is unavailable', () => {
    expect(emailProviderReadiness(EMAIL_PROVIDER_INFO[0]!, null).ready).toBe(false)
  })
})

describe('mailbox rules', () => {
  it('validates the part before the @', () => {
    expect(validateLocalPart('support')).toBeNull()
    expect(validateLocalPart('a.b-c_d')).toBeNull()
    expect(validateLocalPart('')).not.toBeNull()
    expect(validateLocalPart('x@y')).toMatch(/before the @/)
    expect(validateLocalPart('-bad')).not.toBeNull()
  })
  it('lists company@ and cofounder@ first', () => {
    const mk = (address: string) => ({ id: address, address }) as never
    expect(sortMailboxes([mk('zed@a.dev'), mk('cofounder@a.dev'), mk('company@a.dev')]).map((m) => (m as { address: string }).address)).toEqual(['company@a.dev', 'cofounder@a.dev', 'zed@a.dev'])
  })
})
