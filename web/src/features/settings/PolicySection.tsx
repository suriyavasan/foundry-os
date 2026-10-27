import { useState } from 'react'
import { ApiError, errorMessage, useMemberDirectory, usePolicy, useUpdatePolicy } from '@/api'
import type { CompanyPolicy } from '@/api/types'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { Boundary, Button, Card, CardBody, CardHeader, Field, FormError, Input, SectionLabel, Select, useToast } from '@/components/ui'
import { formatUsd } from '@/lib/money'
import {
  APPROVAL_KINDS,
  LIMIT_FIELDS,
  M12_NUMBER_FIELDS,
  M15_MONEY_FIELDS,
  M15_NUMBER_FIELDS,
  MODEL_ROUTING_OPTIONS,
  ROLE_FAMILIES,
  diffPolicy,
  hasM12Fields,
  hasM15Fields,
  hasM6Fields,
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
  const setM6 = (p: Partial<PolicyFormValues['m6']>) => {
    setValues((v) => ({ ...v, m6: { ...v.m6, ...p } }))
    setErrors({})
  }
  const setM12 = (p: Partial<PolicyFormValues['m12']>) => {
    setValues((v) => ({ ...v, m12: { ...v.m12, ...p } }))
    setErrors({})
  }
  const setM15 = (p: Partial<PolicyFormValues['m15']>) => {
    setValues((v) => ({ ...v, m15: { ...v.m15, ...p } }))
    setErrors({})
  }
  const members = useMemberDirectory(companyId)
  const m6 = values.m6
  const m12 = values.m12
  const m15 = values.m15
  const toggleFamily = (family: (typeof ROLE_FAMILIES)[number]['value']) => {
    const cur = m12.cli_role_families ?? []
    setM12({ cli_role_families: cur.includes(family) ? cur.filter((f) => f !== family) : [...cur, family] })
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
    const ce = validatePolicyForm(values, policy)
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
              {values.human_approval_required_for.length === 0 ? 'Nothing is reserved for you: the company is fully autonomous.' : 'Anything not checked is decided by the AI Co-founder.'}
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

        {hasM6Fields(m6) && (
          <section className="space-y-3" aria-label="Spend governance and model routing">
            <SectionLabel>Spend governance &amp; model routing</SectionLabel>
            <div className="grid gap-4 sm:grid-cols-2">
              {m6.max_agent_monthly_budget_micros !== undefined && (
                <Field label="Max monthly budget per agent (USD)" error={errors.max_agent_monthly_budget_micros} hint={`Agents can never be given more than this. Now ${formatUsd(policy.max_agent_monthly_budget_micros, 'no cap')}. Blank = no cap.`}>
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m6.max_agent_monthly_budget_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM6({ max_agent_monthly_budget_micros: e.target.value })} />}
                </Field>
              )}
              {m6.hire_default_monthly_budget_micros !== undefined && (
                <Field label="Default monthly budget for new hires (USD)" error={errors.hire_default_monthly_budget_micros} hint={`Given to a hire that arrives without one. Now ${formatUsd(policy.hire_default_monthly_budget_micros, 'none')}. Blank = none.`}>
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m6.hire_default_monthly_budget_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM6({ hire_default_monthly_budget_micros: e.target.value })} />}
                </Field>
              )}
              {m6.auto_spend_limit_micros !== undefined && (
                <Field label="Automatic spend limit (USD)" error={errors.auto_spend_limit_micros} hint="Purchases above this need a human; up to this, the AI Co-founder approves. 0 means a human approves every purchase.">
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m6.auto_spend_limit_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM6({ auto_spend_limit_micros: e.target.value })} />}
                </Field>
              )}
              {m6.budget_alert_thresholds !== undefined && (
                <Field label="Budget alert thresholds (%)" error={errors.budget_alert_thresholds} hint="Raise an alert when a budget reaches each of these percentages, e.g. 50, 80, 100.">
                  {({ id, invalid, describedBy }) => <Input id={id} value={m6.budget_alert_thresholds} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM6({ budget_alert_thresholds: e.target.value })} />}
                </Field>
              )}
              {m6.model_routing !== undefined && (
                <Field label="Model routing" error={errors.model_routing} hint={MODEL_ROUTING_OPTIONS.find((o) => o.value === m6.model_routing)?.hint}>
                  {({ id, invalid, describedBy }) => (
                    <Select id={id} invalid={invalid} aria-describedby={describedBy} value={m6.model_routing} onChange={(e) => setM6({ model_routing: e.target.value as typeof m6.model_routing })}>
                      {MODEL_ROUTING_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
              )}
              {m6.stall_seconds !== undefined && (
                <Field label="Stall detection (seconds)" error={errors.stall_seconds} hint="A run with no progress for this long is cancelled and counts toward the circuit breaker.">
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={m6.stall_seconds} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM6({ stall_seconds: e.target.value })} />}
                </Field>
              )}
            </div>
            {m6.allow_paid_fallback !== undefined && (
              <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m6.allow_paid_fallback} onChange={(e) => setM6({ allow_paid_fallback: e.target.checked })} />
                <span>
                  <span className="block text-fg">Allow a paid model as a fallback</span>
                  <span className="block text-[12px] text-fg-3">When no free model can do the job, route to a paid one (still within the budgets above).</span>
                </span>
              </label>
            )}
          </section>
        )}

        {hasM12Fields(m12) && (
          <section className="space-y-3" aria-label="Full autonomy">
            <SectionLabel>Full autonomy</SectionLabel>
            <p className="text-[12px] text-fg-3">How the company keeps itself busy, how work is split up, and which work may use a coding CLI. Only you can change these; agents never can.</p>
            {m12.cli_role_families !== undefined && (
              <fieldset>
                <legend className="mb-1 text-[12px] font-medium text-fg-2">Role families that may use a coding CLI (hybrid mode)</legend>
                <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-3.5 accent-(--accent)"
                    checked={m12.cli_role_families === null}
                    onChange={(e) => setM12({ cli_role_families: e.target.checked ? null : ROLE_FAMILIES.map((f) => f.value) })}
                  />
                  <span>
                    <span className="block text-fg">Every role family (default)</span>
                    <span className="block text-[12px] text-fg-3">Work tasks of any role may run on the coding CLI chosen in AI settings.</span>
                  </span>
                </label>
                {m12.cli_role_families !== null && (
                  <div className="mt-1.5 grid gap-1.5 sm:grid-cols-3" role="group" aria-label="Role families">
                    {ROLE_FAMILIES.map((f) => (
                      <label key={f.value} className="flex cursor-pointer items-center gap-2 rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px] hover:bg-hover">
                        <input type="checkbox" className="size-3.5 accent-(--accent)" checked={m12.cli_role_families?.includes(f.value) ?? false} onChange={() => toggleFamily(f.value)} />
                        <span className="text-fg">{f.label}</span>
                      </label>
                    ))}
                  </div>
                )}
                <p className="mt-1.5 text-[12px] text-fg-3">
                  {m12.cli_role_families === null
                    ? 'Untick it to narrow the list; the other roles then work through the model API, which has web tools.'
                    : m12.cli_role_families.length === 0
                      ? 'No role family uses the coding CLI: all work stays on the model API.'
                      : 'Other role families stay on the model API, which has web tools (research, for example).'}
                </p>
              </fieldset>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              {m12.cli_run_reservation_micros !== undefined && (
                <Field
                  label="Reserved per coding CLI run (USD)"
                  error={errors.cli_run_reservation_micros}
                  hint={`Held before a coding CLI run; its own spend cap derives from it. Now ${formatUsd(policy.cli_run_reservation_micros, 'not set')}.`}
                >
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m12.cli_run_reservation_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM12({ cli_run_reservation_micros: e.target.value })} />}
                </Field>
              )}
              {M12_NUMBER_FIELDS.filter((f) => m12[f.key] !== undefined).map((f) => (
                <Field key={f.key} label={f.label} error={errors[f.key]} hint={f.hint}>
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={m12[f.key]} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM12({ [f.key]: e.target.value })} />}
                </Field>
              ))}
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {m12.initiative_enabled !== undefined && (
                <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m12.initiative_enabled} onChange={(e) => setM12({ initiative_enabled: e.target.checked })} />
                  <span>
                    <span className="block text-fg">Start new work on its own</span>
                    <span className="block text-[12px] text-fg-3">When the company is idle, the AI Co-founder looks for the next goal and project (within your budgets).</span>
                  </span>
                </label>
              )}
              {m12.project_auto_complete !== undefined && (
                <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m12.project_auto_complete} onChange={(e) => setM12({ project_auto_complete: e.target.checked })} />
                  <span>
                    <span className="block text-fg">Complete finished projects automatically</span>
                    <span className="block text-[12px] text-fg-3">A project whose work tasks are all finished (at least one done) is closed without waiting for a person.</span>
                  </span>
                </label>
              )}
            </div>
          </section>
        )}

        {hasM15Fields(m15) && (
          <section className="space-y-3" aria-label="Purchasing">
            <SectionLabel>Purchasing</SectionLabel>
            <p className="text-[12px] text-fg-3">
              When an agent wants to buy something. Three bands decide who approves: automatic, the AI Co-founder, or you. The ceilings below are enforced by the backend; only you can change any of this, agents never can.
            </p>
            {m15.purchasing_enabled !== undefined && (
              <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m15.purchasing_enabled} onChange={(e) => setM15({ purchasing_enabled: e.target.checked })} />
                <span>
                  <span className="block text-fg">Let agents request purchases</span>
                  <span className="block text-[12px] text-fg-3">Off: every purchase request becomes a task for you, and no card is issued.</span>
                </span>
              </label>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              {M15_MONEY_FIELDS.filter((f) => m15[f.key] !== undefined).map((f) => (
                <Field key={f.key} label={f.label} error={errors[f.key]} hint={f.hint}>
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m15[f.key]} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM15({ [f.key]: e.target.value })} />}
                </Field>
              ))}
              {m15.purchase_monthly_budget_micros !== undefined && (
                <Field label="Purchase budget per month (USD)" error={errors.purchase_monthly_budget_micros} hint="A hard cap on what is committed plus charged each month. Separate from model budgets. Blank = no cap.">
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m15.purchase_monthly_budget_micros} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM15({ purchase_monthly_budget_micros: e.target.value })} />}
                </Field>
              )}
              {m15.purchase_tolerance_percent !== undefined && (
                <Field label="Tax and FX allowance (%)" error={errors.purchase_tolerance_percent} hint="The card limit is the amount plus this percentage (or the minimum above, whichever is larger).">
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={m15.purchase_tolerance_percent} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM15({ purchase_tolerance_percent: e.target.value })} />}
                </Field>
              )}
              {M15_NUMBER_FIELDS.filter((f) => m15[f.key] !== undefined).map((f) => (
                <Field key={f.key} label={f.label} error={errors[f.key]} hint={f.hint}>
                  {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={m15[f.key]} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setM15({ [f.key]: e.target.value })} />}
                </Field>
              ))}
              {m15.purchase_checkout_member_id !== undefined && (
                <Field label="Who gets checkout tasks" hint="The person who pays when a purchase needs a human at checkout.">
                  {({ id, describedBy }) => (
                    <Select id={id} aria-describedby={describedBy} value={m15.purchase_checkout_member_id} onChange={(e) => setM15({ purchase_checkout_member_id: e.target.value })}>
                      <option value="">The owner (default)</option>
                      {members.members
                        .filter((m) => m.status === 'active' && (m.role === 'owner' || m.role === 'admin'))
                        .map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.display_name}
                          </option>
                        ))}
                    </Select>
                  )}
                </Field>
              )}
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {m15.purchase_recurring_requires_human !== undefined && (
                <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m15.purchase_recurring_requires_human} onChange={(e) => setM15({ purchase_recurring_requires_human: e.target.checked })} />
                  <span>
                    <span className="block text-fg">Starting a subscription needs me</span>
                    <span className="block text-[12px] text-fg-3">The first payment of any recurring purchase goes to you, whatever its size.</span>
                  </span>
                </label>
              )}
              {m15.purchase_new_vendor_requires_human !== undefined && (
                <label className="flex cursor-pointer items-start gap-2 rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] hover:bg-hover">
                  <input type="checkbox" className="mt-0.5 size-3.5 accent-(--accent)" checked={m15.purchase_new_vendor_requires_human} onChange={(e) => setM15({ purchase_new_vendor_requires_human: e.target.checked })} />
                  <span>
                    <span className="block text-fg">A new vendor needs me</span>
                    <span className="block text-[12px] text-fg-3">The first purchase from any website goes to you.</span>
                  </span>
                </label>
              )}
            </div>
          </section>
        )}

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
