import type { Agent, AgentKind, Department, RuntimeInfo } from '@/api/types'
import { Field, Input, Select, Textarea } from '@/components/ui'
import { UNASSIGNED_RUNTIME, managerCandidates, type AgentFormErrors, type AgentFormValues } from './agentForm'

const KIND_LABEL: Record<AgentKind, string> = {
  founder: 'Founder',
  lead: 'Lead',
  specialist: 'Specialist',
  reviewer: 'Reviewer',
}

export function runtimeOptions(runtimes: RuntimeInfo[] | undefined, current?: string): string[] {
  const ids = new Set<string>([UNASSIGNED_RUNTIME])
  runtimes?.forEach((r) => ids.add(r.id))
  if (current) ids.add(current)
  return [...ids]
}

/** Shared by the "Propose hire" dialog and the profile Configuration tab. */
export function AgentFormFields({
  values,
  errors,
  onChange,
  departments,
  agents,
  runtimes,
  selfId,
  disabled,
  showKind,
}: {
  values: AgentFormValues
  errors: AgentFormErrors
  onChange: (patch: Partial<AgentFormValues>) => void
  departments: Department[]
  agents: Agent[]
  runtimes: RuntimeInfo[] | undefined
  selfId?: string
  disabled?: boolean
  showKind?: boolean
}) {
  const managers = managerCandidates(agents, selfId)
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" required error={errors.name}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} value={values.name} disabled={disabled} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange({ name: e.target.value })} />
          )}
        </Field>
        <Field label="Role title" required error={errors.role_title}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} value={values.role_title} disabled={disabled} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. Head of Research" onChange={(e) => onChange({ role_title: e.target.value })} />
          )}
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {showKind && (
          <Field label="Kind" error={errors.kind}>
            {({ id, invalid }) => (
              <Select id={id} value={values.kind} invalid={invalid} disabled={disabled} onChange={(e) => onChange({ kind: e.target.value as AgentKind })}>
                {(['lead', 'specialist', 'reviewer'] as AgentKind[]).map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Field label="Department" error={errors.department_id}>
          {({ id, invalid }) => (
            <Select id={id} value={values.department_id} invalid={invalid} disabled={disabled} onChange={(e) => onChange({ department_id: e.target.value })}>
              <option value="">No department</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Manager" error={errors.manager_agent_id}>
          {({ id, invalid }) => (
            <Select id={id} value={values.manager_agent_id} invalid={invalid} disabled={disabled} onChange={(e) => onChange({ manager_agent_id: e.target.value })}>
              <option value="">No manager</option>
              {managers.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.role_title}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <Field label="Responsibilities" error={errors.responsibilities} hint="What this agent owns.">
        {({ id, invalid, describedBy }) => (
          <Textarea id={id} rows={3} value={values.responsibilities} disabled={disabled} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange({ responsibilities: e.target.value })} />
        )}
      </Field>
      <Field label="Instructions" error={errors.instructions} hint="Guidance for the agent. Never put secrets here.">
        {({ id, invalid, describedBy }) => (
          <Textarea id={id} rows={4} value={values.instructions} disabled={disabled} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange({ instructions: e.target.value })} />
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Model provider" error={errors.model_provider}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} value={values.model_provider} disabled={disabled} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. anthropic" onChange={(e) => onChange({ model_provider: e.target.value })} />
          )}
        </Field>
        <Field label="Model name" error={errors.model_name}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} value={values.model_name} disabled={disabled} invalid={invalid} aria-describedby={describedBy} onChange={(e) => onChange({ model_name: e.target.value })} />
          )}
        </Field>
        <Field label="Runtime" error={errors.runtime}>
          {({ id, invalid }) => (
            <Select id={id} value={values.runtime} invalid={invalid} disabled={disabled} onChange={(e) => onChange({ runtime: e.target.value })}>
              {runtimeOptions(runtimes, values.runtime).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    </div>
  )
}
