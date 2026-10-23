import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ApiError, errorMessage, useCreateAgent, useRuntimes } from '@/api'
import type { Agent, Department } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, useToast } from '@/components/ui'
import {
  emptyAgentForm,
  mapAgentServerErrors,
  toCreateAgentBody,
  validateAgentForm,
  type AgentFormErrors,
  type AgentFormValues,
} from './agentForm'
import { AgentFormFields } from './AgentFormFields'

export function HireDialog({
  open,
  onOpenChange,
  companyId,
  agents,
  departments,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  companyId: string
  agents: Agent[]
  departments: Department[]
}) {
  const founder = agents.find((a) => a.kind === 'founder' && a.status === 'active')
  const [values, setValues] = useState<AgentFormValues>(() => emptyAgentForm({ manager_agent_id: founder?.id ?? '' }))
  const [errors, setErrors] = useState<AgentFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const create = useCreateAgent(companyId)
  const runtimes = useRuntimes()
  const toast = useToast()
  const navigate = useNavigate()

  const patch = (p: Partial<AgentFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors((e) => {
      const next = { ...e }
      for (const k of Object.keys(p)) delete next[k as keyof AgentFormValues]
      return next
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const clientErrors = validateAgentForm(values, { budgets: true })
    setErrors(clientErrors)
    if (Object.keys(clientErrors).length) return
    try {
      const agent = await create.mutateAsync(toCreateAgentBody(values))
      toast.success(`${agent.name} proposed. Activate them from their profile.`)
      onOpenChange(false)
      navigate(`/c/${companyId}/organization/agents/${agent.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped = mapAgentServerErrors(err.fieldErrors)
        setErrors(mapped)
        if (!Object.keys(mapped).length) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title="Propose hire"
      description="Creates a proposed agent. It does nothing until you activate it."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="hire-form" loading={create.isPending}>
            Propose hire
          </Button>
        </>
      }
    >
      <form id="hire-form" onSubmit={submit} noValidate className="space-y-4">
        <AgentFormFields
          showKind
          values={values}
          errors={errors}
          onChange={patch}
          departments={departments}
          agents={agents}
          runtimes={runtimes.data?.items}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Monthly budget (USD)" error={errors.monthly_budget} hint="Leave blank for no limit.">
            {({ id, invalid, describedBy }) => (
              <Input id={id} inputMode="decimal" placeholder="e.g. 50" value={values.monthly_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ monthly_budget: e.target.value })} />
            )}
          </Field>
          <Field label="Daily budget (USD)" error={errors.daily_budget} hint="Leave blank for no limit.">
            {({ id, invalid, describedBy }) => (
              <Input id={id} inputMode="decimal" placeholder="e.g. 5" value={values.daily_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ daily_budget: e.target.value })} />
            )}
          </Field>
        </div>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
