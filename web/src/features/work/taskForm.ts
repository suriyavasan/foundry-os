import type { CreateGoalRequest, CreateProjectRequest, CreateTaskRequest, GoalStatus, Priority } from '@/api/types'
import { parseBudgetInput } from '@/lib/money'
import { memberRef } from '@/lib/people'

export const LIMITS = { taskTitle: 300, goalTitle: 200, projectName: 200 } as const

export interface TaskFormValues {
  title: string
  description: string
  acceptance_criteria: string
  project_id: string
  assignee_agent_id: string
  /** M7: a human assignee. Exclusive with `assignee_agent_id`. */
  assignee_member_id: string
  reviewer_agent_id: string
  priority: Priority
  budget: string
}
export type TaskFormErrors = Partial<Record<keyof TaskFormValues, string>>

export const emptyTaskForm = (over: Partial<TaskFormValues> = {}): TaskFormValues => ({
  title: '',
  description: '',
  acceptance_criteria: '',
  project_id: '',
  assignee_agent_id: '',
  assignee_member_id: '',
  reviewer_agent_id: '',
  priority: 'normal',
  budget: '',
  ...over,
})

export function validateTaskForm(v: TaskFormValues): TaskFormErrors {
  const e: TaskFormErrors = {}
  const title = v.title.trim()
  if (!title) e.title = 'Title is required'
  else if (title.length > LIMITS.taskTitle) e.title = `At most ${LIMITS.taskTitle} characters`
  const b = parseBudgetInput(v.budget)
  if (b.error) e.budget = b.error
  if (v.assignee_agent_id && v.assignee_agent_id === v.reviewer_agent_id) e.reviewer_agent_id = 'The reviewer should be someone other than the assignee'
  return e
}

/** The value of the assignee picker: an agent id, `member:<id>` for a human, or '' for nobody. */
export const assigneePickerValue = (v: Pick<TaskFormValues, 'assignee_agent_id' | 'assignee_member_id'>): string => (v.assignee_member_id ? memberRef(v.assignee_member_id) : v.assignee_agent_id)

/** Form patch for a picker choice (see `assigneePickerValue`). */
export function assigneePatch(value: string): Pick<TaskFormValues, 'assignee_agent_id' | 'assignee_member_id'> {
  return value.startsWith('member:') ? { assignee_agent_id: '', assignee_member_id: value.slice('member:'.length) } : { assignee_agent_id: value, assignee_member_id: '' }
}

const opt = (s: string): string | null => (s.trim() === '' ? null : s.trim())

export function toCreateTaskBody(v: TaskFormValues, extra: { parent_task_id?: string } = {}): CreateTaskRequest {
  return {
    title: v.title.trim(),
    description: opt(v.description),
    acceptance_criteria: opt(v.acceptance_criteria),
    project_id: v.project_id || null,
    assignee_agent_id: v.assignee_member_id ? null : v.assignee_agent_id || null,
    ...(v.assignee_member_id ? { assignee_ref: memberRef(v.assignee_member_id) } : null),
    reviewer_agent_id: v.reviewer_agent_id || null,
    priority: v.priority,
    budget_micros: parseBudgetInput(v.budget).value,
    ...(extra.parent_task_id ? { parent_task_id: extra.parent_task_id } : null),
  }
}

/** Server field names -> form fields. */
export function mapTaskServerErrors(fields: Record<string, string>): TaskFormErrors {
  const alias: Record<string, keyof TaskFormValues> = { budget_micros: 'budget', assignee_ref: 'assignee_agent_id' }
  const known = new Set<string>(Object.keys(emptyTaskForm()))
  const out: TaskFormErrors = {}
  for (const [k, msg] of Object.entries(fields)) {
    const key = alias[k] ?? (known.has(k) ? (k as keyof TaskFormValues) : undefined)
    if (key) out[key] = msg
  }
  return out
}

export interface GoalFormValues {
  title: string
  description: string
  priority: Priority
  status: GoalStatus
  owner_agent_id: string
  target_date: string
}
export const emptyGoalForm = (): GoalFormValues => ({ title: '', description: '', priority: 'normal', status: 'active', owner_agent_id: '', target_date: '' })

export function validateGoalForm(v: GoalFormValues): Partial<Record<keyof GoalFormValues, string>> {
  const e: Partial<Record<keyof GoalFormValues, string>> = {}
  const t = v.title.trim()
  if (!t) e.title = 'Title is required'
  else if (t.length > LIMITS.goalTitle) e.title = `At most ${LIMITS.goalTitle} characters`
  if (v.target_date && !/^\d{4}-\d{2}-\d{2}$/.test(v.target_date)) e.target_date = 'Use YYYY-MM-DD'
  return e
}
export function toCreateGoalBody(v: GoalFormValues): CreateGoalRequest {
  return {
    title: v.title.trim(),
    description: opt(v.description),
    priority: v.priority,
    status: v.status,
    owner_agent_id: v.owner_agent_id || null,
    target_date: v.target_date || null,
  }
}

export interface ProjectFormValues {
  name: string
  description: string
  goal_id: string
  lead_agent_id: string
  budget: string
}
export const emptyProjectForm = (over: Partial<ProjectFormValues> = {}): ProjectFormValues => ({ name: '', description: '', goal_id: '', lead_agent_id: '', budget: '', ...over })

export function validateProjectForm(v: ProjectFormValues): Partial<Record<keyof ProjectFormValues, string>> {
  const e: Partial<Record<keyof ProjectFormValues, string>> = {}
  const n = v.name.trim()
  if (!n) e.name = 'Name is required'
  else if (n.length > LIMITS.projectName) e.name = `At most ${LIMITS.projectName} characters`
  const b = parseBudgetInput(v.budget)
  if (b.error) e.budget = b.error
  return e
}
export function toCreateProjectBody(v: ProjectFormValues): CreateProjectRequest {
  return {
    name: v.name.trim(),
    description: opt(v.description),
    goal_id: v.goal_id || null,
    lead_agent_id: v.lead_agent_id || null,
    budget_micros: parseBudgetInput(v.budget).value,
  }
}
