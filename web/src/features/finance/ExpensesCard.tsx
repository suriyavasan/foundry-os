import { Plus } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useCreateExpense, useExpenses, useProjects } from '@/api'
import { Boundary, Button, Card, CardHeader, Dialog, Field, FormError, Input, Select, TBody, TD, TH, THead, TR, Table, Textarea, useToast } from '@/components/ui'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { formatUsd } from '@/lib/money'
import { formatDay } from '@/lib/finance'
import { DESCRIPTION_MAX, RECURRENCE_LABEL, VENDOR_MAX, emptyExpenseForm, expenseToRequest, validateExpense, type ExpenseFormErrors, type ExpenseFormValues } from './expenseForm'

/** Money a human recorded as spent outside model usage (domains, SaaS, contractors...). Recorded amounts, not estimates. */
export function ExpensesCard({ companyId, from, to, today }: { companyId: string; from: string; to: string; today: string }) {
  const q = useExpenses(companyId, from, to)
  const projects = useProjects(companyId)
  const role = useCompanyRole(companyId)
  const [adding, setAdding] = useState(false)
  const projectName = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.name])), [projects.data])
  const canAdd = canEditPolicy(role) && !q.isError
  return (
    <Card>
      <CardHeader
        title="External expenses"
        description="Money spent outside model usage, recorded by a human. Shown separately from agent spend."
        actions={
          canAdd ? (
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => setAdding(true)}>
              Add expense
            </Button>
          ) : undefined
        }
      />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        unavailableTitle="Expenses are not built yet"
        unavailableText="External expense tracking comes with guardrails & governance."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No expenses in this period', description: canAdd ? 'Record a domain, subscription or contractor payment.' : 'Nothing has been recorded for these dates.' }}
      >
        {(items) => (
          <Table>
            <THead>
              <TR>
                <TH>Date</TH>
                <TH>Vendor</TH>
                <TH>Description</TH>
                <TH>Project</TH>
                <TH>Recurs</TH>
                <TH className="text-right">Amount</TH>
              </TR>
            </THead>
            <TBody>
              {[...items].sort((a, b) => b.incurred_on.localeCompare(a.incurred_on)).map((x) => (
                <TR key={x.id}>
                  <TD className="whitespace-nowrap text-fg-2">{formatDay(x.incurred_on)}</TD>
                  <TD className="font-medium">{x.vendor}</TD>
                  <TD className="max-w-64 text-fg-2">{x.description ?? '—'}</TD>
                  <TD className="text-fg-2">{x.project_id ? (projectName.get(x.project_id) ?? 'Unknown project') : '—'}</TD>
                  <TD className="text-fg-2">{RECURRENCE_LABEL[x.recurrence] ?? x.recurrence}</TD>
                  <TD className="text-right tabular-nums">{formatUsd(x.amount_micros)}</TD>
                </TR>
              ))}
              <TR className="bg-raised font-medium">
                <TD colSpan={5}>Total</TD>
                <TD className="text-right tabular-nums">{formatUsd(items.reduce((s, x) => s + x.amount_micros, 0))}</TD>
              </TR>
            </TBody>
          </Table>
        )}
      </Boundary>
      {adding && <ExpenseDialog companyId={companyId} today={today} onClose={() => setAdding(false)} />}
    </Card>
  )
}

function ExpenseDialog({ companyId, today, onClose }: { companyId: string; today: string; onClose: () => void }) {
  const create = useCreateExpense(companyId)
  const projects = useProjects(companyId)
  const toast = useToast()
  const [v, setV] = useState<ExpenseFormValues>(() => emptyExpenseForm(today))
  const [errors, setErrors] = useState<ExpenseFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const set = (p: Partial<ExpenseFormValues>) => {
    setV((x) => ({ ...x, ...p }))
    setErrors({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce = validateExpense(v)
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      await create.mutateAsync(expenseToRequest(v))
      toast.success('Expense recorded')
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const f = err.fieldErrors
        const mapped: ExpenseFormErrors = { vendor: f.vendor, description: f.description, amount: f.amount_micros, incurredOn: f.incurred_on }
        for (const k of Object.keys(mapped) as Array<keyof ExpenseFormErrors>) if (!mapped[k]) delete mapped[k]
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
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      size="sm"
      title="Add external expense"
      description="Record money already spent outside model usage."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button type="submit" form="expense-form" variant="primary" loading={create.isPending}>
            Add expense
          </Button>
        </>
      }
    >
      <form id="expense-form" onSubmit={(e) => void submit(e)} noValidate className="space-y-3">
        <Field label="Vendor" required error={errors.vendor}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus maxLength={VENDOR_MAX} value={v.vendor} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ vendor: e.target.value })} />}
        </Field>
        <Field label="Description" error={errors.description}>
          {({ id, invalid, describedBy }) => <Textarea id={id} rows={2} maxLength={DESCRIPTION_MAX} value={v.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ description: e.target.value })} />}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Amount (USD)" required error={errors.amount}>
            {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" placeholder="0.00" value={v.amount} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ amount: e.target.value })} />}
          </Field>
          <Field label="Date" required error={errors.incurredOn}>
            {({ id, invalid, describedBy }) => <Input id={id} type="date" value={v.incurredOn} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set({ incurredOn: e.target.value })} />}
          </Field>
          <Field label="Recurrence">
            {({ id }) => (
              <Select id={id} value={v.recurrence} onChange={(e) => set({ recurrence: e.target.value as ExpenseFormValues['recurrence'] })}>
                {Object.entries(RECURRENCE_LABEL).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Project (optional)">
            {({ id }) => (
              <Select id={id} value={v.projectId} onChange={(e) => set({ projectId: e.target.value })}>
                <option value="">No project</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
