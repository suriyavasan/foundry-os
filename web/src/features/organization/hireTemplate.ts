import type { Agent, CreateAgentRequest, Department, RoleTemplate, Skill } from '@/api/types'
import { parseBudgetInput } from '@/lib/money'
import { emptyAgentForm, managerCandidates, toCreateAgentBody, type AgentFormValues } from './agentForm'

/**
 * Pure logic of the "choose a role template" hire flow (M4). The server applies a template's defaults
 * for every field the client does not send, so the dialog pre-fills the form for review but only sends
 * what the human actually changed, which keeps the template authoritative for the rest
 * (tool permissions, kind, default skills).
 */

export interface TemplatePrefill {
  values: AgentFormValues
  /** Default skills that exist and are active in the library: what the server will assign. */
  defaultSkills: string[]
  /** Default skills that are not in the library (or not active): the server skips them. */
  unavailableSkills: string[]
  /** Set when the template's department does not exist yet (the server creates it on hire). */
  departmentToCreate: string | null
}

const norm = (s: string) => s.trim().toLowerCase()

export function findDepartment(departments: Department[], name: string | null): Department | undefined {
  return name ? departments.find((d) => norm(d.name) === norm(name)) : undefined
}

/** Templates a human can hire from: the founder is unique, so the founder template is not offered. */
export function hireableTemplates(templates: RoleTemplate[]): RoleTemplate[] {
  return templates.filter((t) => t.family !== 'founder' && t.default_kind !== 'founder').sort((a, b) => a.title.localeCompare(b.title))
}

/**
 * Who the new hire reports to: the lead of the template's department when there is an active one
 * (and the hire is not itself a lead), otherwise the active founder.
 */
export function pickManager(tpl: RoleTemplate, agents: Agent[], departments: Department[]): string {
  const candidates = managerCandidates(agents).filter((a) => a.status === 'active' || a.status === 'paused')
  const founder = candidates.find((a) => a.kind === 'founder')
  const dept = findDepartment(departments, tpl.default_department)
  const lead = dept?.lead_agent_id ? candidates.find((a) => a.id === dept.lead_agent_id) : undefined
  if (tpl.default_kind === 'specialist' && lead) return lead.id
  return founder?.id ?? lead?.id ?? ''
}

export function prefillFromTemplate(tpl: RoleTemplate, ctx: { agents: Agent[]; departments: Department[]; skills: Skill[] | null }): TemplatePrefill {
  // `skills: null` = the library could not be read; then the server is the judge of which defaults exist.
  const active = ctx.skills ? new Set(ctx.skills.filter((s) => s.status === 'active').map((s) => s.slug)) : null
  const available = (slug: string) => active === null || active.has(slug)
  const dept = findDepartment(ctx.departments, tpl.default_department)
  return {
    values: emptyAgentForm({
      role_title: tpl.title,
      kind: tpl.default_kind === 'founder' ? 'specialist' : tpl.default_kind,
      instructions: tpl.instructions,
      department_id: dept?.id ?? '',
      manager_agent_id: pickManager(tpl, ctx.agents, ctx.departments),
    }),
    defaultSkills: tpl.default_skill_slugs.filter(available),
    unavailableSkills: tpl.default_skill_slugs.filter((s) => !available(s)),
    departmentToCreate: tpl.default_department && !dept ? tpl.default_department : null,
  }
}

export interface TemplateHire {
  body: CreateAgentRequest
  /** Default skills the human deselected: the server assigns defaults on create, so these are removed right after. */
  removeSkills: string[]
}

/**
 * The `POST /agents` body for a template hire. Fields equal to the template's defaults are omitted so the
 * server applies them itself; `skill_slugs` carries only the extras, deselected defaults are returned for
 * removal after creation.
 */
export function buildTemplateHire(values: AgentFormValues, tpl: RoleTemplate, selectedSkills: string[], defaultSkills: string[]): TemplateHire {
  const base = toCreateAgentBody(values)
  const body: CreateAgentRequest = { ...base, role_template: tpl.slug }
  if (values.kind === tpl.default_kind) delete body.kind
  if (values.instructions.trim() === tpl.instructions.trim()) delete body.instructions
  if (!values.department_id) delete body.department_id
  if (!values.manager_agent_id) delete body.manager_agent_id
  if (!values.responsibilities.trim()) delete body.responsibilities
  if (!values.model_provider.trim()) {
    delete body.model_provider
    delete body.model_name
  }
  if (values.runtime === 'unassigned') delete body.runtime // template/founder-runtime defaults are the server's call
  if (parseBudgetInput(values.daily_budget).value === null) delete body.daily_budget_micros
  if (parseBudgetInput(values.monthly_budget).value === null) delete body.monthly_budget_micros
  const defaults = new Set(defaultSkills)
  const chosen = new Set(selectedSkills)
  const extras = selectedSkills.filter((s) => !defaults.has(s))
  if (extras.length) body.skill_slugs = extras
  return { body, removeSkills: defaultSkills.filter((s) => !chosen.has(s)) }
}
