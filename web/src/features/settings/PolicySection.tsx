import { useState } from 'react'
import { ApiError, errorMessage, usePolicy, useUpdatePolicy } from '@/api'
import type { CompanyPolicy } from '@/api/types'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { Boundary, Button, Card, CardBody, CardHeader, Field, FormError, Input, SectionLabel, Select, useToast } from '@/components/ui'
import { formatUsd } from '@/lib/money'
import {
  APPROVAL_KINDS,
  LIMIT_FIELDS,
  diffPolicy,
  policyToForm,
  validatePolicyForm,
  type PolicyFormErrors,
  type PolicyFormValues,
} from './policyForm'

/**
 * Autonomy and hard limits. These are enforced in code by the backend (not by prompts); only a human
 * owner/admin can change them, agents get 403.
 */
export function PolicySection({ companyId }: { companyId: string }) {
  const q = usePolicy(companyId)
  return (
    <Card>
      <CardHeader title="Policy & limits" description="Hard limits the company cannot exceed. Enforced by the backend. Agents can never change them." />
      <Boundary query={q} compact={false} rows={5} unavailableTitle="Policy is not built yet" unavailableText="Autonomy mode, budgets and limits come with the work engine. Until then nothing is enforced.">
        {(policy) => <PolicyForm key={policy.updated_at ?? JSON.stringify(policy)} companyId={companyId} policy={policy} />}
      </Boundary>
    </Card>
  )
}

function PolicyForm({ companyId, policy }: { companyId: string; policy: CompanyPolicy }) {
  const update = useUpdatePolicy(companyId)
  const toast = useToast()
  const [values, setValues] = useState<PolicyFormValues>(() => policyToForm(policy))
  const [errors, setErrors] = useState<PolicyFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const diff = diffPolicy(values, policy)
  const dirty = Object.keys(diff).length > 0

  const patch = (p: Partial<PolicyFormValues>) => {
    setValues((v) => ({ ...v, ...p }))
    setErrors({})
  }
  const setLimit = (key: keyof PolicyFormValues['limits'], value: string) => {
    setValues((v) => ({ ...v, limits: { ...v.limits, [key]: value } }))
    setErrors({})
  }
  const toggleKind = (kind: PolicyFormValues['human_approval_required_for'][number]) =>
    patch({
      human_approval_required_for: values.human_approval_required_for.includes(kind)
        ? values.human_approval_required_for.filter((k) => k !== kind)
        : [...values.human_approval_required_for, kind],
    })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validatePolicyForm(values)
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      await update.mutateAsync(diff)
      toast.success('Policy saved')
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        const mapped: PolicyFormErrors = {}
        for (const [k, msg] of Object.entries(fe)) mapped[k === 'daily_budget_micros' ? 'daily_budget' : k === 'monthly_budget_micros' ? 'monthly_budget' : k === 'default_run_reservation_micros' ? 'default_run_reservation' : k] = msg
        setErrors(mapped)
        setFormError(Object.keys(mapped).length ? 'Some fields need attention.' : err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <CardBody className="space-y-5">
        <section className="space-y-3">
          <SectionLabel>Autonomy</SectionLabel>
          <Field
            label="Autonomy mode"
            hint={
              values.autonomy_mode === 'autonomous'
                ? 'Agents decide and act on their own within the limits below. You are only asked about the items checked here.'
                : 'Supervised: agents ask more often and routine decisions need a human.'
            }
          >
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={values.autonomy_mode} onChange={(e) => patch({ autonomy_mode: e.target.value as PolicyFormValues['autonomy_mode'] })}>
                <option value="autonomous">Autonomous</option>
                <option value="supervised">Supervised</option>
              </Select>
            )}
          </Field>
          <fieldset>
            <legend className="mb-1 text-[12px] font-medium text-fg-2">Ask a human before the company does any of these</legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {APPROVAL_KINDS.map((k) => (
                <label key={k.kind} className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={values.human_approval_required_for.includes(k.kind)} onChange={() => toggleKind(k.kind)} />
                  <span>
                    <span className="block text-fg">{k.label}</span>
                    <span className="block text-[12px] text-fg-3">{k.hint}</span>
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-1.5 text-[12px] text-fg-3">
              {values.human_approval_required_for.length === 0 ? 'Nothing is reserved for you: the company is fully autonomous.' : 'Anything not checked is decided by the AI Founder.'}
            </p>
          </fieldset>
        </section>

        <section className="space-y-3">
          <SectionLabel>Budgets (USD)</SectionLabel>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Daily budget" error={errors.daily_budget} hint={`Now ${formatUsd(policy.daily_budget_micros, 'no limit')}. Blank = no limit.`}>
              {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.daily_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ daily_budget: e.target.value })} />}
            </Field>
            <Field label="Monthly budget" error={errors.monthly_budget} hint={`Now ${formatUsd(policy.monthly_budget_micros, 'no limit')}. Blank = no limit.`}>
              {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.monthly_budget} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ monthly_budget: e.target.value })} />}
            </Field>
            <Field label="Reserved per run" error={errors.default_run_reservation} hint="Held before a run whose model cost is unknown.">
              {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={values.default_run_reservation} invalid={invalid} aria-describedby={describedBy} onChange={(e) => patch({ default_run_reservation: e.target.value })} />}
            </Field>
          </div>
        </section>

        <section className="space-y-3">
          <SectionLabel>Limits</SectionLabel>
          <div className="grid gap-4 sm:grid-cols-2">
            {LIMIT_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} error={errors[f.key]} hint={f.hint}>
                {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={values.limits[f.key]} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setLimit(f.key, e.target.value)} />}
              </Field>
            ))}
          </div>
        </section>

        <section className="space-y-2">
          <SectionLabel>Model providers</SectionLabel>
          <StringListEditor values={values.allowed_model_providers} onChange={(next) => patch({ allowed_model_providers: next })} inputLabel="New model provider" placeholder="e.g. ollama" emptyText="Any provider is allowed." />
        </section>

        <FormError message={formError} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" disabled={!dirty || update.isPending} onClick={() => { setValues(policyToForm(policy)); setErrors({}); setFormError(null) }}>
            Reset
          </Button>
          <Button type="submit" variant="primary" loading={update.isPending} disabled={!dirty}>
            Save policy
          </Button>
        </div>
      </CardBody>
    </form>
  )
}
