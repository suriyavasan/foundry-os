import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateStandard, useUpdateStandard } from '@/api'
import type { CompanyStandard } from '@/api/types'
import { Button, Dialog, Field, FormError, Input, Textarea, useToast } from '@/components/ui'
import { STANDARD_STATEMENT_MAX, STANDARD_TITLE_MAX, emptyStandardForm, toStandardBody, validateStandardForm, type StandardForm, type StandardFormErrors } from '@/lib/reflections'

/** Write a standard, or edit one. Only a person gets here: the AI Co-founder has no such control. */
export function StandardDialog({ companyId, standard, onClose }: { companyId: string; standard: CompanyStandard | null; onClose: () => void }) {
  const create = useCreateStandard(companyId)
  const update = useUpdateStandard(companyId)
  const toast = useToast()
  const [form, setForm] = useState<StandardForm>(() => emptyStandardForm(standard ?? undefined))
  const [errors, setErrors] = useState<StandardFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const busy = create.isPending || update.isPending
  const patch = (p: Partial<StandardForm>) => {
    setForm((f) => ({ ...f, ...p }))
    setErrors((e) => ({ ...e, ...Object.fromEntries(Object.keys(p).map((k) => [k, undefined])) }))
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const found = validateStandardForm(form)
    setErrors(found)
    if (Object.keys(found).length) return
    try {
      const body = toStandardBody(form)
      if (standard) await update.mutateAsync({ id: standard.id, body: { ...body, applies_to: body.applies_to ?? [] } })
      else await create.mutateAsync(body)
      toast.success(standard ? 'Standard updated' : 'Standard added')
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const f = err.fieldErrors
        const mapped: StandardFormErrors = { title: f.title, statement: f.statement }
        setErrors(mapped)
        if (!mapped.title && !mapped.statement) setFormError(err.message)
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={standard ? 'Edit standard' : 'New standard'}
      description="A rule every agent is given with its work. Keep it to one or two clear sentences."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="standard-form" loading={busy}>
            {standard ? 'Save' : 'Add standard'}
          </Button>
        </>
      }
    >
      <form id="standard-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Title" required error={errors.title}>
          {({ id, invalid, describedBy }) => (
            <Input id={id} autoFocus maxLength={STANDARD_TITLE_MAX + 20} value={form.title} invalid={invalid} aria-describedby={describedBy} placeholder="e.g. Cite every statistic" onChange={(e) => patch({ title: e.target.value })} />
          )}
        </Field>
        <Field label="The rule" required error={errors.statement} hint={`${form.statement.trim().length} / ${STANDARD_STATEMENT_MAX} characters`}>
          {({ id, invalid, describedBy }) => (
            <Textarea id={id} rows={4} value={form.statement} invalid={invalid} aria-describedby={describedBy} placeholder="Every number in a deliverable names its source." onChange={(e) => patch({ statement: e.target.value })} />
          )}
        </Field>
        <Field label="Applies to" hint="Optional. Role families or tags separated by commas, e.g. research, marketing. Empty means everyone.">
          {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} value={form.appliesTo} onChange={(e) => patch({ appliesTo: e.target.value })} />}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
