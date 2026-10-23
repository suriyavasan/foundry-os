import { describe, expect, it } from 'vitest'
import {
  emptyGoalForm,
  emptyProjectForm,
  emptyTaskForm,
  mapTaskServerErrors,
  toCreateGoalBody,
  toCreateProjectBody,
  toCreateTaskBody,
  validateGoalForm,
  validateProjectForm,
  validateTaskForm,
} from './taskForm'

describe('task form', () => {
  it('requires a title', () => {
    expect(validateTaskForm(emptyTaskForm()).title).toBeDefined()
    expect(validateTaskForm(emptyTaskForm({ title: '  ' })).title).toBeDefined()
    expect(validateTaskForm(emptyTaskForm({ title: 'Ship it' }))).toEqual({})
  })
  it('rejects titles over 300 characters', () => {
    expect(validateTaskForm(emptyTaskForm({ title: 'x'.repeat(301) })).title).toMatch(/300/)
  })
  it('validates the USD budget and converts it to integer micros', () => {
    expect(validateTaskForm(emptyTaskForm({ title: 't', budget: 'abc' })).budget).toBeDefined()
    expect(toCreateTaskBody(emptyTaskForm({ title: 't', budget: '12.50' })).budget_micros).toBe(12_500_000)
    expect(toCreateTaskBody(emptyTaskForm({ title: 't' })).budget_micros).toBeNull()
  })
  it('does not let the reviewer be the assignee', () => {
    expect(validateTaskForm(emptyTaskForm({ title: 't', assignee_agent_id: 'a', reviewer_agent_id: 'a' })).reviewer_agent_id).toBeDefined()
  })
  it('builds a request body with nulls for blanks and an optional parent', () => {
    const body = toCreateTaskBody(emptyTaskForm({ title: ' Plan ', description: ' ', priority: 'high', project_id: 'p1' }), { parent_task_id: 't0' })
    expect(body).toMatchObject({ title: 'Plan', description: null, acceptance_criteria: null, project_id: 'p1', assignee_agent_id: null, priority: 'high', parent_task_id: 't0' })
  })
  it('maps server field errors', () => {
    expect(mapTaskServerErrors({ budget_micros: 'too big', title: 'bad', nope: 'x' })).toEqual({ budget: 'too big', title: 'bad' })
  })
})

describe('goal and project forms', () => {
  it('validates goals', () => {
    expect(validateGoalForm(emptyGoalForm()).title).toBeDefined()
    expect(validateGoalForm({ ...emptyGoalForm(), title: 'g', target_date: '10/12' }).target_date).toBeDefined()
    expect(toCreateGoalBody({ ...emptyGoalForm(), title: ' Grow ', target_date: '2026-12-31' })).toMatchObject({ title: 'Grow', target_date: '2026-12-31', owner_agent_id: null })
  })
  it('validates projects', () => {
    expect(validateProjectForm(emptyProjectForm()).name).toBeDefined()
    expect(validateProjectForm(emptyProjectForm({ name: 'p', budget: '-1' })).budget).toBeDefined()
    expect(toCreateProjectBody(emptyProjectForm({ name: 'P', goal_id: 'g1', budget: '100' }))).toMatchObject({ goal_id: 'g1', budget_micros: 100_000_000 })
  })
})

describe('human assignees (M7)', () => {
  it('maps picker choices to the form and back', async () => {
    const { assigneePatch, assigneePickerValue } = await import('./taskForm')
    expect(assigneePatch('member:m1')).toEqual({ assignee_agent_id: '', assignee_member_id: 'm1' })
    expect(assigneePatch('a1')).toEqual({ assignee_agent_id: 'a1', assignee_member_id: '' })
    expect(assigneePatch('')).toEqual({ assignee_agent_id: '', assignee_member_id: '' })
    expect(assigneePickerValue(emptyTaskForm({ assignee_member_id: 'm1' }))).toBe('member:m1')
    expect(assigneePickerValue(emptyTaskForm({ assignee_agent_id: 'a1' }))).toBe('a1')
  })
  it('sends assignee_ref for a human and no ref for an agent', () => {
    expect(toCreateTaskBody(emptyTaskForm({ title: 't', assignee_member_id: 'm1' }))).toMatchObject({ assignee_ref: 'member:m1', assignee_agent_id: null })
    const agentBody = toCreateTaskBody(emptyTaskForm({ title: 't', assignee_agent_id: 'a1' }))
    expect(agentBody.assignee_agent_id).toBe('a1')
    expect('assignee_ref' in agentBody).toBe(false)
  })
  it('maps a server error on assignee_ref to the assignee field', () => {
    expect(mapTaskServerErrors({ assignee_ref: 'not a member' })).toEqual({ assignee_agent_id: 'not a member' })
  })
})
