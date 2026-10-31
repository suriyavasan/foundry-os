import { screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeApproval, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const inHours = (h: number) => new Date(Date.now() + h * 3_600_000 + 30_000).toISOString()
const inMinutes = (m: number) => new Date(Date.now() + m * 60_000 + 5_000).toISOString()

describe('approval deadlines (M18)', () => {
  it('tells a human approval it declines automatically, and a Co-founder approval where it goes next', async () => {
    const human = makeApproval({ id: 'h1', title: 'Register acme.io', approver: 'human', expires_at: inHours(5) })
    const cofounder = makeApproval({ id: 'f1', title: 'Buy a stock photo', approver: 'founder', expires_at: inMinutes(20) })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([human, cofounder]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    const you = await within(dialog).findByRole('region', { name: 'Waiting for you' })
    expect(within(you).getByTestId('approval-expiry')).toHaveTextContent('Declines automatically in 5 h')
    const co = within(dialog).getByRole('region', { name: 'Decided by the AI Co-founder' })
    expect(within(co).getByTestId('approval-expiry')).toHaveTextContent("Goes to you in 20 min if the Co-founder doesn't decide")
  })

  it('also shows the deadline on the canvas approvals widget', async () => {
    const human = makeApproval({ id: 'h1', title: 'Register acme.io', approver: 'human', expires_at: inHours(3) })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([human]) }).impl)
    renderCanvas()
    const widget = await screen.findByRole('region', { name: 'Approvals' })
    expect(await within(widget).findByTestId('approval-expiry')).toHaveTextContent('Declines automatically in 3 h')
  })

  it('shows an expired approval as declined by timeout, without decision buttons', async () => {
    const expired = makeApproval({ id: 'e1', title: 'Register acme.io', status: 'expired', approver: 'human', expires_at: inHours(-1), decision_note: 'No answer within 24h; declined by timeout' })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([expired]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    expect(await within(dialog).findByText('Expired · declined by timeout')).toBeInTheDocument()
    expect(within(dialog).queryByTestId('approval-expiry')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('shows no deadline line for an approval without one', async () => {
    const old = makeApproval({ id: 'o1', title: 'Old one', approver: 'human' })
    vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/approvals': page([old]) }).impl)
    renderCanvas('/c/c1?panel=approvals')
    const dialog = await screen.findByRole('dialog', { name: 'Approvals' })
    await within(dialog).findByText('Old one')
    expect(within(dialog).queryByTestId('approval-expiry')).not.toBeInTheDocument()
  })
})
