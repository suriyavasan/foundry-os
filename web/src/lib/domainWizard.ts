import type { DomainAvailability, DomainPath, DomainRegistrationCreate, DomainRegistrationPreviewRequest, DomainRegistry, FreeDomainProvider } from '@/api/types'

/**
 * The "Get a domain" wizard (M8 §2.3) as pure data: three paths, their steps, what each step needs before "Next",
 * and the exact request that creates the registration (and with it the human approval). The UI renders this; the
 * server validates it again.
 */

export type WizardStepId = 'path' | 'registry' | 'name' | 'preview' | 'provider' | 'domain' | 'search' | 'confirm'

export interface PathInfo {
  path: DomainPath
  title: string
  summary: string
  cost: string
  /** What the Founder should know before choosing it. */
  caveats: string[]
}

export const PATH_INFO: readonly PathInfo[] = [
  {
    path: 'github_registry',
    title: 'Free subdomain from a GitHub registry',
    summary: 'A name like yourname.is-a.dev. The AI Co-founder prepares a registration file and opens a pull request; the registry reviews it.',
    cost: 'Free',
    caveats: [
      'The pull request is published under your GitHub identity, and it accepts the registry’s terms. You approve the exact content first.',
      'Many registries only accept personal or open-source projects and review requests by hand, so it can take days.',
    ],
  },
  {
    path: 'free_provider',
    title: 'Free domain from a provider with a dashboard',
    summary: 'You register the domain yourself on the provider’s site, following the exact steps and DNS records below. The AI Co-founder then checks DNS and wires it to your tunnel.',
    cost: 'Free',
    caveats: ['You create the account and accept the provider’s terms yourself. Foundry AI OS never does that for you.', 'Providers may require renewal or limit what the domain can be used for.'],
  },
  {
    path: 'paid_registrar',
    title: 'Buy a domain from a registrar',
    summary: 'Search a name at a connected registrar (Porkbun, Namecheap). A purchase is a real charge, so it waits for your approval.',
    cost: 'Paid · needs your approval',
    caveats: ['You see the quoted price before anything is requested.', 'Needs a connected registrar account. Nothing is bought without your approval.'],
  },
]

export function pathInfo(path: DomainPath): PathInfo {
  return PATH_INFO.find((p) => p.path === path)!
}

export interface WizardState {
  path: DomainPath | null
  registryId: string
  /** The label chosen under a registry's suffix. */
  name: string
  providerId: string
  /** The full domain for the assisted path. */
  domain: string
  /** The availability result chosen for purchase. */
  selected: DomainAvailability | null
}

export function initialWizard(): WizardState {
  return { path: null, registryId: '', name: '', providerId: '', domain: '', selected: null }
}

export function stepsFor(path: DomainPath | null): WizardStepId[] {
  switch (path) {
    case 'github_registry':
      return ['path', 'registry', 'name', 'preview']
    case 'free_provider':
      return ['path', 'provider', 'domain']
    case 'paid_registrar':
      return ['path', 'search', 'confirm']
    default:
      return ['path']
  }
}

export const STEP_TITLE: Record<WizardStepId, string> = {
  path: 'Choose a path',
  registry: 'Choose a registry',
  name: 'Choose a name',
  preview: 'Review what will be submitted',
  provider: 'Choose a provider',
  domain: 'Your domain',
  search: 'Find a domain',
  confirm: 'Request the purchase',
}

export function nextStep(state: WizardState, step: WizardStepId): WizardStepId | null {
  const steps = stepsFor(state.path)
  const i = steps.indexOf(step)
  return i >= 0 && i < steps.length - 1 ? steps[i + 1]! : null
}
export function prevStep(state: WizardState, step: WizardStepId): WizardStepId | null {
  const steps = stepsFor(state.path)
  const i = steps.indexOf(step)
  return i > 0 ? steps[i - 1]! : null
}
export const isLastStep = (state: WizardState, step: WizardStepId) => nextStep(state, step) === null

/** The wizard started with a path: back to the choice resets everything that belonged to the old one. */
export function choosePath(path: DomainPath): WizardState {
  return { ...initialWizard(), path }
}

/* ------------------------------------ validation ------------------------------------ */

/** A registry label under the registry's rules (the server enforces them again). */
export function validateLabel(name: string, registry: Pick<DomainRegistry, 'name_min_length' | 'name_max_length' | 'name_pattern'> | null): string | null {
  const v = name.trim().toLowerCase()
  if (!v) return 'Enter the name you want'
  const min = registry?.name_min_length ?? 2
  const max = registry?.name_max_length ?? 63
  if (v.length < min) return `Use at least ${min} characters`
  if (v.length > max) return `Use at most ${max} characters`
  let pattern: RegExp
  try {
    pattern = new RegExp(registry?.name_pattern || '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$')
  } catch {
    pattern = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/
  }
  if (!pattern.test(v)) return 'Use lowercase letters, digits and dashes, starting and ending with a letter or digit'
  return null
}

export function validateDomain(domain: string): string | null {
  const v = domain.trim().toLowerCase()
  if (!v) return 'Enter the domain you want, e.g. example.com'
  if (!/^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/.test(v)) return 'Enter a domain like example.com (no https://, no path)'
  return null
}

export function fqdnFor(registry: Pick<DomainRegistry, 'suffix'> | null, name: string): string {
  const label = name.trim().toLowerCase()
  return registry && label ? `${label}.${registry.suffix}` : ''
}

/** Whether a domain falls under a provider's suffixes. A provider with no declared suffixes accepts anything. */
export function matchesProviderSuffix(provider: Pick<FreeDomainProvider, 'suffixes'> | null, domain: string): boolean {
  if (!provider || provider.suffixes.length === 0) return true
  const d = domain.trim().toLowerCase()
  return provider.suffixes.some((s) => d.endsWith(`.${s.replace(/^\./, '').toLowerCase()}`))
}

export interface WizardContext {
  registries: DomainRegistry[]
  providers: FreeDomainProvider[]
}

/** Why the current step cannot continue yet (null = it can). */
export function stepBlocker(step: WizardStepId, state: WizardState, ctx: WizardContext): string | null {
  switch (step) {
    case 'path':
      return state.path ? null : 'Choose one of the three paths'
    case 'registry':
      return ctx.registries.some((r) => r.id === state.registryId) ? null : 'Choose a registry'
    case 'name':
      return validateLabel(state.name, ctx.registries.find((r) => r.id === state.registryId) ?? null)
    case 'preview':
      return null
    case 'provider':
      return ctx.providers.some((p) => p.id === state.providerId) ? null : 'Choose a provider'
    case 'domain': {
      const err = validateDomain(state.domain)
      if (err) return err
      const provider = ctx.providers.find((p) => p.id === state.providerId) ?? null
      return matchesProviderSuffix(provider, state.domain) ? null : `This provider only issues domains ending in ${provider!.suffixes.map((s) => `.${s.replace(/^\./, '')}`).join(', ')}`
    }
    case 'search':
      return state.selected ? (state.selected.available ? null : 'That domain is taken') : 'Search and pick an available domain'
    case 'confirm':
      return purchaseBlocker(state.selected)
  }
}

/** A purchase needs a quoted price: an unknown price is never approved by default. */
export function purchaseBlocker(sel: DomainAvailability | null): string | null {
  if (!sel) return 'Pick a domain first'
  if (!sel.available) return 'That domain is taken'
  if (sel.price_micros == null) return 'The registrar did not quote a price, so a purchase cannot be requested'
  return null
}

/* ------------------------------------ requests ------------------------------------ */

export function previewRequest(state: WizardState): DomainRegistrationPreviewRequest | null {
  if (state.path !== 'github_registry' || !state.registryId || !state.name.trim()) return null
  return { registry_id: state.registryId, name: state.name.trim().toLowerCase() }
}

/**
 * The request that creates the registration. For the GitHub and purchase paths the server answers with a human
 * approval (single use, bound to exactly this content or price); the assisted path creates the DNS plan.
 */
export function registrationRequest(state: WizardState): DomainRegistrationCreate | null {
  switch (state.path) {
    case 'github_registry':
      return state.registryId && state.name.trim() ? { path: 'github_registry', registry_id: state.registryId, name: state.name.trim().toLowerCase() } : null
    case 'free_provider':
      return state.providerId && state.domain.trim() ? { path: 'free_provider', provider_id: state.providerId, domain: state.domain.trim().toLowerCase() } : null
    case 'paid_registrar': {
      const sel = state.selected
      if (!sel || purchaseBlocker(sel)) return null
      return { path: 'paid_registrar', registrar: sel.registrar, domain: sel.domain, years: 1, max_price_micros: sel.price_micros }
    }
    default:
      return null
  }
}

/** The label of the final action per path. */
export function submitLabel(path: DomainPath | null): string {
  switch (path) {
    case 'github_registry':
      return 'Request approval to submit'
    case 'free_provider':
      return 'Show me the steps'
    case 'paid_registrar':
      return 'Request purchase'
    default:
      return 'Continue'
  }
}

/** Whether the request creates a human approval the Founder must find in Approvals. */
export function createsApproval(path: DomainPath | null): boolean {
  return path === 'github_registry' || path === 'paid_registrar'
}
