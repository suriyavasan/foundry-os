import type { CreateCompanyRequest } from '@/api/types'

/** Limits mirrored from docs/api.md. Name 1-120 is documented; others are conservative client-side bounds. */
export const LIMITS = {
  companyName: 120,
  founderName: 120,
  constraint: 500,
  maxConstraints: 50,
} as const

export interface OnboardingValues {
  name: string
  mission: string
  vision: string
  strategic_goal: string
  constraints: string[]
  timezone: string
  founder_name: string
  founder_model_provider: string
  founder_model_name: string
}

export type OnboardingErrors = Partial<Record<keyof OnboardingValues | 'constraints', string>>

export function validateOnboarding(v: OnboardingValues): OnboardingErrors {
  const errors: OnboardingErrors = {}
  const name = v.name.trim()
  if (!name) errors.name = 'Company name is required'
  else if (name.length > LIMITS.companyName) errors.name = `Company name must be at most ${LIMITS.companyName} characters`

  const founder = v.founder_name.trim()
  if (founder.length > LIMITS.founderName) errors.founder_name = `Founder name must be at most ${LIMITS.founderName} characters`

  if (!v.timezone.trim()) errors.timezone = 'Choose a timezone'

  if (v.constraints.length > LIMITS.maxConstraints) errors.constraints = `At most ${LIMITS.maxConstraints} constraints`
  else if (v.constraints.some((c) => c.trim().length > LIMITS.constraint))
    errors.constraints = `Each constraint must be at most ${LIMITS.constraint} characters`

  // provider and model go together
  const provider = v.founder_model_provider.trim()
  const model = v.founder_model_name.trim()
  if (model && !provider) errors.founder_model_provider = 'Provider is required when a model is set'
  return errors
}

const opt = (s: string) => {
  const t = s.trim()
  return t === '' ? undefined : t
}

export function toCreateCompanyBody(v: OnboardingValues): CreateCompanyRequest {
  return {
    name: v.name.trim(),
    mission: opt(v.mission),
    vision: opt(v.vision),
    strategic_goal: opt(v.strategic_goal),
    constraints: v.constraints.map((c) => c.trim()).filter(Boolean),
    timezone: v.timezone,
    founder: {
      name: opt(v.founder_name) ?? 'Atlas',
      model_provider: opt(v.founder_model_provider),
      model_name: opt(v.founder_model_name),
    },
  }
}

/** Map server 422 field paths onto form fields. */
export function mapServerFieldErrors(fields: Record<string, string>): OnboardingErrors {
  const map: Record<string, keyof OnboardingValues> = {
    name: 'name',
    mission: 'mission',
    vision: 'vision',
    strategic_goal: 'strategic_goal',
    timezone: 'timezone',
    'founder.name': 'founder_name',
    'founder.model_provider': 'founder_model_provider',
    'founder.model_name': 'founder_model_name',
  }
  const out: OnboardingErrors = {}
  for (const [k, msg] of Object.entries(fields)) {
    if (k === 'constraints' || k.startsWith('constraints.')) out.constraints ??= msg
    else if (map[k]) out[map[k]] = msg
  }
  return out
}
