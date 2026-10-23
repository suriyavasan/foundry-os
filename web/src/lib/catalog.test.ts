import { describe, expect, it } from 'vitest'
import { makeSkill } from '@/test/factories'
import { describeInstall, repoQueryFor } from './catalog'

describe('repoQueryFor', () => {
  it('uses an owner/repo slug, else parses it from the source ref', () => {
    expect(repoQueryFor({ slug: 'anthropics/skills', source_ref: 'github:anthropics/skills' })).toBe('anthropics/skills')
    expect(repoQueryFor({ slug: 'skills', source_ref: 'github:anthropics/skills@abc123:skills/pdf' })).toBe('anthropics/skills')
  })
})

describe('describeInstall', () => {
  it('states license, trust and scan, and that quarantine blocks use', () => {
    const r = describeInstall({ skill: makeSkill({ license: 'MIT', trust: 'community', scan_verdict: 'caution' }), outcome: 'installed', quarantined: true, scan: {} })
    expect(r.headline).toBe('Installed, but quarantined')
    expect(r.detail).toContain('License: MIT')
    expect(r.detail).toContain('community')
    expect(r.detail).toContain('caution')
    expect(r.detail).toMatch(/no agent can use it/)
  })
  it('says an active install can be assigned, and reports unchanged installs', () => {
    expect(describeInstall({ skill: makeSkill({ license: null }), outcome: 'installed', quarantined: false, scan: {} }).detail).toMatch(/none declared.*can be assigned/)
    expect(describeInstall({ skill: makeSkill(), outcome: 'unchanged', quarantined: false, scan: {} }).headline).toBe('Already up to date')
  })
})
