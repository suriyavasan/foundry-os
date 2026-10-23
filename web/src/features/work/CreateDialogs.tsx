import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useAgents, useCreateGoal, useCreateProject, useCreateTask, useGoals, useProjects } from '@/api'
import type { Agent, Priority } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, Select, Textarea, useToast } from '@/components/ui'
import { useCanvas, type CreatePreset } from '@/features/canvas/CanvasContext'
import { PRIORITIES, PRIORITY_LABEL } from '@/lib/taskStatus'
import {
  LIMITS,
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
  type GoalFormValues,
  type ProjectFormValues,
  type TaskFormErrors,
  type TaskFormValues,
} from './taskForm'

const assignable = (agents: Agent[]) => agents.filter((a) => a.status !== 'retired' && a.status !== 'rejected').sort((a, b) => a.name.localeCompare(b.name))

function PrioritySelect({ id, value, onChange }: { id: string; value: Priority; onChange: (p: Priority) => void }) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value as Priority)}>
      {PRIORITIES.slice()
        .reverse()
        .map((p) => (
          <option key={p} value={p}>
            {PRIORITY_LABEL[p]}
          </option>
        ))}
    </Select>
  )
}

export function CreateTaskDialog({ companyId, preset, onClose }: { companyId: string; preset?: CreatePreset; onClose: () => void }) {
  const agents = useAgents(companyId)
  const projects = useProjects(companyId)
  const create = useCreateTask(companyId)
  const toast = useToast()
  const { openPanel } = useCanvas()
  const [values, setValues] = useState<TaskFormValues>(() =>
    emptyTaskForm({ project_id: preset?.project_id ?? '', assignee_agent_id: preset?.assignee_agent_id ?? '' }),
  )
  const [errors, setErrors] = useState<TaskFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)

  const patch = (p: Partial<TaskFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors((e) => {
      const next = { ...e }
      for (const k of Object.keys(p)) delete next[k as keyof TaskFormValues]
      return next
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateTaskForm(values)
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      const task = await create.mutateAsync(toCreateTaskBody(values, { parent_task_id: preset?.parent_task_id }))
      toast.success(`Task created: ${task.title}`)
      onClose()
      openPanel({ kind: 'task', id: task.id })
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped = mapTaskServerErrors(err.fieldErrors)
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  const list = assignable(agents.data ?? [])
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      size="lg"
      title="Create task"
      description="A task assigned to an active agent is dispatched automatically once its dependencies are done."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="create-task-form" loading={create.isPending}>
            Create task
          </Button>
        </>
      }
    >
      <form id="create-task-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Title" required error={errors.title}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus value={values.title} invalid={invalid} aria-describedby={describedBy} maxLength={LIMITS.taskTitle + 20} onChange={(e) => patch({ title: e.target.value })} />}
        </Field>
        <Field label="Description" error={errors.description}>
          {({ id, invalid, describedBy }) => <Textarea id={id} rows={3} value={values.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ description: e.target.value })} />}
        </Field>
        <Field label="Acceptance criteria" error={errors.acceptance_criteria} hint="When set, a reviewer checks the result before the task counts as done.">
          {({ id, invalid, describedBy }) => <Textarea id={id} rows={3} value={values.acceptance_criteria} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ acceptance_criteria: e.target.value })} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Project" error={errors.project_id}>
            {({ id, invalid }) => (
              <Select id={id} value={values.project_id} invalid={invalid} onChange={(e) => patch({ project_id: e.target.value })}>
                <option value="">No project</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Assignee" error={errors.assignee_agent_id}>
            {({ id, invalid }) => (
              <Select id={id} value={values.assignee_agent_id} invalid={invalid} onChange={(e) => patch({ assignee_agent_id: e.target.value })}>
                <option value="">Unassigned</option>
                {list.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.role_title}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Reviewer" error={errors.reviewer_agent_id}>
            {({ id, invalid }) => (
              <Select id={id} value={values.reviewer_agent_id} invalid={invalid} onChange={(e) => patch({ reviewer_agent_id: e.target.value })}>
                <option value="">Assignee&apos;s manager</option>
                {list.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Priority" error={errors.priority}>
            {({ id }) => <PrioritySelect id={id} value={values.priority} onChange={(priority) => patch({ priority })} />}
          </Field>
          <Field label="Budget (USD)" error={errors.budget} hint="Cap for this task. Leave blank for no per-task cap.">
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" placeholder="e.g. 2.50" value={values.budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ budget: e.target.value })} />}
          </Field>
        </div>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}

export function GoalDialog({ companyId, onClose }: { companyId: string; onClose: () => void }) {
  const agents = useAgents(companyId)
  const create = useCreateGoal(companyId)
  const toast = useToast()
  const [values, setValues] = useState<GoalFormValues>(emptyGoalForm)
  const [errors, setErrors] = useState<Partial<Record<keyof GoalFormValues, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const patch = (p: Partial<GoalFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateGoalForm(values)
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      const g = await create.mutateAsync(toCreateGoalBody(values))
      toast.success(`Goal created: ${g.title}`)
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        const mapped: typeof errors = {}
        for (const k of ['title', 'description', 'priority', 'status', 'owner_agent_id', 'target_date'] as const) if (fe[k]) mapped[k] = fe[k]
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else setFormError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      size="md"
      title="Create goal"
      description="Goals are outcomes the company plans against. The AI Co-founder breaks them into projects and tasks."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="create-goal-form" loading={create.isPending}>
            Create goal
          </Button>
        </>
      }
    >
      <form id="create-goal-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Title" required error={errors.title}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus value={values.title} invalid={invalid} aria-describedby={describedBy} maxLength={LIMITS.goalTitle + 20} onChange={(e) => patch({ title: e.target.value })} />}
        </Field>
        <Field label="Description" error={errors.description}>
          {({ id, invalid, describedBy }) => <Textarea id={id} rows={3} value={values.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ description: e.target.value })} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Priority">{({ id }) => <PrioritySelect id={id} value={values.priority} onChange={(priority) => patch({ priority })} />}</Field>
          <Field label="Status">
            {({ id }) => (
              <Select id={id} value={values.status} onChange={(e) => patch({ status: e.target.value as GoalFormValues['status'] })}>
                <option value="active">Active</option>
                <option value="proposed">Proposed</option>
              </Select>
            )}
          </Field>
          <Field label="Owner" hint="Defaults to the AI Co-founder.">
            {({ id }) => (
              <Select id={id} value={values.owner_agent_id} onChange={(e) => patch({ owner_agent_id: e.target.value })}>
                <option value="">AI Co-founder</option>
                {assignable(agents.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Target date" error={errors.target_date}>
            {({ id, invalid, describedBy }) => <Input id={id} type="date" value={values.target_date} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ target_date: e.target.value })} />}
          </Field>
        </div>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}

export function ProjectDialog({ companyId, preset, onClose }: { companyId: string; preset?: CreatePreset; onClose: () => void }) {
  const agents = useAgents(companyId)
  const goals = useGoals(companyId)
  const create = useCreateProject(companyId)
  const toast = useToast()
  const [values, setValues] = useState<ProjectFormValues>(() => emptyProjectForm({ goal_id: preset?.goal_id ?? '' }))
  const [errors, setErrors] = useState<Partial<Record<keyof ProjectFormValues, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const patch = (p: Partial<ProjectFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateProjectForm(values)
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      const p = await create.mutateAsync(toCreateProjectBody(values))
      toast.success(`Project created: ${p.name}`)
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        const mapped: typeof errors = {}
        if (fe.name) mapped.name = fe.name
        if (fe.description) mapped.description = fe.description
        if (fe.budget_micros) mapped.budget = fe.budget_micros
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else setFormError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      size="md"
      title="Create project"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="create-project-form" loading={create.isPending}>
            Create project
          </Button>
        </>
      }
    >
      <form id="create-project-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" required error={errors.name}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus value={values.name} invalid={invalid} aria-describedby={describedBy} maxLength={LIMITS.projectName + 20} onChange={(e) => patch({ name: e.target.value })} />}
        </Field>
        <Field label="Description" error={errors.description}>
          {({ id, invalid, describedBy }) => <Textarea id={id} rows={3} value={values.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ description: e.target.value })} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Goal">
            {({ id }) => (
              <Select id={id} value={values.goal_id} onChange={(e) => patch({ goal_id: e.target.value })}>
                <option value="">No goal</option>
                {(goals.data ?? []).map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.title}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Lead">
            {({ id }) => (
              <Select id={id} value={values.lead_agent_id} onChange={(e) => patch({ lead_agent_id: e.target.value })}>
                <option value="">No lead</option>
                {assignable(agents.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Budget (USD)" error={errors.budget} hint="Leave blank for no project cap.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ budget: e.target.value })} />}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
