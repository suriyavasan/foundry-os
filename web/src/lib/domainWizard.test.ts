import { describe, expect, it } from 'vitest'
import type { DomainAvailability, DomainRegistry, FreeDomainProvider } from '@/api/types'
import { choosePath, createsApproval, fqdnFor, initialWizard, isLastStep, nextStep, prevStep, previewRequest, registrationRequest, stepBlocker, stepsFor, submitLabel, validateDomain, validateLabel } from './domainWizard'

const registry: DomainRegistry = { id: 'is-a-dev', name: 'is-a.dev', suffix: 'is-a.dev', repo: 'is-a-dev/register', rules_url: null, eligibility: ['Personal or open-source projects'], review: 'human', name_min_length: 3, name_max_length: 20, name_pattern: '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$' }
const provider: FreeDomainProvider = { id: 'dp', name: 'DigitalPlat', dashboard_url: 'https://dash.example', suffixes: ['dpdns.org'], eligibility: [], steps: ['Sign up'] }
const ctx = { registries: [registry], providers: [provider] }
const quote = (over: Partial<DomainAvailability> = {}): DomainAvailability => ({ domain: 'acme.dev', available: true, registrar: 'porkbun', price_micros: 12_000_000, renewal_price_micros: 15_000_000, ...over })

describe('wizard steps per path', () => {
  it('has distinct step lists for the three paths', () => {
    expect(stepsFor(null)).toEqual(['path'])
    expect(stepsFor('github_registry')).toEqual(['path', 'registry', 'name', 'preview'])
    expect(stepsFor('free_provider')).toEqual(['path', 'provider', 'domain'])
    expect(stepsFor('paid_registrar')).toEqual(['path', 'search', 'confirm'])
  })
  it('walks forward and back and knows the last step', () => {
    const s = choosePath('paid_registrar')
    expect(nextStep(s, 'path')).toBe('search')
    expect(prevStep(s, 'confirm')).toBe('search')
    expect(prevStep(s, 'path')).toBeNull()
    expect(isLastStep(s, 'confirm')).toBe(true)
    expect(isLastStep(s, 'search')).toBe(false)
  })
  it('choosing another path resets what belonged to the old one', () => {
    expect(choosePath('free_provider')).toEqual({ ...initialWizard(), path: 'free_provider' })
  })
  it('only the GitHub and purchase paths create an approval', () => {
    expect(createsApproval('github_registry')).toBe(true)
    expect(createsApproval('paid_registrar')).toBe(true)
    expect(createsApproval('free_provider')).toBe(false)
    expect(submitLabel('paid_registrar')).toBe('Request purchase')
  })
})

describe('step blockers', () => {
  it('needs a path, then path-specific input', () => {
    expect(stepBlocker('path', initialWizard(), ctx)).not.toBeNull()
    expect(stepBlocker('path', choosePath('github_registry'), ctx)).toBeNull()
    const s = { ...choosePath('github_registry'), registryId: 'is-a-dev' }
    expect(stepBlocker('registry', s, ctx)).toBeNull()
    expect(stepBlocker('name', { ...s, name: 'a' }, ctx)).toMatch(/at least 3/)
    expect(stepBlocker('name', { ...s, name: 'Bad_Name' }, ctx)).not.toBeNull()
    expect(stepBlocker('name', { ...s, name: 'acme' }, ctx)).toBeNull()
  })
  it('the assisted path checks the provider’s suffix', () => {
    const s = { ...choosePath('free_provider'), providerId: 'dp' }
    expect(stepBlocker('domain', { ...s, domain: 'acme.com' }, ctx)).toMatch(/\.dpdns\.org/)
    expect(stepBlocker('domain', { ...s, domain: 'acme.dpdns.org' }, ctx)).toBeNull()
  })
  it('a purchase needs an available domain with a quoted price', () => {
    const s = choosePath('paid_registrar')
    expect(stepBlocker('confirm', s, ctx)).toBeTruthy()
    expect(stepBlocker('confirm', { ...s, selected: quote({ available: false }) }, ctx)).toMatch(/taken/)
    expect(stepBlocker('confirm', { ...s, selected: quote({ price_micros: null }) }, ctx)).toMatch(/did not quote/)
    expect(stepBlocker('confirm', { ...s, selected: quote() }, ctx)).toBeNull()
  })
  it('validates labels and domains', () => {
    expect(validateLabel('', registry)).not.toBeNull()
    expect(validateLabel('a'.repeat(21), registry)).toMatch(/at most 20/)
    expect(validateDomain('example.com')).toBeNull()
    expect(validateDomain('https://example.com')).not.toBeNull()
    expect(fqdnFor(registry, ' Acme ')).toBe('acme.is-a.dev')
    expect(fqdnFor(null, 'acme')).toBe('')
  })
})

describe('the request that creates the approval', () => {
  it('registry path: preview and registration carry the lower-cased name', () => {
    const s = { ...choosePath('github_registry'), registryId: 'is-a-dev', name: ' Acme ' }
    expect(previewRequest(s)).toEqual({ registry_id: 'is-a-dev', name: 'acme' })
    expect(registrationRequest(s)).toEqual({ path: 'github_registry', registry_id: 'is-a-dev', name: 'acme' })
  })
  it('assisted path: provider and domain', () => {
    expect(registrationRequest({ ...choosePath('free_provider'), providerId: 'dp', domain: 'Acme.dpdns.org' })).toEqual({ path: 'free_provider', provider_id: 'dp', domain: 'acme.dpdns.org' })
  })
  it('purchase: binds the approval to the quoted price, one year', () => {
    expect(registrationRequest({ ...choosePath('paid_registrar'), selected: quote() })).toEqual({ path: 'paid_registrar', registrar: 'porkbun', domain: 'acme.dev', years: 1, max_price_micros: 12_000_000 })
  })
  it('never builds a request that is not ready', () => {
    expect(registrationRequest(initialWizard())).toBeNull()
    expect(registrationRequest({ ...choosePath('paid_registrar'), selected: quote({ price_micros: null }) })).toBeNull()
    expect(registrationRequest({ ...choosePath('github_registry'), registryId: 'x', name: '' })).toBeNull()
    expect(previewRequest(choosePath('free_provider'))).toBeNull()
  })
})
