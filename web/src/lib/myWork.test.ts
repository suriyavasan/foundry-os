import { describe, expect, it } from 'vitest'
import type { Approval } from '@/api/types'
import { makeTask } from '@/test/factories'
import { approvalIsMine, splitMyWork, validateAttachment } from './myWork'

const approval = (over: Partial<Approval> = {}): Approval =>
  ({ id: 'a1', company_id: 'c1', kind: 'spend', status: 'pending', approver: 'human', title: 'Buy domain', ...over }) as Approval

describe('splitMyWork', () => {
  const tasks = [
    makeTask({ id: 'low', assignee_ref: 'member:m1', priority: 'low' }),
    makeTask({ id: 'urgent', assignee_ref: 'member:m1', priority: 'urgent' }),
    makeTask({ id: 'review', assignee_ref: 'member:m1', kind: 'review' }),
    makeTask({ id: 'done', assignee_ref: 'member:m1', status: 'done' }),
    makeTask({ id: 'theirs', assignee_ref: 'member:m2' }),
    makeTask({ id: 'bot', assignee_agent_id: 'a1' }),
  ]
  it('separates work from reviews, hides finished and other people’s tasks, most urgent first', () => {
    const w = splitMyWork(tasks, [], 'm1', false)
    expect(w.tasks.map((t) => t.id)).toEqual(['urgent', 'low'])
    expect(w.reviews.map((t) => t.id)).toEqual(['review'])
  })
  it('scopes approvals: mine, or unscoped for owners and admins only', () => {
    const list = [approval({ id: 'mine', approver_ref: 'member:m1' }), approval({ id: 'theirs', approver_ref: 'member:m2' }), approval({ id: 'open' }), approval({ id: 'founder', approver: 'founder' }), approval({ id: 'decided', status: 'approved' })]
    expect(splitMyWork([], list, 'm1', false).approvals.map((a) => a.id)).toEqual(['mine'])
    expect(splitMyWork([], list, 'm1', true).approvals.map((a) => a.id)).toEqual(['mine', 'open'])
    expect(approvalIsMine(list[1]!, 'm1', true)).toBe(false)
  })
})

describe('attachments', () => {
  it('limits uploads to 25 MB and refuses empty files', () => {
    expect(validateAttachment(null)).toBeNull()
    expect(validateAttachment({ size: 1024 })).toBeNull()
    expect(validateAttachment({ size: 0 })).toMatch(/empty/)
    expect(validateAttachment({ size: 25 * 1024 * 1024 + 1 })).toMatch(/25 MB/)
  })
})
