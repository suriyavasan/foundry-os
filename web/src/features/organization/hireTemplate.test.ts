import { describe, expect, it } from 'vitest'
import type { Department } from '@/api/types'
import { makeAgent, makeSkill, makeTemplate } from '@/test/factories'
import { emptyAgentForm } from './agentForm'
import { buildTemplateHire, hireableTemplates, pickManager, prefillFromTemplate } from './hireTemplate'

const dept = (over: Partial<Department>): Department => ({ id: 'd1', company_id: 'c1', name: 'Engineering', description: null, lead_agent_id: null, agent_count: 0, created_at: 'x', updated_at: 'x', ...over })
const founder = makeAgent({ id: 'f1', name: 'Atlas', kind: 'founder' })
const lead = makeAgent({ id: 'l1', name: 'Lena', kind: 'lead', role_title: 'Eng lead' })
const skills = ['plan', 'debugging', 'extra'].map((slug) => makeSkill({ slug }))

describe('prefillFromTemplate', () => {
  const tpl = makeTemplate({ default_skill_slugs: ['plan', 'debugging', 'ghost'] })

  it('pre-fills title, kind, instructions, department and manager', () => {
    const p = prefillFromTemplate(tpl, { agents: [founder, lead], departments: [dept({ id: 'd9', lead_agent_id: 'l1' })], skills })
    expect(p.values).toMatchObject({ role_title: 'Software Engineer', kind: 'specialist', instructions: 'Write clean code.', department_id: 'd9', manager_agent_id: 'l1', name: '' })
    expect(p.departmentToCreate).toBeNull()
  })
  it('splits default skills into available and unavailable (not in the library or not active)', () => {
    const p = prefillFromTemplate(tpl, { agents: [founder], departments: [], skills: [...skills, makeSkill({ slug: 'ghost', status: 'quarantined' })] })
    expect(p.defaultSkills).toEqual(['plan', 'debugging'])
    expect(p.unavailableSkills).toEqual(['ghost'])
  })
  it('lets the server decide when the library could not be read', () => {
    const p = prefillFromTemplate(tpl, { agents: [founder], departments: [], skills: null })
    expect(p.defaultSkills).toEqual(['plan', 'debugging', 'ghost'])
    expect(p.unavailableSkills).toEqual([])
  })
  it('reports a department the hire will create, and matches existing ones case-insensitively', () => {
    expect(prefillFromTemplate(tpl, { agents: [founder], departments: [], skills }).departmentToCreate).toBe('Engineering')
    expect(prefillFromTemplate(tpl, { agents: [founder], departments: [dept({ id: 'd2', name: 'ENGINEERING' })], skills }).values.department_id).toBe('d2')
  })
})

describe('pickManager', () => {
  it('prefers the department lead for specialists, the founder for leads, and ignores retired agents', () => {
    const departments = [dept({ lead_agent_id: 'l1' })]
    expect(pickManager(makeTemplate(), [founder, lead], departments)).toBe('l1')
    expect(pickManager(makeTemplate({ default_kind: 'lead' }), [founder, lead], departments)).toBe('f1')
    expect(pickManager(makeTemplate(), [founder, { ...lead, status: 'retired' }], departments)).toBe('f1')
    expect(pickManager(makeTemplate(), [], [])).toBe('')
  })
})

describe('hireableTemplates', () => {
  it('hides the founder template and sorts by title', () => {
    const out = hireableTemplates([makeTemplate({ slug: 'z', title: 'Zed' }), makeTemplate({ slug: 'f', family: 'founder', default_kind: 'founder', title: 'Founder' }), makeTemplate({ slug: 'a', title: 'Alpha' })])
    expect(out.map((t) => t.slug)).toEqual(['a', 'z'])
  })
})

describe('buildTemplateHire', () => {
  const tpl = makeTemplate()
  const base = () => ({ ...emptyAgentForm({ name: 'Sam', role_title: tpl.title, kind: tpl.default_kind, instructions: tpl.instructions, manager_agent_id: 'f1' }) })

  it('posts the template slug and omits fields that equal the template defaults', () => {
    const { body, removeSkills } = buildTemplateHire(base(), tpl, ['plan', 'debugging'], ['plan', 'debugging'])
    expect(body).toMatchObject({ name: 'Sam', role_title: 'Software Engineer', role_template: 'software-engineer', manager_agent_id: 'f1' })
    expect(body).not.toHaveProperty('kind')
    expect(body).not.toHaveProperty('instructions')
    expect(body).not.toHaveProperty('department_id')
    expect(body).not.toHaveProperty('skill_slugs')
    expect(body).not.toHaveProperty('model_provider')
    expect(body).not.toHaveProperty('monthly_budget_micros')
    expect(removeSkills).toEqual([])
  })
  it('sends edited fields', () => {
    const v = { ...base(), kind: 'lead' as const, instructions: 'Custom.', department_id: 'd1', monthly_budget: '25', model_provider: 'p1', model_name: 'qwen' }
    const { body } = buildTemplateHire(v, tpl, [], [])
    expect(body).toMatchObject({ kind: 'lead', instructions: 'Custom.', department_id: 'd1', monthly_budget_micros: 25_000_000, model_provider: 'p1', model_name: 'qwen' })
  })
  it('sends only extra skills as skill_slugs, and returns deselected defaults for removal', () => {
    const { body, removeSkills } = buildTemplateHire(base(), tpl, ['plan', 'extra', 'more'], ['plan', 'debugging'])
    expect(body.skill_slugs).toEqual(['extra', 'more'])
    expect(removeSkills).toEqual(['debugging'])
  })
})
