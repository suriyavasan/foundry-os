import { describe, expect, it } from 'vitest'
import type { HumanWorkItem } from '@/api/types'
import { canDeclineHumanWork, canTakeHumanWork, categoryLabel, dueText, filterHumanWork, holderText, sortHumanWork } from './humanWork'

const item = (over: Partial<HumanWorkItem>): HumanWorkItem => ({
  id: 'h1',
  company_id: 'c1',
  title: 'Sign the NDA',
  description: null,
  instructions: 'Sign page 3',
  category: 'signature',
  status: 'backlog',
  priority: 'normal',
  due_at: '2026-11-03T10:00:00Z',
  overdue: false,
  assignee_ref: null,
  assignee_name: null,
  pool: true,
  requested_by_agent_id: 'a1',
  requested_by_agent_name: 'Nova',
  project_id: null,
  blocking_task_ids: [],
  result_summary: null,
  output_refs: [],
  created_at: '2026-11-01T10:00:00Z',
  completed_at: null,
  ...over,
})

describe('human work helpers', () => {
  it('labels categories and falls back for an unknown one', () => {
    expect(categoryLabel('call')).toBe('Phone call')
    expect(categoryLabel(null)).toBe('Needs a person')
    expect(categoryLabel('teleport')).toBe('Needs a person')
  })

  it('puts overdue first, then the earliest due date', () => {
    const a = item({ id: 'a', due_at: '2026-11-05T00:00:00Z' })
    const b = item({ id: 'b', due_at: '2026-11-09T00:00:00Z', overdue: true })
    const c = item({ id: 'c', due_at: '2026-11-04T00:00:00Z' })
    expect(sortHumanWork([a, b, c]).map((i) => i.id)).toEqual(['b', 'c', 'a'])
  })

  it('filters mine and the pool', () => {
    const mine = item({ id: 'm', pool: false, assignee_ref: 'member:me', assignee_name: 'Me' })
    const theirs = item({ id: 't', pool: false, assignee_ref: 'member:other', assignee_name: 'Other' })
    const pool = item({ id: 'p' })
    const all = [mine, theirs, pool]
    expect(filterHumanWork(all, 'mine', 'me').map((i) => i.id)).toEqual(['m'])
    expect(filterHumanWork(all, 'mine', null)).toEqual([])
    expect(filterHumanWork(all, 'pool', 'me').map((i) => i.id)).toEqual(['p'])
    expect(filterHumanWork(all, 'all', 'me')).toHaveLength(3)
  })

  it('says who may take and who may decline', () => {
    expect(['owner', 'admin', 'member'].every(canTakeHumanWork)).toBe(true)
    expect(canTakeHumanWork('viewer')).toBe(false)
    expect(canTakeHumanWork(undefined)).toBe(false)
    expect(canDeclineHumanWork('admin')).toBe(true)
    expect(canDeclineHumanWork('member')).toBe(false)
  })

  it('describes the due date', () => {
    const now = Date.parse('2026-11-01T12:00:00Z')
    expect(dueText(null, now)).toBeNull()
    expect(dueText('not a date', now)).toBeNull()
    expect(dueText('2026-11-01T09:00:00Z', now)).toEqual({ text: 'Overdue by 3 h', tone: 'danger' })
    expect(dueText('2026-11-01T12:20:00Z', now)).toEqual({ text: 'Due in less than an hour', tone: 'warning' })
    expect(dueText('2026-11-02T18:00:00Z', now)).toEqual({ text: 'Due in 30 h', tone: 'neutral' })
    expect(dueText('2026-11-08T12:00:00Z', now)?.text).toBe('Due in 7 days')
  })

  it('names the holder', () => {
    expect(holderText(item({}))).toContain('pool')
    expect(holderText(item({ pool: false, assignee_name: 'Bob' }))).toBe('Taken by Bob')
  })
})
