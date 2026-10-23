import { describe, expect, it } from 'vitest'
import type { AgentSkill, AuditEvent, SkillStatus } from '@/api/types'
import { makeSkill, makeSkillDetail, makeTask, makeTemplate } from '@/test/factories'
import {
  NO_FILTERS,
  assignedAgentCounts,
  collapseHrActivity,
  filterSkills,
  isFlagged,
  openHiringRequests,
  readScanReport,
  scanVerdictInfo,
  skillMdPath,
  skillStatusInfo,
  skillsByFamily,
  sortSkills,
  summarizeLibrary,
  tagOptions,
} from './skills'

describe('scan verdict mapping', () => {
  it('maps each documented verdict to a tone and label, flagging caution and dangerous', () => {
    expect(scanVerdictInfo('safe')).toMatchObject({ label: 'Safe', tone: 'success', flagged: false })
    expect(scanVerdictInfo('caution')).toMatchObject({ label: 'Caution', tone: 'warning', flagged: true })
    expect(scanVerdictInfo('dangerous')).toMatchObject({ label: 'Dangerous', tone: 'danger', flagged: true })
    expect(scanVerdictInfo('unscanned')).toMatchObject({ label: 'Unscanned', tone: 'neutral', flagged: false })
  })
  it('never treats an unknown verdict from a newer backend as safe', () => {
    const v = scanVerdictInfo('mystery')
    expect(v.flagged).toBe(true)
    expect(v.tone).not.toBe('success')
    expect(v.label).toBe('mystery')
  })
  it('flags quarantined skills regardless of the verdict, and flagged verdicts regardless of status', () => {
    expect(isFlagged({ status: 'quarantined', scan_verdict: 'safe' })).toBe(true)
    expect(isFlagged({ status: 'active', scan_verdict: 'caution' })).toBe(true)
    expect(isFlagged({ status: 'active', scan_verdict: 'safe' })).toBe(false)
  })
  it('labels statuses', () => {
    expect(skillStatusInfo('quarantined').tone).toBe('danger')
    expect(skillStatusInfo('active').tone).toBe('success')
    expect(skillStatusInfo('archived').tone).toBe('neutral')
  })
})

describe('library filtering', () => {
  const skills = [
    makeSkill({ id: '1', slug: 'humanizer', name: 'humanizer', description: 'Strip AI-isms', tags: ['writing', 'prose'], category: 'creative' }),
    makeSkill({ id: '2', slug: 'sales-playbook', name: 'sales-playbook', description: 'Outreach', tags: ['sales'], category: 'sales', source_kind: 'company_authored' }),
    makeSkill({ id: '3', slug: 'gh-thing', name: 'gh-thing', description: 'From GitHub', tags: ['writing'], source_kind: 'github', trust: 'community', scan_verdict: 'caution', status: 'quarantined' }),
    makeSkill({ id: '4', slug: 'old', name: 'old', status: 'archived' as SkillStatus }),
  ]
  const ids = (f: Partial<typeof NO_FILTERS>) => filterSkills(skills, { ...NO_FILTERS, ...f }).map((s) => s.id)

  it('returns everything with no filters', () => expect(ids({})).toEqual(['1', '2', '3', '4']))
  it('filters by status', () => {
    expect(ids({ status: 'quarantined' })).toEqual(['3'])
    expect(ids({ status: 'active' })).toEqual(['1', '2'])
  })
  it('filters by source kind', () => expect(ids({ source: 'github' })).toEqual(['3']))
  it('filters by tag or category', () => {
    expect(ids({ tag: 'writing' })).toEqual(['1', '3'])
    expect(ids({ tag: 'sales' })).toEqual(['2'])
  })
  it('searches name, description and tags; all words must match', () => {
    expect(ids({ q: 'strip' })).toEqual(['1'])
    expect(ids({ q: 'prose creative' })).toEqual(['1'])
    expect(ids({ q: 'WRITING github' })).toEqual(['3'])
    expect(ids({ q: 'nothing like this' })).toEqual([])
  })
  it('combines filters', () => expect(ids({ tag: 'writing', status: 'active' })).toEqual(['1']))
  it('puts quarantined first, then flagged, then the rest, archived last', () => {
    expect(sortSkills(skills).map((s) => s.id)).toEqual(['3', '1', '2', '4'])
  })
  it('lists tags and categories by frequency', () => {
    expect(tagOptions(skills)[0]).toBe('writing')
    expect(tagOptions(skills)).toContain('creative')
  })
  it('summarizes the library', () => {
    expect(summarizeLibrary(skills)).toEqual({ total: 4, active: 2, quarantined: 1, archived: 1, flagged: 1 })
  })
})

describe('skills by role family', () => {
  const skills = [makeSkill({ slug: 'plan' }), makeSkill({ slug: 'debugging' }), makeSkill({ slug: 'design-md' }), makeSkill({ slug: 'bad', status: 'quarantined' })]
  it('counts distinct active default skills per family, omitting empty families and inactive skills', () => {
    const templates = [
      makeTemplate({ family: 'engineering', default_skill_slugs: ['plan', 'debugging', 'missing'] }),
      makeTemplate({ slug: 'dev2', family: 'engineering', default_skill_slugs: ['plan'] }),
      makeTemplate({ slug: 'designer', family: 'design', default_skill_slugs: ['design-md', 'plan'] }),
      makeTemplate({ slug: 'qa', family: 'qa', default_skill_slugs: ['bad'] }),
    ]
    expect(skillsByFamily(skills, templates)).toEqual([
      { family: 'engineering', count: 2 },
      { family: 'design', count: 2 },
    ])
  })
})

describe('assigned agent counts', () => {
  it('counts agents per skill id and tolerates unloaded agents', () => {
    const a = (skillId: string): AgentSkill => ({ skill: makeSkill({ id: skillId }), assigned_by_type: 'human', assigned_by_id: null, assigned_at: 'x' })
    const counts = assignedAgentCounts([[a('s1'), a('s2')], [a('s1')], undefined])
    expect(counts.get('s1')).toBe(2)
    expect(counts.get('s2')).toBe(1)
    expect(counts.get('s3')).toBeUndefined()
  })
})

describe('scan report', () => {
  it('reads findings sorted by severity and falls back to the skill verdict', () => {
    const r = readScanReport(
      makeSkillDetail({
        scan_verdict: 'caution',
        scan_report: { findings: [{ severity: 'low', pattern: 'a' }, { severity: 'high', pattern: 'b' }, 'junk', null] },
      }),
    )
    expect(r.verdict).toBe('caution')
    expect(r.findings.map((f) => f.pattern)).toEqual(['b', 'a'])
    expect(r.finding_count).toBe(2)
  })
  it('survives an empty or malformed report', () => {
    expect(readScanReport(makeSkillDetail({ scan_report: {} })).findings).toEqual([])
    expect(readScanReport(makeSkillDetail({ scan_report: { findings: 'nope' } })).findings).toEqual([])
  })
  it('finds SKILL.md at the root only', () => {
    expect(skillMdPath([{ path: 'refs/SKILL.md' }, { path: 'SKILL.md' }])).toBe('SKILL.md')
    expect(skillMdPath([{ path: 'refs/SKILL.md' }])).toBeNull()
  })
})

describe('HR activity', () => {
  const ev = (id: string, action: string, at: string, summary = action): AuditEvent => ({
    id, company_id: 'c1', actor_type: 'system', actor_id: null, actor_label: 'System', action, target_type: 'skill', target_id: id, summary, data: {}, created_at: at,
  })
  it('keeps only HR-relevant events, newest first', () => {
    const out = collapseHrActivity([ev('1', 'agent.paused', '2026-10-23T12:00:00Z'), ev('2', 'hire.created', '2026-10-23T12:05:00Z', 'Hired Sam'), ev('3', 'company.created', '2026-10-23T12:06:00Z')])
    expect(out.map((e) => e.text)).toEqual(['Hired Sam'])
  })
  it('collapses a bulk install into one line so it cannot bury real hires', () => {
    const installs = Array.from({ length: 35 }, (_, i) => ev(`s${i}`, 'skill.installed', `2026-10-23T12:00:${String(i).padStart(2, '0')}Z`))
    const out = collapseHrActivity([...installs, ev('h', 'hire.created', '2026-10-23T13:00:00Z', 'Hired Sam')])
    expect(out).toHaveLength(2)
    expect(out[0]!.text).toBe('Hired Sam')
    expect(out[1]).toMatchObject({ text: '35 skills installed', count: 35, targetId: null })
  })
  it('does not merge installs that are far apart in time', () => {
    const out = collapseHrActivity([ev('a', 'skill.installed', '2026-10-23T12:00:00Z', 'Installed a'), ev('b', 'skill.installed', '2026-10-23T18:00:00Z', 'Installed b')])
    expect(out.map((e) => e.text)).toEqual(['Installed b', 'Installed a'])
  })
})

describe('open hiring requests', () => {
  it('keeps hiring tasks that are not terminal', () => {
    const tasks = [
      makeTask({ id: 'a', kind: 'hiring', status: 'in_progress' }),
      makeTask({ id: 'b', kind: 'hiring', status: 'done' }),
      makeTask({ id: 'c', kind: 'hiring', status: 'cancelled' }),
      makeTask({ id: 'd', kind: 'work', status: 'backlog' }),
      makeTask({ id: 'e', kind: 'hiring', status: 'backlog', created_at: '2026-10-24T00:00:00Z' }),
    ]
    expect(openHiringRequests(tasks).map((t) => t.id)).toEqual(['e', 'a'])
  })
})
