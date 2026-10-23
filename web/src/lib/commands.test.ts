import { describe, expect, it } from 'vitest'
import { makeAgent } from '@/test/factories'
import { makeTask } from '@/test/factories'
import { buildCommands, filterCommands } from './commands'

const cmds = buildCommands({
  agents: [
    makeAgent({ id: 'a1', name: 'Atlas', role_title: 'Founder & CEO', kind: 'founder' }),
    makeAgent({ id: 'a2', name: 'Iris', role_title: 'Research Lead', status: 'paused' }),
    makeAgent({ id: 'a3', name: 'Gone', role_title: 'Designer', status: 'retired' }),
  ],
  tasks: [
    makeTask({ id: 't1', title: 'Write launch plan', status: 'in_progress' }),
    makeTask({ id: 't2', title: 'Old task', status: 'done' }),
  ],
  companyStatus: 'active',
  workEngine: true,
})
const labels = (q: string) => filterCommands(cmds, q).map((c) => c.label)

describe('buildCommands', () => {
  it('offers pause when active and resume when paused, never both', () => {
    const active = cmds.filter((c) => c.action.type === 'company').map((c) => c.id)
    expect(active).toEqual(['act:pause'])
    const paused = buildCommands({ agents: [], tasks: [], companyStatus: 'paused', workEngine: true })
    expect(paused.filter((c) => c.action.type === 'company').map((c) => c.id)).toEqual(['act:resume'])
  })
  it('hides retired agents and finished tasks', () => {
    expect(cmds.some((c) => c.id === 'agent:a3')).toBe(false)
    expect(cmds.some((c) => c.id === 't:t2' || c.id === 'task:t2')).toBe(false)
  })
  it('does not offer to create work when the work engine is not deployed', () => {
    const none = buildCommands({ agents: [], tasks: [], companyStatus: 'active', workEngine: false })
    expect(none.some((c) => c.id === 'act:task')).toBe(false)
    expect(none.some((c) => c.id === 'act:hire')).toBe(true)
  })
  it('offers the AI Co-founder commands, and hides them while the M5 backend is missing', () => {
    expect(cmds.find((c) => c.id === 'act:note')).toMatchObject({ label: 'Write a note to the AI Co-founder', action: { type: 'compose-note' } })
    expect(cmds.find((c) => c.id === 'act:wake')).toMatchObject({ label: 'Wake AI Co-founder', action: { type: 'wake-cofounder' } })
    const none = buildCommands({ agents: [], tasks: [], companyStatus: 'active', workEngine: true, autonomy: false })
    expect(none.some((c) => c.id === 'act:note' || c.id === 'act:wake')).toBe(false)
  })
  it('opens memory and notes from the palette', () => {
    expect(labels('open memory')).toContain('Open Memory')
    expect(labels('open notes')).toContain('Open Notes')
    expect(cmds.find((c) => c.id === 'panel:cofounder')?.label).toBe('Open AI Co-founder')
    expect(labels('write note')[0]).toBe('Write a note to the AI Co-founder')
    expect(labels('wake')[0]).toBe('Wake AI Co-founder')
  })
  it('has a command for every panel', () => {
    expect(cmds.filter((c) => c.group === 'Panels').length).toBeGreaterThanOrEqual(10)
  })
})

describe('filterCommands', () => {
  it('with no query lists actions first, then panels, agents and tasks', () => {
    const groups = filterCommands(cmds, '').map((c) => c.group)
    expect(groups[0]).toBe('Actions')
    expect(groups.indexOf('Panels')).toBeGreaterThan(groups.lastIndexOf('Actions'))
    expect(groups.indexOf('Agents')).toBeGreaterThan(groups.lastIndexOf('Panels'))
  })
  it('matches agents by name and by role', () => {
    expect(labels('atl')).toContain('Atlas')
    expect(labels('research')).toContain('Iris')
  })
  it('ranks label prefixes above substrings', () => {
    const r = labels('task')
    expect(r[0]).toBe('Create task')
    expect(r).toContain('Open Tasks')
  })
  it('requires every token to match (AND)', () => {
    expect(labels('create goal')).toEqual(['Create goal'])
    expect(labels('create zebra')).toEqual([])
  })
  it('is case-insensitive and tolerates punctuation', () => {
    expect(labels('PAUSE')).toContain('Pause company')
    expect(labels('open  org')).toContain('Open Organization')
  })
  it('finds tasks by title', () => {
    expect(labels('launch')).toEqual(['Write launch plan'])
  })
  it('uses keywords', () => {
    expect(labels('kill')).toContain('Pause company')
    expect(labels('dark')).toContain('Toggle theme')
  })
  it('returns nothing for a query that matches nothing', () => {
    expect(labels('qqqqq')).toEqual([])
  })
})
