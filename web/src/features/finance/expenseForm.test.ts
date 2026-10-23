import { describe, expect, it } from 'vitest'
import { emptyExpenseForm, expenseToRequest, validateExpense, type ExpenseFormValues } from './expenseForm'

const ok = (over: Partial<ExpenseFormValues> = {}): ExpenseFormValues => ({ ...emptyExpenseForm('2026-10-24'), vendor: 'Namecheap', amount: '12.50', ...over })

describe('expense form', () => {
  it('starts one-time, dated today, empty', () => {
    expect(emptyExpenseForm('2026-10-24')).toMatchObject({ recurrence: 'one_time', incurredOn: '2026-10-24', vendor: '', amount: '' })
  })

  it('converts USD to integer micros without float drift', () => {
    const micros = (amount: string) => expenseToRequest(ok({ amount })).amount_micros
    expect(micros('19.99')).toBe(19_990_000) // 19.99 * 1e6 is 19990000.000000004 in floating point
    expect(micros('0.1')).toBe(100_000)
    expect(micros('0.3')).toBe(300_000)
    expect(micros('1234.56')).toBe(1_234_560_000)
    expect(micros('$1,000.25')).toBe(1_000_250_000)
    expect(micros('0.000001')).toBe(1)
    expect(Number.isInteger(micros('8.675309'))).toBe(true)
    expect(micros('8.675309')).toBe(8_675_309)
  })

  it('accepts a clean expense', () => {
    expect(validateExpense(ok())).toEqual({})
  })

  it('requires vendor, a positive amount and a real date', () => {
    expect(validateExpense(ok({ vendor: '  ' })).vendor).toBeDefined()
    expect(validateExpense(ok({ amount: '' })).amount).toBe('Enter the amount in USD')
    expect(validateExpense(ok({ amount: '0' })).amount).toMatch(/more than zero/)
    expect(validateExpense(ok({ amount: '0.0000004' })).amount).toBeDefined()
    expect(validateExpense(ok({ amount: '-3' })).amount).toBeDefined()
    expect(validateExpense(ok({ amount: '1e3' })).amount).toBeDefined()
    expect(validateExpense(ok({ amount: '12.5.1' })).amount).toBeDefined()
    expect(validateExpense(ok({ amount: 'abc' })).amount).toBeDefined()
    expect(validateExpense(ok({ incurredOn: '2026-02-30' })).incurredOn).toBeDefined()
    expect(validateExpense(ok({ incurredOn: '' })).incurredOn).toBeDefined()
  })

  it('bounds the text fields', () => {
    expect(validateExpense(ok({ vendor: 'x'.repeat(121) })).vendor).toBeDefined()
    expect(validateExpense(ok({ description: 'x'.repeat(501) })).description).toBeDefined()
  })

  it('builds the request: trims, blank optionals become null', () => {
    expect(expenseToRequest(ok({ vendor: ' Namecheap ', description: '  ', projectId: '', recurrence: 'yearly' }))).toEqual({
      vendor: 'Namecheap',
      description: null,
      amount_micros: 12_500_000,
      recurrence: 'yearly',
      project_id: null,
      incurred_on: '2026-10-24',
    })
    expect(expenseToRequest(ok({ description: 'acme.io renewal', projectId: 'p1' }))).toMatchObject({ description: 'acme.io renewal', project_id: 'p1' })
  })
})
