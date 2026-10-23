import type { Agent, AgentKind, RoleFamily } from '@/api/types'

export const ROLE_FAMILIES: readonly RoleFamily[] = [
  'founder',
  'hr',
  'engineering',
  'design',
  'marketing',
  'sales',
  'customer_success',
  'research',
  'qa',
  'operations',
  'finance',
  'generic',
]

export const ROLE_FAMILY_LABEL: Record<RoleFamily, string> = {
  founder: 'Founder',
  hr: 'People & HR',
  engineering: 'Engineering',
  design: 'Design',
  marketing: 'Marketing',
  sales: 'Sales',
  customer_success: 'Customer success',
  research: 'Research',
  qa: 'Quality',
  operations: 'Operations',
  finance: 'Finance',
  generic: 'General',
}

export function isRoleFamily(value: unknown): value is RoleFamily {
  return typeof value === 'string' && (ROLE_FAMILIES as readonly string[]).includes(value)
}

/**
 * Keyword rules, checked in order: the first family with a matching keyword wins.
 * Order matters for compound titles ("QA Engineer" is quality, "DevOps Engineer" is operations,
 * "Financial Analyst" is finance, "Sales Engineer" is sales), so generic words like `engineer`
 * and `analyst` sit last.
 */
const RULES: ReadonlyArray<readonly [RoleFamily, RegExp]> = [
  ['qa', /\b(qa|sdet|quality|tester|testing|test engineer)\b/],
  ['hr', /\b(hr|human resources|people|recruit\w*|talent|hiring|onboarding)\b/],
  ['finance', /\b(financ\w*|cfo|accountant|accounting|bookkeep\w*|controller|treasury|billing|payroll|fp&a)\b/],
  ['sales', /\b(sales|account executive|business development|bdr|sdr|revenue|partnerships?|cro)\b/],
  ['customer_success', /\b(customer|support|success|helpdesk|help desk|client)\b/],
  ['design', /\b(design\w*|ux|ui|creative|illustrat\w*|art director)\b/],
  ['marketing', /\b(marketing|marketer|growth|seo|content|copywrit\w*|social media|brand|communications|pr|cmo)\b/],
  ['research', /\b(research\w*|scientist|analyst|analytics|strateg\w*|insights)\b/],
  ['operations', /\b(operations?|ops|devops|sre|site reliability|infrastructure|sysadmin|it admin\w*|coo|office manager|logistics)\b/],
  ['engineering', /\b(engineer\w*|developer|programmer|software|backend|back-end|frontend|front-end|full[- ]?stack|architect|coder|swe|cto|technolog\w*|machine learning|ml)\b/],
]

/**
 * The employee type that selects an avatar. Pure.
 * Precedence: explicit `role_family` (M4) > founder kind > role_title keywords > generic.
 */
export function inferRoleFamily(agent: Pick<Agent, 'kind' | 'role_title'> & { role_family?: string | null }): RoleFamily {
  if (isRoleFamily(agent.role_family)) return agent.role_family
  return inferFromKindAndTitle(agent.kind, agent.role_title)
}

export function inferFromKindAndTitle(kind: AgentKind, roleTitle: string | null | undefined): RoleFamily {
  if (kind === 'founder') return 'founder'
  const title = (roleTitle ?? '').toLowerCase().replace(/[_/]+/g, ' ')
  if (!title.trim()) return 'generic'
  for (const [family, re] of RULES) if (re.test(title)) return family
  return 'generic'
}

/** Short role label under an avatar: drops "Head of"/"Senior" noise only when it is long. */
export function shortRole(roleTitle: string, max = 22): string {
  const t = roleTitle.trim()
  if (t.length <= max) return t
  return `${t.slice(0, max - 1).trimEnd()}…`
}
