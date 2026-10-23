import { describe, expect, it } from 'vitest'
import { SIMPLE_PANELS, legacyPanelFor, parsePanel, samePanel, serializePanel, withPanel, type Panel } from './panels'

describe('parsePanel', () => {
  it('parses every simple panel', () => {
    for (const k of SIMPLE_PANELS) expect(parsePanel(`?panel=${k}`)).toEqual({ kind: k })
  })

  it('parses id panels', () => {
    expect(parsePanel('?panel=agent:abc-123')).toEqual({ kind: 'agent', id: 'abc-123' })
    expect(parsePanel('?panel=task:t_9')).toEqual({ kind: 'task', id: 't_9' })
    expect(parsePanel('?panel=artifact:a1')).toEqual({ kind: 'artifact', id: 'a1' })
    expect(parsePanel('?panel=skill:5202d89e-12ba-490a-910e-721837f36e70')).toEqual({ kind: 'skill', id: '5202d89e-12ba-490a-910e-721837f36e70' })
  })

  it('parses the M6 guardrails panel', () => {
    expect(parsePanel('?panel=guardrails')).toEqual({ kind: 'guardrails' })
    expect(withPanel('', { kind: 'guardrails' })).toBe('?panel=guardrails')
  })

  it('parses the M5 panels: notes, memory, cofounder and a note thread', () => {
    expect(parsePanel('?panel=notes')).toEqual({ kind: 'notes' })
    expect(parsePanel('?panel=memory')).toEqual({ kind: 'memory' })
    expect(parsePanel('?panel=cofounder')).toEqual({ kind: 'cofounder' })
    expect(parsePanel('?panel=note:n-1')).toEqual({ kind: 'note', id: 'n-1' })
    expect(parsePanel('?panel=note')).toBeNull()
    expect(parsePanel('?panel=notes:1')).toBeNull()
    expect(parsePanel('?panel=note:../x')).toBeNull()
    expect(parsePanel(withPanel('', { kind: 'note', id: 'abc' }))).toEqual({ kind: 'note', id: 'abc' })
  })

  it('reads a run id alongside a task panel only', () => {
    expect(parsePanel('?panel=task:t1&run=r1')).toEqual({ kind: 'task', id: 't1', run: 'r1' })
    expect(parsePanel('?panel=agent:a1&run=r1')).toEqual({ kind: 'agent', id: 'a1' })
  })

  it('rejects unknown, malformed or unsafe values', () => {
    expect(parsePanel('')).toBeNull()
    expect(parsePanel('?panel=')).toBeNull()
    expect(parsePanel('?panel=nope')).toBeNull()
    expect(parsePanel('?panel=agent')).toBeNull() // id panel without id
    expect(parsePanel('?panel=agent:')).toBeNull()
    expect(parsePanel('?panel=tasks:1')).toBeNull() // simple panel with id
    expect(parsePanel('?panel=skills:1')).toBeNull()
    expect(parsePanel('?panel=skill')).toBeNull() // id panel without id
    expect(parsePanel('?panel=skill:../x')).toBeNull()
    expect(parsePanel('?panel=agent:../../etc')).toBeNull()
    expect(parsePanel('?panel=task:t1&run=bad/run')).toEqual({ kind: 'task', id: 't1' })
    expect(parsePanel('?panel=agent:' + 'x'.repeat(65))).toBeNull()
  })

  it('accepts URLSearchParams', () => {
    expect(parsePanel(new URLSearchParams({ panel: 'finance' }))).toEqual({ kind: 'finance' })
  })
})

describe('serializePanel / withPanel', () => {
  const samples: Panel[] = [
    { kind: 'org' },
    { kind: 'goals' },
    { kind: 'agent', id: 'a1' },
    { kind: 'task', id: 't1' },
    { kind: 'task', id: 't1', run: 'r1' },
    { kind: 'artifact', id: 'x9' },
    { kind: 'skills' },
    { kind: 'skill', id: 'abc-1' },
  ]

  it('round-trips through the query string', () => {
    for (const p of samples) expect(parsePanel(withPanel('', p))).toEqual(p)
  })

  it('serializes to the documented form', () => {
    expect(serializePanel({ kind: 'agent', id: 'a1' })).toBe('agent:a1')
    expect(serializePanel({ kind: 'tasks' })).toBe('tasks')
    expect(serializePanel({ kind: 'skill', id: 's1' })).toBe('skill:s1')
  })

  it('keeps unrelated params and replaces the panel', () => {
    expect(withPanel('?foo=1&panel=tasks', { kind: 'org' })).toBe('?foo=1&panel=org')
  })

  it('drops the run param when switching away from a task panel and when closing', () => {
    expect(withPanel('?panel=task:t1&run=r1', { kind: 'agent', id: 'a' })).toBe('?panel=agent:a')
    expect(withPanel('?panel=task:t1&run=r1&foo=1', null)).toBe('?foo=1')
    expect(withPanel('?panel=tasks', null)).toBe('')
  })
})

describe('samePanel', () => {
  it('compares kind, id and run', () => {
    expect(samePanel({ kind: 'org' }, { kind: 'org' })).toBe(true)
    expect(samePanel({ kind: 'agent', id: 'a' }, { kind: 'agent', id: 'b' })).toBe(false)
    expect(samePanel(null, null)).toBe(true)
    expect(samePanel(null, { kind: 'org' })).toBe(false)
  })
})

describe('legacyPanelFor', () => {
  it('maps old sidebar areas to panels', () => {
    expect(legacyPanelFor('organization')).toEqual({ kind: 'org' })
    expect(legacyPanelFor('projects')).toEqual({ kind: 'goals' })
    expect(legacyPanelFor('settings')).toEqual({ kind: 'settings' })
    expect(legacyPanelFor('organization/agents/a1')).toEqual({ kind: 'agent', id: 'a1' })
  })
  it('maps the old home and bare company URL to the canvas', () => {
    expect(legacyPanelFor('command-center')).toBeNull()
    expect(legacyPanelFor('')).toBeNull()
  })
  it('returns undefined for unknown areas', () => {
    expect(legacyPanelFor('nonsense')).toBeUndefined()
  })
})
