import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FounderQuestion } from '@/api/questions'
import { makeAgent, mockApi, page } from '@/test/factories'
import { baseRoutes, renderCanvas } from '@/test/m5'

const scrollIntoView = vi.fn()
beforeEach(() => {
  localStorage.clear()
  scrollIntoView.mockReset()
  Element.prototype.scrollIntoView = scrollIntoView
})
afterEach(() => vi.unstubAllGlobals())

const question = (over: Partial<FounderQuestion> = {}): FounderQuestion => ({
  id: 'q1', company_id: 'c1', run_id: null, answered_by_user_id: null, answered_via: null, answered_at: null, updated_at: new Date().toISOString(), agent_id: 'a2', task_id: null, question: 'Pro or Plus?', options: ['Pro', 'Plus'], default_answer: 'Pro', blocking: false, status: 'open', answer: null,
  deadline: new Date(Date.now() + 4 * 3_600_000).toISOString(), created_at: '2026-10-30T10:00:00Z', ...over,
})

async function renderCc(questions: FounderQuestion[]) {
  vi.stubGlobal('fetch', mockApi({ ...baseRoutes, '/companies/c1/agents': page([makeAgent({ id: 'a2', name: 'Iris' })]), '/companies/c1/command-center/feed': page([]), '/companies/c1/questions': page(questions) }).impl)
  renderCanvas()
  return screen.findByRole('region', { name: 'Command Center' })
}

describe('Questions pill in the Command Center header', () => {
  it('counts the open questions and jumps to the oldest, highlighting it', async () => {
    const widget = await renderCc([
      question({ id: 'new', question: 'Newer question?', created_at: '2026-10-30T11:00:00Z' }),
      question({ id: 'old', question: 'Older question?', created_at: '2026-10-30T08:00:00Z' }),
      question({ id: 'done', status: 'answered', answer: 'Pro', created_at: '2026-10-29T08:00:00Z' }),
    ])
    const pill = await within(widget).findByRole('button', { name: '2 questions' })
    await userEvent.click(pill)
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    const oldest = document.getElementById('question-old')!
    expect(scrollIntoView.mock.contexts[0]).toBe(oldest)
    expect(oldest).toHaveAttribute('data-highlight', 'true')
    expect(document.getElementById('question-new')).not.toHaveAttribute('data-highlight')
    await waitFor(() => expect(oldest).not.toHaveAttribute('data-highlight'), { timeout: 3000 })
  })

  it('uses the singular for one question', async () => {
    const widget = await renderCc([question()])
    expect(await within(widget).findByRole('button', { name: '1 question' })).toBeInTheDocument()
  })

  it('is absent when nothing is open, and answered / defaulted ones still render as receipts', async () => {
    const widget = await renderCc([question({ id: 'a', status: 'answered', answer: 'Plus' }), question({ id: 'd', status: 'defaulted', answer: 'Pro' })])
    const receipts = await within(widget).findAllByTestId('question')
    expect(receipts.map((r) => r.textContent)).toEqual([expect.stringContaining('You answered: Plus'), expect.stringContaining('Went with the default: Pro')])
    expect(within(widget).queryByRole('button', { name: /questions?$/ })).not.toBeInTheDocument()
  })
})
