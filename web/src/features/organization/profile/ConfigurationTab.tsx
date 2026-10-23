import { AlertTriangle } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useModelProviders, useRuntimes } from '@/api'
import type { Agent, Department } from '@/api/types'
import { KeyValueEditor, rowsFromObject, rowsToObject, type KVRow } from '@/components/forms/KeyValueEditor'
import { Button, Card, CardBody, CardHeader, FormError } from '@/components/ui'
import {
  agentToForm,
  mapAgentServerErrors,
  toUpdateAgentBody,
  validateAgentForm,
  type AgentFormErrors,
  type AgentFormValues,
} from '../agentForm'
import { AgentFormFields } from '../AgentFormFields'
import { useAgentSave } from './useAgentSave'

export function ConfigurationTab({ agent, agents, departments }: { agent: Agent; agents: Agent[]; departments: Department[] }) {
  const readOnly = agent.status === 'retired'
  const runtimes = useRuntimes()
  const providers = useModelProviders(agent.company_id)
  const { save, saving, error, fieldErrors } = useAgentSave(agent)
  const [values, setValues] = useState<AgentFormValues>(() => agentToForm(agent))
  const [rows, setRows] = useState<KVRow[]>(() => rowsFromObject(agent.runtime_config))
  const [clientErrors, setClientErrors] = useState<AgentFormErrors>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})

  const errors: AgentFormErrors = { ...mapAgentServerErrors(fieldErrors), ...clientErrors }
  const configServerError = Object.entries(fieldErrors).find(([k]) => k.startsWith('runtime_config'))?.[1]

  const patch = (p: Partial<AgentFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setClientErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ce = validateAgentForm(values, { budgets: false })
    const parsed = rowsToObject(rows)
    setClientErrors(ce)
    setRowErrors(parsed.errors)
    if (Object.keys(ce).length || Object.keys(parsed.errors).length) return
    await save({ ...toUpdateAgentBody(values), runtime_config: parsed.value }, 'Configuration saved')
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {readOnly && (
        <div className="rounded-md border border-line bg-surface px-3 py-2 text-[13px] text-fg-2">
          Retired agents are read-only.
        </div>
      )}
      <Card>
        <CardHeader title="Profile" />
        <CardBody>
          <AgentFormFields
            values={values}
            errors={errors}
            onChange={patch}
            departments={departments}
            agents={agents}
            runtimes={runtimes.data?.items}
            providers={providers.data}
            selfId={agent.id}
            disabled={readOnly}
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Runtime configuration" description="Non-secret settings passed to the runtime." />
        <CardBody className="space-y-3">
          <div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-fg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
            <p>
              Do not put secrets here (API keys, tokens, passwords). Runtime config is visible to the agent and the server rejects
              keys that look like secrets. Credentials are managed separately.
            </p>
          </div>
          <KeyValueEditor rows={rows} onChange={(r) => { setRows(r); setRowErrors({}) }} errors={rowErrors} disabled={readOnly} />
          {configServerError && <p role="alert" className="text-[12px] text-danger">{configServerError}</p>}
          <p className="text-[12px] text-fg-3">Values are text unless the existing value is not a string, in which case it is edited as JSON.</p>
        </CardBody>
      </Card>
      <FormError message={error} />
      <div className="flex justify-end">
        <Button
          type="submit"
          variant="primary"
          loading={saving}
          disabled={readOnly}
        >
          Save configuration
        </Button>
      </div>
    </form>
  )
}
