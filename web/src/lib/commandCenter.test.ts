import { describe, expect, it } from 'vitest'
import type { CommandExchange } from '@/api/types'
import {
  HISTORY_START,
  SLASH_COMMANDS,
  applySuggestion,
  commandChangesState,
  enterAcceptsSuggestion,
  enterSends,
  exchangeChips,
  exchangeView,
  historyKeyApplies,
  isActiveExchange,
  localFailure,
  matchCommands,
  normalizeCommandOutput,
  optimisticExchange,
  panelFromString,
  parseInput,
  pushHistory,
  stepHistory,
  usageOf,
} from './commandCenter'

const ex = (over: Partial<CommandExchange> = {}): CommandExchange => ({
  id: 'e1',
  input: 'hello',
  input_kind: 'note',
  created_at: '2026-10-24T10:00:00Z',
  updated_at: '2026-10-24T10:00:00Z',
  status: 'pending',
  output: null,
  note_id: 'n1',
  run_id: null,
  actions: [],
  ...over,
})

describe('parsing what was typed', () => {
  it('slash input is a command, anything else non-empty is free text', () => {
    expect(parseInput('')).toEqual({ kind: 'empty' })
    expect(parseInput('   ')).toEqual({ kind: 'empty' })
    expect(parseInput('/help')).toEqual({ kind: 'command', name: 'help', args: '', known: true })
    expect(parseInput('  /Tasks   blocked ')).toEqual({ kind: 'command', name: 'tasks', args: 'blocked', known: true })
    expect(parseInput('/task Write the docs @iris')).toMatchObject({ name: 'task', args: 'Write the docs @iris' })
    expect(parseInput('/zap now')).toMatchObject({ kind: 'command', known: false })
    expect(parseInput('Please raise prices')).toEqual({ kind: 'text', text: 'Please raise prices' })
    expect(parseInput('see /help')).toEqual({ kind: 'text', text: 'see /help' })
    expect(parseInput('/ alone')).toEqual({ kind: 'text', text: '/ alone' })
  })

  it('lists every command of the spec', () => {
    expect(SLASH_COMMANDS.map((c) => c.name)).toEqual(['help', 'status', 'goals', 'agents', 'tasks', 'spend', 'approvals', 'standards', 'pause', 'resume', 'goal', 'task', 'hire', 'approve', 'reject'])
    expect(SLASH_COMMANDS.every((c) => c.description.length > 0)).toBe(true)
    expect(usageOf(SLASH_COMMANDS.find((c) => c.name === 'task')!)).toBe('/task <title> [@agent]')
    expect(usageOf(SLASH_COMMANDS.find((c) => c.name === 'help')!)).toBe('/help')
  })

  it('knows which commands change things', () => {
    expect(commandChangesState('/pause')).toBe(true)
    expect(commandChangesState('/goal Launch')).toBe(true)
    expect(commandChangesState('/status')).toBe(false)
    expect(commandChangesState('plain text')).toBe(false)
  })
})

describe('autocomplete', () => {
  it('opens on "/" with every command, narrows by prefix, closes after a space', () => {
    expect(matchCommands('/').length).toBe(8)
    expect(matchCommands('/ta').map((c) => c.name)).toEqual(['tasks', 'task'])
    expect(matchCommands('/ap').map((c) => c.name)).toEqual(['approvals', 'approve'])
    expect(matchCommands('/go').map((c) => c.name)).toEqual(['goals', 'goal'])
    expect(matchCommands('/tasks ')).toEqual([])
    expect(matchCommands('hello')).toEqual([])
    expect(matchCommands('')).toEqual([])
    expect(matchCommands('/zzzz')).toEqual([])
  })

  it('accepting a suggestion adds a space only when the command takes arguments', () => {
    const by = (n: string) => SLASH_COMMANDS.find((c) => c.name === n)!
    expect(applySuggestion(by('help'))).toBe('/help')
    expect(applySuggestion(by('task'))).toBe('/task ')
  })

  it('Enter accepts the highlighted suggestion until the typed text already is that command', () => {
    const tasks = SLASH_COMMANDS.find((c) => c.name === 'tasks')
    expect(enterAcceptsSuggestion('/ta', tasks)).toBe(true)
    expect(enterAcceptsSuggestion('/tasks', tasks)).toBe(false)
    expect(enterAcceptsSuggestion('/ta', undefined)).toBe(false)
  })

  it('Enter sends a command or free text (Shift+Enter is the newline); an empty box sends nothing', () => {
    expect(enterSends('/status')).toBe(true)
    expect(enterSends('a thought')).toBe(true)
    expect(enterSends('/goal a\nb')).toBe(true)
    expect(enterSends('   ')).toBe(false)
    expect(enterSends('')).toBe(false)
  })
})

describe('history', () => {
  it('stores newest last, skips empties and immediate repeats, keeps the last 50', () => {
    expect(pushHistory([], '  /help ')).toEqual(['/help'])
    expect(pushHistory(['/help'], '/help')).toEqual(['/help'])
    expect(pushHistory(['/help'], '')).toEqual(['/help'])
    const many = Array.from({ length: 60 }, (_, i) => `c${i}`).reduce<string[]>((h, c) => pushHistory(h, c), [])
    expect(many).toHaveLength(50)
    expect(many.at(-1)).toBe('c59')
  })

  it('arrow up walks back through history, down walks forward and ends on the draft', () => {
    const h = ['/a', '/b', '/c']
    let r = stepHistory(h, HISTORY_START, 'up', 'my draft')
    expect(r.value).toBe('/c')
    r = stepHistory(h, r.cursor, 'up', r.value)
    expect(r.value).toBe('/b')
    r = stepHistory(h, r.cursor, 'up', r.value)
    r = stepHistory(h, r.cursor, 'up', r.value)
    expect(r.value).toBe('/a') // stops at the oldest
    r = stepHistory(h, r.cursor, 'down', r.value)
    expect(r.value).toBe('/b')
    r = stepHistory(h, r.cursor, 'down', r.value)
    r = stepHistory(h, r.cursor, 'down', r.value)
    expect(r.value).toBe('my draft')
    expect(r.cursor).toEqual(HISTORY_START)
    expect(stepHistory(h, HISTORY_START, 'down', 'x').value).toBe('x')
    expect(stepHistory([], HISTORY_START, 'up', 'x').value).toBe('x')
  })

  it('arrows browse history only from the first or last line', () => {
    expect(historyKeyApplies('up', '', 0)).toBe(true)
    expect(historyKeyApplies('up', 'one line', 3)).toBe(true)
    expect(historyKeyApplies('up', 'a\nb', 3)).toBe(false) // caret on the second line
    expect(historyKeyApplies('down', 'a\nb', 0)).toBe(false) // caret on the first line of two
    expect(historyKeyApplies('down', 'a\nb', 3)).toBe(true)
  })
})

describe('what an exchange shows in each status', () => {
  const now = new Date('2026-10-24T10:01:00Z').getTime()

  it('done shows the output; a missing output still says something', () => {
    expect(exchangeView(ex({ status: 'done', input_kind: 'command', output: { kind: 'text', title: 'T', text: 'x' } }), now)).toMatchObject({ phase: 'output', placeholder: null, canRetry: false })
    expect(exchangeView(ex({ status: 'done', input_kind: 'command' }), now).placeholder).toMatch(/returned nothing/)
  })

  it('pending and processing wait and work, with a retry only after the system itself would have given up', () => {
    expect(exchangeView(ex({ status: 'pending' }), now)).toMatchObject({ phase: 'waiting', stale: false, canRetry: false })
    expect(exchangeView(ex({ status: 'processing' }), now)).toMatchObject({ phase: 'working', stale: false })
    const later = new Date('2026-10-24T10:16:30Z').getTime() // 16.5 minutes
    expect(exchangeView(ex({ status: 'processing' }), later)).toMatchObject({ stale: true, canRetry: true })
    expect(exchangeView(ex({ status: 'pending' }), later)).toMatchObject({ stale: true, canRetry: true })
  })

  it('answered shows the reply; failed always explains and can be retried, with or without server text', () => {
    expect(exchangeView(ex({ status: 'answered', output: { kind: 'text', title: null, text: 'Done' } }), now)).toMatchObject({ phase: 'answered', placeholder: null })
    expect(exchangeView(ex({ status: 'answered' }), now).placeholder).toMatch(/wrote no reply/)
    const failedWithText = exchangeView(ex({ status: 'failed', output: { kind: 'error', title: 'Failed', text: 'The model timed out' } }), now)
    expect(failedWithText).toMatchObject({ phase: 'failed', canRetry: true, placeholder: null })
    const fallback = exchangeView(ex({ status: 'failed' }), now)
    expect(fallback).toMatchObject({ phase: 'failed', canRetry: true })
    expect(fallback.placeholder).toMatch(/did not get an answer/)
  })

  it('knows which exchanges are still in flight', () => {
    expect(isActiveExchange(ex({ status: 'pending' }))).toBe(true)
    expect(isActiveExchange(ex({ status: 'processing' }))).toBe(true)
    for (const s of ['done', 'answered', 'failed'] as const) expect(isActiveExchange(ex({ status: s }))).toBe(false)
  })

  it('turns the actions of an answer into chips that open panels', () => {
    const chips = exchangeChips(ex({ actions: [{ type: 'goal', ref_id: 'g1', label: 'Launch' }, { type: 'task', ref_id: 't1', label: null }] }), { task: new Map([['t1', 'Write docs']]) })
    expect(chips.map((c) => [c.label, c.panel])).toEqual([
      ['Launch', { kind: 'goals' }],
      ['Write docs', { kind: 'task', id: 't1' }],
    ])
  })

  it('a refused input becomes a failed exchange with the reason; an in-flight one is optimistic', () => {
    const f = localFailure('/pause', 'Network down', new Date('2026-10-24T10:00:00Z'))
    expect(f).toMatchObject({ local: true, status: 'failed', input_kind: 'command', output: { kind: 'error', text: 'Network down' } })
    expect(optimisticExchange('/status')).toMatchObject({ status: 'processing', input_kind: 'command' })
    expect(optimisticExchange('think about pricing')).toMatchObject({ status: 'pending', input_kind: 'note' })
    expect(optimisticExchange('x').id.startsWith('pending-')).toBe(true)
  })
})

describe('structured output', () => {
  it('normalises tables from arrays or objects and keeps every cell as text', () => {
    const t = normalizeCommandOutput({ kind: 'table', title: 'Tasks', text: null, columns: ['Title', 'Status'], rows: [['A', 'done'], { Title: 'B', Status: 'blocked' }, ['C', 3 as unknown as string]] })!
    expect(t.rows).toEqual([['A', 'done'], ['B', 'blocked'], ['C', '3']])
    expect(t.columns).toEqual(['Title', 'Status'])
    const inferred = normalizeCommandOutput({ kind: 'table', title: null, text: null, rows: [{ a: 1, b: 2 }] })!
    expect(inferred.columns).toEqual(['a', 'b'])
  })

  it('lists, errors, and text pass through; null stays null', () => {
    expect(normalizeCommandOutput(null)).toBeNull()
    expect(normalizeCommandOutput({ kind: 'list', title: 'Goals', text: '', rows: ['one', ['two', 'active']] })!.rows).toEqual([['one'], ['two', 'active']])
    expect(normalizeCommandOutput({ kind: 'error', title: 'Unknown command', text: 'Try /help' })).toMatchObject({ kind: 'error', text: 'Try /help' })
    expect(normalizeCommandOutput({ kind: 'weird' as 'text', title: null, text: 'x' })!.kind).toBe('text')
  })

  it('links open known panels only', () => {
    const o = normalizeCommandOutput({ kind: 'text', title: null, text: 'x', links: [{ label: 'Tasks', panel: 'tasks' }, { label: 'One', panel: 'task:t1' }, { label: 'Nope', panel: 'javascript:alert(1)' }, { label: 'Refl', panel: 'reflection:r1' }] })!
    expect(o.links).toEqual([
      { label: 'Tasks', panel: { kind: 'tasks' } },
      { label: 'One', panel: { kind: 'task', id: 't1' } },
      { label: 'Refl', panel: { kind: 'reflection', id: 'r1' } },
    ])
    expect(panelFromString('?panel=standards')).toEqual({ kind: 'standards' })
    expect(panelFromString('../etc')).toBeNull()
  })
})
