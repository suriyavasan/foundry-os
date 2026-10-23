import { describe, expect, it } from 'vitest'
import { makeNote, makeReply } from '@/test/factories'
import {
  NOTE_MAX_BYTES,
  actionChips,
  confirmOptimistic,
  counterTone,
  countUnread,
  filterNotes,
  DEFAULT_NOTE_FILTERS,
  hasUnreadReply,
  isSubmitShortcut,
  noteBytes,
  noteChips,
  noteExcerpt,
  optimisticNote,
  restoreDraft,
  rollbackOptimistic,
  sortNotes,
  validateNoteBody,
  withOptimistic,
  withReply,
} from './notes'

describe('note body validation', () => {
  it('rejects empty and whitespace-only notes with a reason', () => {
    for (const body of ['', '   ', '\n\t ']) {
      const v = validateNoteBody(body)
      expect(v.ok).toBe(false)
      if (!v.ok) {
        expect(v.problem).toBe('empty')
        expect(v.message).toMatch(/write something/i)
      }
    }
  })

  it('trims and accepts a normal note', () => {
    expect(validateNoteBody('  hello  ')).toEqual({ ok: true, body: 'hello' })
  })

  it('measures the 10 KB limit in UTF-8 bytes, not characters', () => {
    expect(noteBytes('abc')).toBe(3)
    expect(noteBytes('é')).toBe(2)
    expect(noteBytes('日本')).toBe(6)
    expect(validateNoteBody('a'.repeat(NOTE_MAX_BYTES)).ok).toBe(true)
    const over = validateNoteBody('a'.repeat(NOTE_MAX_BYTES + 1))
    expect(over.ok).toBe(false)
    // 5121 two-byte characters are only 5121 characters but 10242 bytes.
    expect(validateNoteBody('é'.repeat(5121)).ok).toBe(false)
    expect(validateNoteBody('é'.repeat(5120)).ok).toBe(true)
  })

  it('colours the counter: normal, warning from 90 %, danger over the limit', () => {
    expect(counterTone(100)).toBe('normal')
    expect(counterTone(NOTE_MAX_BYTES * 0.9)).toBe('warning')
    expect(counterTone(NOTE_MAX_BYTES)).toBe('warning')
    expect(counterTone(NOTE_MAX_BYTES + 1)).toBe('danger')
  })
})

describe('composer keyboard + draft', () => {
  it('submits on Cmd+Enter and Ctrl+Enter only', () => {
    expect(isSubmitShortcut({ key: 'Enter', metaKey: true, ctrlKey: false })).toBe(true)
    expect(isSubmitShortcut({ key: 'Enter', metaKey: false, ctrlKey: true })).toBe(true)
    expect(isSubmitShortcut({ key: 'Enter', metaKey: false, ctrlKey: false })).toBe(false)
    expect(isSubmitShortcut({ key: 'a', metaKey: true, ctrlKey: false })).toBe(false)
  })

  it('puts a failed draft back without losing what was typed since', () => {
    expect(restoreDraft('', 'first')).toBe('first')
    expect(restoreDraft('   ', 'first')).toBe('first')
    expect(restoreDraft('second', 'first')).toBe('first\n\nsecond')
    expect(restoreDraft('first', 'first')).toBe('first')
  })
})

describe('triage chip mapping', () => {
  it('a new note is New and has no category', () => {
    const c = noteChips(makeNote({ status: 'new' }))
    expect(c.status.label).toBe('New')
    expect(c.category).toBeNull()
  })

  it('a triaged note is Triaged with its category chip', () => {
    const c = noteChips(makeNote({ status: 'triaged', triage: { category: 'directive', summary: 's', actions: [], confidence: 0.9 } }))
    expect(c.status).toMatchObject({ label: 'Triaged', tone: 'success' })
    expect(c.category).toMatchObject({ label: 'Directive', tone: 'accent' })
  })

  it('maps every category to a distinct label', () => {
    const cats = ['directive', 'goal', 'requirement', 'idea', 'feedback', 'question', 'context', 'noise'] as const
    const labels = cats.map((category) => noteChips(makeNote({ status: 'triaged', triage: { category, summary: '', actions: [], confidence: null } })).category!.label)
    expect(new Set(labels).size).toBe(cats.length)
    expect(labels).toContain('Noise')
  })

  it('archived notes say so and keep their category; pending notes say Sending', () => {
    expect(noteChips(makeNote({ status: 'archived' })).status.label).toBe('Archived')
    expect(noteChips(makeNote({ pending: true })).status.label).toBe('Sending')
  })
})

describe('action chips route to panels', () => {
  const titles = { goal: new Map([['g1', 'Launch v1']]), task: new Map([['t1', 'Write copy']]) }
  const chips = actionChips(
    [
      { type: 'goal', ref_id: 'g1' },
      { type: 'project', ref_id: 'p1' },
      { type: 'task', ref_id: 't1' },
      { type: 'schedule', ref_id: 's1' },
      { type: 'memory', ref_id: 'm1' },
      { type: 'wizard', ref_id: 'x1' },
      { type: 'task', ref_id: '../etc/passwd' },
    ],
    titles,
  )

  it('goal and project open the goals panel, task opens that task, schedule opens routines, memory opens memory', () => {
    expect(chips.map((c) => c.panel)).toEqual([{ kind: 'goals' }, { kind: 'goals' }, { kind: 'task', id: 't1' }, { kind: 'routines' }, { kind: 'memory' }, null, null])
  })

  it('labels with the record name when it is known, else the type', () => {
    expect(chips[0]!.label).toBe('Launch v1')
    expect(chips[1]!.label).toBe('Project')
    expect(chips[2]!.label).toBe('Write copy')
    expect(actionChips([{ type: 'goal', ref_id: 'g9', label: 'From server' }], titles)[0]!.label).toBe('From server')
  })

  it('handles notes without triage', () => {
    expect(actionChips(null)).toEqual([])
    expect(actionChips(undefined)).toEqual([])
  })
})

describe('listing', () => {
  const a = makeNote({ id: 'a', created_at: '2026-10-24T08:00:00Z', body: 'Pricing idea' })
  const b = makeNote({ id: 'b', created_at: '2026-10-24T10:00:00Z', status: 'triaged', triage: { category: 'goal', summary: 'Grow revenue', actions: [], confidence: 1 } })
  const c = makeNote({ id: 'c', created_at: '2026-10-24T09:00:00Z', pinned: true, status: 'archived' })

  it('sorts newest first with pinned notes on top', () => {
    expect(sortNotes([a, b, c]).map((n) => n.id)).toEqual(['c', 'b', 'a'])
    expect(sortNotes([a, b]).map((n) => n.id)).toEqual(['b', 'a'])
  })

  it('hides archived notes by default and filters by status, category, pin and search', () => {
    const all = [a, b, c]
    expect(filterNotes(all, DEFAULT_NOTE_FILTERS).map((n) => n.id)).toEqual(['b', 'a'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, status: 'archived' }).map((n) => n.id)).toEqual(['c'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, status: 'all' })).toHaveLength(3)
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, status: 'new' }).map((n) => n.id)).toEqual(['a'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, category: 'goal' }).map((n) => n.id)).toEqual(['b'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, status: 'all', pinnedOnly: true }).map((n) => n.id)).toEqual(['c'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, q: 'PRICING' }).map((n) => n.id)).toEqual(['a'])
    expect(filterNotes(all, { ...DEFAULT_NOTE_FILTERS, q: 'revenue' }).map((n) => n.id)).toEqual(['b'])
  })

  it('searches reply text too', () => {
    const withReplyText = makeNote({ id: 'd', last_reply: makeReply({ body: 'Created the Lisbon project' }) })
    expect(filterNotes([withReplyText], { ...DEFAULT_NOTE_FILTERS, q: 'lisbon' })).toHaveLength(1)
  })

  it('makes a plain excerpt of markdown', () => {
    expect(noteExcerpt('# Title\n\nSome **bold** and `code` with [a link](http://x.y)')).toBe('Title Some bold and code with a link')
    expect(noteExcerpt('x'.repeat(300), 50)).toHaveLength(50)
    expect(noteExcerpt('```\nsecret\n```')).toBe('[code]')
  })
})

describe('unread replies', () => {
  const agentReply = makeReply({ author_type: 'agent', created_at: '2026-10-24T10:00:00Z' })
  const n = makeNote({ id: 'n1', last_reply: agentReply })

  it('is unread until the reply (or a newer one) has been opened', () => {
    expect(hasUnreadReply(n, {})).toBe(true)
    expect(hasUnreadReply(n, { n1: '2026-10-24T09:00:00Z' })).toBe(true)
    expect(hasUnreadReply(n, { n1: '2026-10-24T10:00:00Z' })).toBe(false)
  })

  it('never counts the Founder’s own replies or notes without replies', () => {
    expect(hasUnreadReply(makeNote({ id: 'x', last_reply: makeReply({ author_type: 'human' }) }), {})).toBe(false)
    expect(hasUnreadReply(makeNote({ id: 'y' }), {})).toBe(false)
    expect(countUnread([n, makeNote({ id: 'y' })], {})).toBe(1)
  })
})

describe('optimistic list updates', () => {
  it('inserts on top, confirms by replacing, and rolls back by removing', () => {
    const existing = makeNote({ id: 'old' })
    const tmp = optimisticNote('hello', [], 'c1')
    expect(tmp.pending).toBe(true)
    expect(tmp.status).toBe('new')
    const inserted = withOptimistic([existing], tmp)
    expect(inserted.map((n) => n.id)).toEqual([tmp.id, 'old'])

    const real = makeNote({ id: 'real', body: 'hello' })
    expect(confirmOptimistic(inserted, tmp.id, real).map((n) => n.id)).toEqual(['real', 'old'])
    // the stream may have delivered the real row first: never a duplicate
    expect(confirmOptimistic([real, ...inserted], tmp.id, real).map((n) => n.id)).toEqual(['real', 'old'])

    expect(rollbackOptimistic(inserted, tmp.id)).toEqual([existing])
    expect(rollbackOptimistic(undefined, 'x')).toEqual([])
  })

  it('reflects a reply in the row preview and count', () => {
    const r = makeReply({ note_id: 'n1', body: 'On it' })
    const out = withReply([makeNote({ id: 'n1', reply_count: 1 }), makeNote({ id: 'n2' })], r)!
    expect(out[0]!.last_reply).toBe(r)
    expect(out[0]!.reply_count).toBe(2)
    expect(out[1]!.last_reply).toBeUndefined()
  })
})
