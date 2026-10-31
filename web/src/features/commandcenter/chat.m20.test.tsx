import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FounderQuestion } from '@/api/questions'
import type { CommandExchange } from '@/api/types'
import { normalizeLive } from '@/lib/liveState'
import { jsonResponse, makeAgent, mockApi, page } from '@/test/factories'
import { makeExchange } from '@/test/m10'
import { apiError, baseRoutes, renderCanvas } from '@/test/m5'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const feed = (items: CommandExchange[], next: string | null = null) => ({ items, next_cursor: next })
const daysAgo = (d: number, h = 10) => new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate() - d, h).toISOString()
const widget = () => screen.getByRole('region', { name: 'Command Center' })

const question = (over: Partial<FounderQuestion> = {}): FounderQuestion => ({
  id: 'q1', company_id: 'c1', run_id: null, answered_by_user_id: null, answered_via: null, answered_at: null, updated_at: new Date().toISOString(), agent_id: 'a2', task_id: null, question: 'Should the landing page say "Pro" or "Plus"?', options: ['Pro', 'Plus'], default_answer: 'Pro', blocking: false, status: 'open', answer: null,
  deadline: new Date(Date.now() + 4 * 3_600_000).toISOString(), created_at: new Date().toISOString(), ...over,
})

async function renderCc(over: Record<string, unknown> = {}) {
  const api = mockApi({ ...baseRoutes, '/companies/c1/command-center/feed': feed([]), ...over })
  vi.stubGlobal('fetch', api.impl)
  renderCanvas()
  await screen.findByRole('region', { name: 'Command Center' })
  return api
}

describe('the conversation', () => {
  it('is a live log, oldest first, with the newest at the bottom and a separator per day instead of a time per message', async () => {
    await renderCc({
      '/companies/c1/command-center/feed': feed([
        makeExchange({ id: 'c', input: 'third', created_at: daysAgo(0, 9) }),
        makeExchange({ id: 'b', input: 'second', created_at: daysAgo(1, 9) }),
        makeExchange({ id: 'a', input: 'first', created_at: daysAgo(1, 8) }),
      ]),
    })
    const log = await within(widget()).findByRole('log', { name: 'Command Center conversation' })
    expect(log).toHaveAttribute('aria-live', 'polite')
    await within(log).findAllByTestId('exchange')
    const flow = [...log.querySelectorAll('[role="separator"], [data-testid="exchange"]')].map((n) => (n.getAttribute('role') === 'separator' ? n.getAttribute('aria-label') : n.querySelector('p')!.textContent))
    expect(flow).toEqual(['Yesterday', 'first', 'second', 'Today', 'third'])
    // no per-message timestamp text ("5 min ago"); the exact time is in the bubble's tooltip
    expect(within(log).queryByText(/ago$/)).not.toBeInTheDocument()
    expect(within(log).getByText('third')).toHaveAttribute('title', daysAgo(0, 9))
  })

  it('names who is speaking: You, and the AI Co-founder with its face on the first reply', async () => {
    const atlas = makeAgent({ id: 'f1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder', reports_to_human: true })
    await renderCc({
      '/companies/c1/agents': page([atlas]),
      '/companies/c1/command-center/feed': feed([makeExchange({ id: 'a', input: 'hello', input_kind: 'note', status: 'answered', output: { kind: 'text', title: null, text: 'Hi there.' } })]),
    })
    const card = await within(widget()).findByTestId('exchange')
    expect(within(card).getByRole('group', { name: 'You' })).toHaveTextContent('hello')
    const reply = within(card).getByRole('group', { name: 'AI Co-founder' })
    expect(reply).toHaveTextContent('Hi there.')
    await waitFor(() => expect(reply.querySelector('img.cos-avatar-face')).not.toBeNull())
  })

  it('loads older messages when the reader scrolls to the top, and on request', async () => {
    const cursors: Array<string | null> = []
    await renderCc({
      '/companies/c1/command-center/feed': (url: URL) => {
        cursors.push(url.searchParams.get('cursor'))
        return jsonResponse(url.searchParams.get('cursor') === 'c2' ? feed([makeExchange({ id: 'o', input: 'older one', created_at: daysAgo(3) })]) : feed([makeExchange({ id: 'n', input: 'newer one', created_at: daysAgo(0) })], 'c2'))
      },
    })
    const log = await within(widget()).findByRole('log')
    await within(log).findByText('newer one')
    expect(cursors).toEqual([null]) // nothing is fetched until the reader asks
    for (const [k, v] of Object.entries({ scrollHeight: 900, clientHeight: 300 })) Object.defineProperty(log, k, { configurable: true, value: v })
    log.scrollTop = 0
    fireEvent.scroll(log)
    await within(log).findByText('older one')
    expect(cursors).toContain('c2')
    // the older message is above the newer one
    expect(log.textContent!.indexOf('older one')).toBeLessThan(log.textContent!.indexOf('newer one'))
    expect(within(log).queryByRole('button', { name: 'Load older messages' })).not.toBeInTheDocument()
  })
})

describe('founder questions in the chat', () => {
  const iris = makeAgent({ id: 'a2', name: 'Iris', role_title: 'Head of Research', kind: 'lead', manager_agent_id: 'f1' })
  const withQuestions = (items: FounderQuestion[], extra: Record<string, unknown> = {}) => ({ '/companies/c1/agents': page([baseRoutes['/companies/c1/agents'].items[0]!, iris]), '/companies/c1/questions': page(items), ...extra })

  it('shows an open question as a bubble from the asking agent, with its options, the default and a free reply', async () => {
    await renderCc(withQuestions([question()]))
    const bubble = await within(widget()).findByTestId('question')
    const group = within(bubble).getByRole('group', { name: 'Iris' })
    expect(within(group).getByText('Should the landing page say "Pro" or "Plus"?')).toBeInTheDocument()
    expect(group.querySelector('img.cos-avatar-face')).not.toBeNull() // Iris's face
    expect(within(group).getByText('Iris')).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: 'Pro' })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: 'Plus' })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: 'Use default (Pro)' })).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: 'Reply…' })).toBeInTheDocument()
  })

  it('answers with an option, with the default, or with free text', async () => {
    const api = await renderCc(withQuestions([question()], { 'POST /companies/c1/questions/q1/answer': { ok: true } }))
    const answers = () => api.calls.filter((c) => c.method === 'POST' && c.path === '/companies/c1/questions/q1/answer').map((c) => c.body)
    const user = userEvent.setup()
    const bubble = await within(widget()).findByTestId('question')
    await user.click(within(bubble).getByRole('button', { name: 'Plus' }))
    await waitFor(() => expect(answers()).toEqual([{ answer: 'Plus' }]))
    await user.click(within(bubble).getByRole('button', { name: 'Use default (Pro)' }))
    await waitFor(() => expect(answers()).toEqual([{ answer: 'Plus' }, { answer: 'Pro' }]))
    await user.click(within(bubble).getByRole('button', { name: 'Reply…' }))
    await user.type(within(bubble).getByRole('textbox', { name: 'Your answer' }), 'Neither, call it Studio{Enter}')
    await waitFor(() => expect(answers().at(-1)).toEqual({ answer: 'Neither, call it Studio' }))
  })

  it('turns a resolved question into a one-line receipt', async () => {
    await renderCc(
      withQuestions([
        question({ id: 'q1', status: 'answered', answer: 'Plus' }),
        question({ id: 'q2', status: 'defaulted', answer: 'Pro', question: 'Which colour?' }),
        question({ id: 'q3', status: 'cancelled', question: 'Never mind?' }),
      ]),
    )
    const receipts = await within(widget()).findAllByTestId('question')
    expect(receipts).toHaveLength(3)
    expect(within(widget()).queryByRole('button', { name: 'Use default (Pro)' })).not.toBeInTheDocument()
    const text = receipts.map((r) => r.textContent).join('|')
    expect(text).toMatch(/Iris asked “Should the landing page say "Pro" or "Plus"\?” · You answered: Plus/)
    expect(text).toMatch(/Went with the default: Pro/)
    expect(text).toMatch(/Cancelled/)
  })

  it('shows the question as plain text: nothing the agent wrote becomes markup', async () => {
    await renderCc(withQuestions([question({ question: '<img src=x onerror=alert(1)> **bold** [link](http://evil.test)', options: ['<b>A</b>'] })]))
    const bubble = await within(widget()).findByTestId('question')
    expect(bubble.querySelector('img[src="x"]')).toBeNull()
    expect(bubble.querySelector('a')).toBeNull()
    expect(bubble.querySelector('strong')).toBeNull()
    expect(bubble).toHaveTextContent('**bold** [link](http://evil.test)')
    expect(within(bubble).getByRole('button', { name: '<b>A</b>' })).toBeInTheDocument()
  })

  it('says why an answer failed, and keeps the question', async () => {
    await renderCc(withQuestions([question()], { 'POST /companies/c1/questions/q1/answer': () => apiError(500, 'The answer could not be saved') }))
    const bubble = await within(widget()).findByTestId('question')
    await userEvent.click(within(bubble).getByRole('button', { name: 'Plus' }))
    expect(await within(bubble).findByRole('alert')).toHaveTextContent('The answer could not be saved')
    expect(within(bubble).getByRole('button', { name: 'Plus' })).toBeEnabled()
  })

  it('a viewer reads the question but cannot answer it', async () => {
    await renderCc(withQuestions([question()], { '/me': { user: { id: 'u', email: 'o@x', display_name: 'Sam' }, memberships: [{ company_id: 'c1', company_name: 'Acme', role: 'viewer' }] } }))
    const bubble = await within(widget()).findByTestId('question')
    expect(within(bubble).queryByRole('button')).not.toBeInTheDocument()
    expect(bubble).toHaveTextContent(/Viewers cannot answer/)
  })

  it('on a backend without the questions API (404) or when reading is forbidden (403) there are no bubbles and no error', async () => {
    for (const questions of [undefined, () => apiError(403, 'Only owners and admins')]) {
      const api = mockApi({ ...baseRoutes, '/companies/c1/command-center/feed': feed([makeExchange({ input: '/status' })]), ...(questions ? { '/companies/c1/questions': questions } : {}) })
      vi.stubGlobal('fetch', api.impl)
      const view = renderCanvas()
      await screen.findByRole('region', { name: 'Command Center' })
      await within(widget()).findAllByTestId('exchange')
      await waitFor(() => expect(api.calls.some((c) => c.path === '/companies/c1/questions')).toBe(true))
      expect(within(widget()).queryByTestId('question')).not.toBeInTheDocument()
      expect(within(widget()).queryByRole('alert')).not.toBeInTheDocument()
      expect(within(widget()).getByRole('combobox')).toBeInTheDocument()
      view.unmount()
    }
  })
})

describe('the Command Center as the first thing on the canvas', () => {
  it('shows the asking agent as working when it is (the face carries the live ring)', async () => {
    const iris = makeAgent({ id: 'a2', name: 'Iris', manager_agent_id: 'f1', live: normalizeLive({ state: 'working' }) })
    await renderCc({ '/companies/c1/agents': page([baseRoutes['/companies/c1/agents'].items[0]!, iris]), '/companies/c1/questions': page([question()]) })
    const bubble = await within(widget()).findByTestId('question')
    expect(bubble.querySelector('.cos-avatar')).toHaveAttribute('data-pulse', 'true')
  })
})
