import type { ExpenseRecurrence, ExternalExpenseCreate } from '@/api/types'
import { isIsoDate } from '@/lib/finance'
import { usdToMicros } from '@/lib/money'

export interface ExpenseFormValues {
  vendor: string
  description: string
  /** USD as typed. Converted with string math, so 19.99 is exactly 19,990,000 micros. */
  amount: string
  recurrence: ExpenseRecurrence
  projectId: string
  incurredOn: string
}

export type ExpenseFormErrors = Partial<Record<'vendor' | 'description' | 'amount' | 'incurredOn', string>>

export const RECURRENCE_LABEL: Record<ExpenseRecurrence, string> = { one_time: 'One-time', monthly: 'Monthly', yearly: 'Yearly' }

export const emptyExpenseForm = (today: string): ExpenseFormValues => ({ vendor: '', description: '', amount: '', recurrence: 'one_time', projectId: '', incurredOn: today })

export const VENDOR_MAX = 120
export const DESCRIPTION_MAX = 500

export function validateExpense(v: ExpenseFormValues): ExpenseFormErrors {
  const e: ExpenseFormErrors = {}
  if (!v.vendor.trim()) e.vendor = 'Who was paid?'
  else if (v.vendor.trim().length > VENDOR_MAX) e.vendor = `At most ${VENDOR_MAX} characters`
  if (v.description.trim().length > DESCRIPTION_MAX) e.description = `At most ${DESCRIPTION_MAX} characters`
  const micros = usdToMicros(v.amount)
  if (v.amount.trim() === '') e.amount = 'Enter the amount in USD'
  else if (micros === null) e.amount = 'Enter a USD amount like 25 or 25.50 (up to 6 decimals)'
  else if (micros <= 0) e.amount = 'The amount must be more than zero'
  if (!isIsoDate(v.incurredOn)) e.incurredOn = 'Pick a date'
  return e
}

/** The API body (money as integer micros). Call only after `validateExpense` returned no errors. */
export function expenseToRequest(v: ExpenseFormValues): ExternalExpenseCreate {
  const description = v.description.trim()
  return {
    vendor: v.vendor.trim(),
    description: description === '' ? null : description,
    amount_micros: usdToMicros(v.amount) as number,
    recurrence: v.recurrence,
    project_id: v.projectId === '' ? null : v.projectId,
    incurred_on: v.incurredOn,
  }
}
