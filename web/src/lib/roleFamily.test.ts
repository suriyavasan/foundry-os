import { describe, expect, it } from 'vitest'
import type { AgentKind } from '@/api/types'
import { ROLE_FAMILIES, inferRoleFamily, isRoleFamily, shortRole } from './roleFamily'

const infer = (role_title: string, kind: AgentKind = 'specialist', role_family?: string | null) =>
  inferRoleFamily({ kind, role_title, role_family })

describe('inferRoleFamily', () => {
  it('prefers an explicit role_family from the backend (M4)', () => {
    expect(infer('Software Engineer', 'specialist', 'design')).toBe('design')
  })

  it('ignores an unknown role_family and falls back to inference', () => {
    expect(infer('Software Engineer', 'specialist', 'wizard')).toBe('engineering')
    expect(infer('Software Engineer', 'specialist', null)).toBe('engineering')
  })

  it('treats every founder kind as founder regardless of title', () => {
    expect(infer('Head of Sales', 'founder')).toBe('founder')
    expect(infer('Founder & CEO', 'founder')).toBe('founder')
  })

  it.each([
    ['Head of People', 'hr'],
    ['Technical Recruiter', 'hr'],
    ['HR Business Partner', 'hr'],
    ['Backend Developer', 'engineering'],
    ['Chief Technology Officer', 'engineering'],
    ['Full-Stack Engineer', 'engineering'],
    ['Product Designer', 'design'],
    ['UX Researcher', 'design'],
    ['Growth Marketer', 'marketing'],
    ['SEO Lead', 'marketing'],
    ['Account Executive', 'sales'],
    ['Sales Engineer', 'sales'],
    ['Customer Success Manager', 'customer_success'],
    ['Support Specialist', 'customer_success'],
    ['Market Researcher', 'research'],
    ['Data Analyst', 'research'],
    ['QA Engineer', 'qa'],
    ['Test Engineer', 'qa'],
    ['DevOps Engineer', 'operations'],
    ['Operations Manager', 'operations'],
    ['People Ops', 'hr'],
    ['Financial Analyst', 'finance'],
    ['Bookkeeper', 'finance'],
    ['CFO', 'finance'],
  ] as const)('infers %s as %s', (title, family) => {
    expect(infer(title)).toBe(family)
  })

  it('falls back to generic for unknown or empty titles', () => {
    expect(infer('Wizard of Vibes')).toBe('generic')
    expect(infer('')).toBe('generic')
    expect(infer('Reviewer', 'reviewer')).toBe('generic')
  })

  it('does not match short keywords inside other words', () => {
    expect(infer('Pharmacist')).toBe('generic') // "hr"/"ui" are not substrings-matched
    expect(infer('Guidance Counselor')).toBe('generic')
  })

  it('always returns a known family', () => {
    for (const t of ['x', 'Lead', 'Chief of Staff', 'Designer & Developer']) {
      expect(ROLE_FAMILIES).toContain(infer(t))
    }
  })
})

describe('isRoleFamily / shortRole', () => {
  it('validates family strings', () => {
    expect(isRoleFamily('qa')).toBe(true)
    expect(isRoleFamily('nope')).toBe(false)
    expect(isRoleFamily(undefined)).toBe(false)
  })
  it('truncates long role titles with an ellipsis', () => {
    expect(shortRole('Designer')).toBe('Designer')
    expect(shortRole('Senior Staff Machine Learning Researcher', 20)).toHaveLength(20)
    expect(shortRole('Senior Staff Machine Learning Researcher', 20).endsWith('…')).toBe(true)
  })
})
