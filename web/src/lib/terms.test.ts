import { describe, expect, it } from 'vitest'
import { makeAgent } from '@/test/factories'
import { AGENT_KIND_LABEL, AI_COFOUNDER_LABEL, FOUNDER_LABEL, YOU_FOUNDER_LABEL, displayRole, findCofounder, isCofounder } from './terms'
import { ROLE_FAMILY_LABEL } from './roleFamily'

describe('terminology', () => {
  it('the human is the Founder; the AI agent of kind founder is the AI Co-founder', () => {
    expect(YOU_FOUNDER_LABEL).toBe('You · Founder')
    expect(FOUNDER_LABEL).toBe('Founder')
    expect(AI_COFOUNDER_LABEL).toBe('AI Co-founder')
    expect(AGENT_KIND_LABEL.founder).toBe('AI Co-founder')
    expect(ROLE_FAMILY_LABEL.founder).toBe('AI Co-founder')
  })

  it('never calls the human a co-founder', () => {
    expect(YOU_FOUNDER_LABEL.toLowerCase()).not.toContain('co-founder')
    expect(FOUNDER_LABEL.toLowerCase()).not.toContain('co-founder')
  })

  it('shows the AI Co-founder label instead of the stored role title, and leaves other roles alone', () => {
    expect(displayRole(makeAgent({ kind: 'founder', role_title: 'Founder & CEO' }))).toBe('AI Co-founder')
    expect(displayRole(makeAgent({ kind: 'lead', role_title: 'Head of Sales' }))).toBe('Head of Sales')
    expect(isCofounder(makeAgent({ kind: 'founder' }))).toBe(true)
    expect(isCofounder(makeAgent({ kind: 'lead' }))).toBe(false)
  })

  it('finds the active AI Co-founder, ignoring retired ones', () => {
    const retired = makeAgent({ id: 'old', kind: 'founder', status: 'retired' })
    const active = makeAgent({ id: 'new', kind: 'founder', status: 'active' })
    expect(findCofounder([retired, active])?.id).toBe('new')
    expect(findCofounder([retired])).toBeUndefined()
    expect(findCofounder([makeAgent({ kind: 'lead' })])).toBeUndefined()
  })
})

describe('UI copy (source scan)', () => {
  // Every shipped UI source file. The test files themselves are excluded.
  const files = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx', '!/src/api/schema.d.ts', '!/src/test/**'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>

  it('scans a meaningful number of files', () => {
    expect(Object.keys(files).length).toBeGreaterThan(100)
  })

  const forbidden: Array<[string, RegExp]> = [
    ['"AI Founder" (it is the AI Co-founder)', /AI Founder/],
    ['"human co-founder" (the human is the Founder)', /human co-founder/i],
    ['"Co-founder · <name>" as the human label', /Co-founder ·\s*\$\{/],
    ['"You (co-founder)"', /You \(.*co-founder\)/i],
  ]
  for (const [name, re] of forbidden) {
    it(`does not use ${name}`, () => {
      const offenders = Object.entries(files)
        .filter(([, src]) => re.test(src))
        .map(([path]) => path)
      expect(offenders).toEqual([])
    })
  }
})
