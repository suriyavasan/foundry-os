import { TriangleAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateGuardrailRule, useUpdateGuardrailRule } from '@/api'
import type { GuardrailRule } from '@/api/types'
import { StringListEditor } from '@/components/forms/StringListEditor'
import { Button, Dialog, Field, FormError, Input, Select, useToast } from '@/components/ui'
import { useAgentMap } from '@/features/canvas/hooks'
import { DECISION_HELP, DECISION_LABEL, DECISION_ORDER, TIERS, TIER_EXAMPLES, TIER_LABEL, defaultFor, directionVsDefault } from '@/lib/guardrails'
import { ROLE_FAMILIES, ROLE_FAMILY_LABEL } from '@/lib/roleFamily'
import { emptyRuleForm, formToRequest, ruleToForm, validateRuleForm, type RuleFormErrors, type RuleFormValues } from './ruleForm'

/** Add or edit one rule. Owner/admin only (the caller does not render it for anyone else; the server refuses agents regardless). */
export function RuleDialog({ companyId, rule, onClose }: { companyId: string; rule?: GuardrailRule; onClose: () => void }) {
  const create = useCreateGuardrailRule(companyId)
  const update = useUpdateGuardrailRule(companyId)
  const { agents } = useAgentMap(companyId)
  const toast = useToast()
  const [v, setV] = useState<RuleFormValues>(() => (rule ? ruleToForm(rule) : emptyRuleForm()))
  const [errors, setErrors] = useState<RuleFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const pending = create.isPending || update.isPending
  const set = (p: Partial<RuleFormValues>) => {
    setV((x) => ({ ...x, ...p }))
    setErrors({})
  }

  const direction = v.tier ? directionVsDefault(v.tier, v.decision) : 'same'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateRuleForm(v)
    setErrors(ce)
    if (Object.keys(ce).length) return
    const body = formToRequest(v)
    try {
      if (rule) await update.mutateAsync({ id: rule.id, ...body })
      else await create.mutateAsync(body)
      toast.success(rule ? 'Rule updated' : 'Rule added')
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped: RuleFormErrors = {}
        for (const [k, msg] of Object.entries(err.fieldErrors)) {
          if (k === 'tier') mapped.tier = msg
          else if (k === 'action') mapped.action = msg
          else if (k.startsWith('scope')) mapped[v.scopeType === 'agent' ? 'agentId' : 'family'] = msg
          else if (k.includes('max_amount')) mapped.maxAmount = msg
          else if (k.includes('max_per_day')) mapped.maxPerDay = msg
          else if (k.includes('allowed_domains')) mapped.domains = msg
        }
        setErrors(mapped)
        setFormError(Object.keys(mapped).length ? 'Some fields need attention.' : err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !pending && onClose()}
      title={rule ? 'Edit guardrail rule' : 'Add guardrail rule'}
      description="Company policy. Only a human owner or admin can change it; agents never can."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" form="guardrail-rule-form" variant="primary" loading={pending}>
            {rule ? 'Save rule' : 'Add rule'}
          </Button>
        </>
      }
    >
      <form id="guardrail-rule-form" onSubmit={(e) => void submit(e)} noValidate className="space-y-4">
        <fieldset className="space-y-3">
          <legend className="mb-1 text-[12px] font-medium text-fg-2">What it covers</legend>
          <div className="flex gap-2" role="radiogroup" aria-label="Rule target">
            {(['tier', 'action'] as const).map((k) => (
              <label key={k} className={`flex flex-1 cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-[13px] ${v.targetKind === k ? 'border-accent bg-accent-subtle text-fg' : 'border-line bg-raised text-fg-2 hover:bg-hover'}`}>
                <input type="radio" name="target" className="size-3.5 accent-(--accent)" checked={v.targetKind === k} onChange={() => set({ targetKind: k })} />
                {k === 'tier' ? 'A whole tier' : 'One specific action'}
              </label>
            ))}
          </div>
          {v.targetKind === 'action' && (
            <Field label="Action" required error={errors.action} hint="The name of the action or tool, e.g. email.send. A rule for an exact action wins over a rule for its tier.">
              {({ id, invalid, describedBy }) => <Input id={id} value={v.action} invalid={invalid} aria-describedby={describedBy} className="font-mono" placeholder="email.send" onChange={(e) => set({ action: e.target.value })} />}
            </Field>
          )}
          <Field label={v.targetKind === 'action' ? 'Tier of the action' : 'Tier'} required error={errors.tier} hint={v.tier ? TIER_EXAMPLES[v.tier] : undefined}>
            {({ id, invalid, describedBy }) => (
              <Select id={id} value={v.tier} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ tier: e.target.value as RuleFormValues['tier'] })}>
                <option value="">Choose a tier</option>
                {TIERS.map((t) => (
                  <option key={t} value={t}>
                    {TIER_LABEL[t]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="mb-1 text-[12px] font-medium text-fg-2">Who it applies to</legend>
          <Field label="Scope" hint="The narrower scope wins: an agent over its family, a family over all agents.">
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={v.scopeType} onChange={(e) => set({ scopeType: e.target.value as RuleFormValues['scopeType'] })}>
                <option value="all">All agents</option>
                <option value="family">A role family</option>
                <option value="agent">One agent</option>
              </Select>
            )}
          </Field>
          {v.scopeType === 'family' && (
            <Field label="Role family" required error={errors.family}>
              {({ id, invalid, describedBy }) => (
                <Select id={id} value={v.family} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ family: e.target.value })}>
                  <option value="">Choose a family</option>
                  {ROLE_FAMILIES.map((f) => (
                    <option key={f} value={f}>
                      {ROLE_FAMILY_LABEL[f]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          {v.scopeType === 'agent' && (
            <Field label="Agent" required error={errors.agentId}>
              {({ id, invalid, describedBy }) => (
                <Select id={id} value={v.agentId} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ agentId: e.target.value })}>
                  <option value="">Choose an agent</option>
                  {agents
                    .filter((a) => a.status !== 'retired' && a.status !== 'rejected')
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} · {a.role_title}
                      </option>
                    ))}
                </Select>
              )}
            </Field>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="mb-1 text-[12px] font-medium text-fg-2">Decision</legend>
          <Field label="What happens" hint={DECISION_HELP[v.decision]}>
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={v.decision} onChange={(e) => set({ decision: e.target.value as RuleFormValues['decision'] })}>
                {DECISION_ORDER.map((d) => (
                  <option key={d} value={d}>
                    {DECISION_LABEL[d]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {direction === 'loosens' && v.tier && (
            <div role="status" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-fg">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <span>
                More permissive than the built-in default for {TIER_LABEL[v.tier]} ({DECISION_LABEL[defaultFor(v.tier)!.decision]}). Agents will be able to do this with less oversight.
              </span>
            </div>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="mb-1 text-[12px] font-medium text-fg-2">Conditions (optional)</legend>
          <p className="text-[12px] text-fg-3">The rule applies only while the conditions hold; the server evaluates them when the action runs. Leave blank for none.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Amount up to (USD)" error={errors.maxAmount}>
              {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={v.maxAmount} invalid={invalid} aria-describedby={describedBy} placeholder="no limit" onChange={(e) => set({ maxAmount: e.target.value })} />}
            </Field>
            <Field label="At most per day" error={errors.maxPerDay}>
              {({ id, invalid, describedBy }) => <Input id={id} inputMode="numeric" value={v.maxPerDay} invalid={invalid} aria-describedby={describedBy} placeholder="no limit" onChange={(e) => set({ maxPerDay: e.target.value })} />}
            </Field>
          </div>
          <div>
            <div className="mb-1 text-[12px] font-medium text-fg-2">Allowed domains</div>
            <StringListEditor values={v.domains} onChange={(domains) => set({ domains })} inputLabel="New allowed domain" placeholder="example.com" addLabel="Add domain" emptyText="Any domain." />
            {errors.domains && (
              <p role="alert" className="mt-1 text-[12px] text-danger">
                {errors.domains}
              </p>
            )}
          </div>
        </fieldset>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
