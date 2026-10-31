import { describe, expect, it } from 'vitest'
import type { FounderQuestion } from '@/api/questions'
import type { HumanWorkItem } from '@/api/types'
import { makeExchange } from '@/test/m10'
import { makeHumanWork } from '@/test/m22'
import { buildChat, dayLabel, statusLineOf, type ChatEntry } from './chatFeed'
import { exchangeView, localFailure, optimisticExchange } from './commandCenter'

const NOW = new Date(2026, 9, 30, 15, 0, 0).getTime()
const at = (daysAgo: number, h = 10, m = 0) => new Date(2026, 9, 30 - daysAgo, h, m, 0).toISOString()
const q = (over: Partial<FounderQuestion> = {}): FounderQuestion => ({
  id: 'q1', company_id: 'c1', run_id: null, answered_by_user_id: null, answered_via: null, answered_at: null, updated_at: at(0, 11), agent_id: 'a1', task_id: null, question: 'Which name?', options: ['A', 'B'], default_answer: 'A', blocking: false, status: 'open', answer: null, deadline: at(0, 18), created_at: at(0, 11), ...over,
})
const kinds = (es: ChatEntry[]) => es.map((e) => (e.kind === 'day' ? `day:${e.label}` : e.kind === 'exchange' ? `ex:${e.exchange.id}` : e.kind === 'human_work' ? `h:${e.item.id}` : `q:${e.question.id}`))

describe('buildChat', () => {
  it('reads oldest first, with the newest at the bottom, whatever order the feed gave', () => {
    const feed = [makeExchange({ id: 'new', created_at: at(0, 12) }), makeExchange({ id: 'old', created_at: at(0, 9) })]
    expect(kinds(buildChat(feed, [], { now: NOW }))).toEqual(['day:Today', 'ex:old', 'ex:new'])
  })

  it('puts a day separator where the day changes (Today, Yesterday, then a date) instead of a time per message', () => {
    const feed = [makeExchange({ id: 'c', created_at: at(0, 9) }), makeExchange({ id: 'b', created_at: at(1, 9) }), makeExchange({ id: 'a', created_at: at(5, 9) })]
    const k = kinds(buildChat(feed, [], { now: NOW }))
    expect(k.filter((x) => x.startsWith('day:'))).toHaveLength(3)
    expect(k[0]).toMatch(/^day:/)
    expect(k).toContain('day:Today')
    expect(k).toContain('day:Yesterday')
    expect(k.indexOf('ex:a')).toBeLessThan(k.indexOf('ex:b'))
    expect(k.indexOf('ex:b')).toBeLessThan(k.indexOf('ex:c'))
  })

  it('weaves founder questions in by time', () => {
    const feed = [makeExchange({ id: 'a', created_at: at(0, 9) }), makeExchange({ id: 'b', created_at: at(0, 12) })]
    expect(kinds(buildChat(feed, [q({ created_at: at(0, 10) })], { now: NOW }))).toEqual(['day:Today', 'ex:a', 'q:q1', 'ex:b'])
  })

  it('shows the Co-founder face and name on the first reply of a group only', () => {
    const feed = [
      makeExchange({ id: 'a', status: 'answered', created_at: at(0, 9) }),
      makeExchange({ id: 'b', status: 'pending', output: null, created_at: at(0, 10) }),
      makeExchange({ id: 'c', status: 'pending', output: null, created_at: at(0, 11) }),
    ]
    const ex = buildChat(feed, [], { now: NOW }).filter((e): e is Extract<ChatEntry, { kind: 'exchange' }> => e.kind === 'exchange')
    expect(ex.map((e) => e.showAuthor)).toEqual([true, false, false]) // waiting ones have no reply bubble yet
    // two Founder messages in a row sit closer together
    expect(ex.map((e) => e.tight)).toEqual([false, false, true])
  })

  it('groups consecutive questions of the same agent', () => {
    const qs = [q({ id: 'q1', created_at: at(0, 9) }), q({ id: 'q2', created_at: at(0, 9, 5) }), q({ id: 'q3', agent_id: 'a2', created_at: at(0, 9, 6) })]
    const e = buildChat([], qs, { now: NOW }).filter((x): x is Extract<ChatEntry, { kind: 'question' }> => x.kind === 'question')
    expect(e.map((x) => x.showAuthor)).toEqual([true, false, true])
  })

  it('leaves out resolved questions older than the loaded history, but never an open one', () => {
    const feed = [makeExchange({ id: 'a', created_at: at(0, 9) })]
    const qs = [q({ id: 'old-done', status: 'answered', answer: 'A', created_at: at(3) }), q({ id: 'old-open', created_at: at(3) })]
    expect(kinds(buildChat(feed, qs, { now: NOW, hasMore: true })).filter((k) => k.startsWith('q:'))).toEqual(['q:old-open'])
    expect(kinds(buildChat(feed, qs, { now: NOW, hasMore: false })).filter((k) => k.startsWith('q:'))).toHaveLength(2)
  })

  it('is empty for nothing', () => {
    expect(buildChat([], [], { now: NOW })).toEqual([])
  })
})

describe('dayLabel', () => {
  it('says Today and Yesterday, and gives the year only for another year', () => {
    expect(dayLabel(at(0), NOW)).toBe('Today')
    expect(dayLabel(at(1), NOW)).toBe('Yesterday')
    expect(dayLabel(at(9), NOW)).not.toMatch(/2026/)
    expect(dayLabel(new Date(2025, 2, 3).toISOString(), NOW)).toMatch(/2025/)
  })
})

describe('statusLineOf: only while not answered', () => {
  const line = (over: Parameters<typeof makeExchange>[0], now = NOW) => {
    const ex = makeExchange(over)
    return statusLineOf(ex, exchangeView(ex, now))
  }
  it('shows nothing for a command that ran or a message that was answered', () => {
    expect(line({ status: 'done' })).toBeNull()
    expect(line({ status: 'answered', input_kind: 'note' })).toBeNull()
  })
  it('says what it is waiting for', () => {
    expect(line({ status: 'pending', output: null, created_at: at(0, 14, 58) })).toMatchObject({ text: 'Waiting for the AI Co-founder' })
    const sending = optimisticExchange('hi')
    expect(statusLineOf(sending, exchangeView(sending))).toMatchObject({ text: 'Sending…' })
  })
  it('has no line while it is being worked on (the typing bubble speaks), unless it is stale', () => {
    expect(line({ status: 'processing', output: null, created_at: at(0, 14, 58) })).toBeNull()
    expect(line({ status: 'processing', output: null, created_at: at(0, 9) })).toMatchObject({ retry: 'Retry now', alert: true })
  })
  it('failed offers a retry, and says when it never reached the server', () => {
    expect(line({ status: 'failed' })).toMatchObject({ text: 'Failed', retry: 'Retry', tone: 'danger' })
    const local = localFailure('/x', 'nope')
    expect(statusLineOf(local, exchangeView(local))?.text).toMatch(/did not reach the server/)
  })
})

describe('buildChat: human-work requests (M22)', () => {
  const hw = (over: Partial<HumanWorkItem> = {}): HumanWorkItem => makeHumanWork({ id: 'h1', requested_by_agent_id: 'a2', created_at: at(0, 11), ...over })

  it('draws open requests in time order among the exchanges and questions, and groups them by who asked', () => {
    const feed = [makeExchange({ id: 'e1', created_at: at(0, 9) })]
    const out = buildChat(feed, [q({ id: 'q1', created_at: at(0, 10) })], { now: NOW, humanWork: [hw({ id: 'h2', created_at: at(0, 13) }), hw({ id: 'h1', created_at: at(0, 11) })] })
    expect(kinds(out)).toEqual(['day:Today', 'ex:e1', 'q:q1', 'h:h1', 'h:h2'])
    const bubbles = out.filter((e) => e.kind === 'human_work')
    expect(bubbles[0]).toMatchObject({ showAuthor: true, tight: false })
    expect(bubbles[1]).toMatchObject({ showAuthor: false, tight: true }) // same agent twice in a row
  })

  it('leaves finished and cancelled requests out', () => {
    const out = buildChat([], [], { now: NOW, humanWork: [hw({ id: 'a', status: 'done' }), hw({ id: 'b', status: 'cancelled' }), hw({ id: 'c', status: 'ready' })] })
    expect(kinds(out)).toEqual(['day:Today', 'h:c'])
  })

  it('is unchanged without human work', () => {
    expect(kinds(buildChat([], [], { now: NOW }))).toEqual([])
  })
})
