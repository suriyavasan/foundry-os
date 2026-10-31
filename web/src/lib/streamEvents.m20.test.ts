import { describe, expect, it } from 'vitest'
import { qk } from '@/api/keys'
import { KNOWN_EVENT_TYPES, effectsForEvent } from './streamEvents'

const keys = (type: string) => effectsForEvent('c1', { id: 5, type, payload: { question_id: 'q1' } }).map((e) => (e.kind === 'invalidate' ? JSON.stringify(e.key) : 'patch'))

describe('M20 stream events: founder questions', () => {
  it('listens for question.created and question.updated', () => {
    expect(KNOWN_EVENT_TYPES).toContain('question.created')
    expect(KNOWN_EVENT_TYPES).toContain('question.updated')
  })
  it('refreshes the questions in the chat, and the work an answer releases', () => {
    for (const t of ['question.created', 'question.updated']) {
      const k = keys(t)
      expect(k).toContain(JSON.stringify(qk.questions('c1')))
      expect(k).toContain(JSON.stringify(qk.tasksAll('c1')))
    }
  })
})
