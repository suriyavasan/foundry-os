import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateDepartment, useUpdateDepartment } from '@/api'
import type { Agent, Department } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, Select, Textarea, useToast } from '@/components/ui'

export const DEPARTMENT_NAME_MAX = 80

interface Values {
  name: string
  description: string
  lead_agent_id: string
}

export function validateDepartment(v: Values): Partial<Record<keyof Values, string>> {
  const e: Partial<Record<keyof Values, string>> = {}
  const name = v.name.trim()
  if (!name) e.name = 'Department name is required'
  else if (name.length > DEPARTMENT_NAME_MAX) e.name = `At most ${DEPARTMENT_NAME_MAX} characters`
  return e
}

/** Create (department omitted) or edit (department provided). Remount via `key` to reset. */
export function DepartmentDialog({
  open,
  onOpenChange,
  companyId,
  agents,
  department,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  companyId: string
  agents: Agent[]
  department?: Department
}) {
  const [values, setValues] = useState<Values>({
    name: department?.name ?? '',
    description: department?.description ?? '',
    lead_agent_id: department?.lead_agent_id ?? '',
  })
  const [errors, setErrors] = useState<Partial<Record<keyof Values, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const create = useCreateDepartment(companyId)
  const update = useUpdateDepartment(companyId)
  const toast = useToast()
  const pending = create.isPending || update.isPending
  const leads = agents.filter((a) => a.status !== 'retired' && a.status !== 'rejected')

  const set = (p: Partial<Values>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateDepartment(values)
    setErrors(ce)
    if (Object.keys(ce).length) return
    const body = {
      name: values.name.trim(),
      description: values.description.trim() || null,
      lead_agent_id: values.lead_agent_id || null,
    }
    try {
      if (department) await update.mutateAsync({ id: department.id, ...body })
      else await create.mutateAsync(body)
      toast.success(department ? `Updated ${body.name}` : `Created department ${body.name}`)
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        const mapped: typeof errors = {}
        if (fe.name) mapped.name = fe.name
        if (fe.description) mapped.description = fe.description
        if (fe.lead_agent_id) mapped.lead_agent_id = fe.lead_agent_id
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else if (err instanceof ApiError && err.status === 409) {
        setErrors({ name: err.message })
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
      title={department ? 'Edit department' : 'New department'}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="department-form" loading={pending}>
            {department ? 'Save' : 'Create department'}
          </Button>
        </>
      }
    >
      <form id="department-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" required error={errors.name} hint="Unique within the company.">
          {({ id, invalid, describedBy }) => (
            <Input id={id} autoFocus value={values.name} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ name: e.target.value })} />
          )}
        </Field>
        <Field label="Description" error={errors.description}>
          {({ id, invalid, describedBy }) => (
            <Textarea id={id} rows={2} value={values.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ description: e.target.value })} />
          )}
        </Field>
        <Field label="Lead" error={errors.lead_agent_id}>
          {({ id, invalid }) => (
            <Select id={id} value={values.lead_agent_id} invalid={invalid} onChange={(e) => set({ lead_agent_id: e.target.value })}>
              <option value="">No lead</option>
              {leads.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.role_title}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
