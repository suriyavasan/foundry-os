import type { Approval, Priority, Task } from '@/api/types'
import { memberRef, taskAssignee } from './people'
import { isTerminalTask } from './taskStatus'

export interface MyWork {
  /** Open tasks assigned to me that are not reviews. */
  tasks: Task[]
  /** Open review tasks assigned to me. */
  reviews: Task[]
  /** Pending approvals a human has to decide that are mine (scoped to me, or open to any owner/admin). */
  approvals: Approval[]
}

const RANK: Record<Priority, number> = { urgent: 0, high: 1, normal: 2, low: 3 }

function byUrgency(a: Task, b: Task): number {
  const due = (t: Task) => t.due_at ?? '9999'
  return RANK[a.priority] - RANK[b.priority] || due(a).localeCompare(due(b)) || a.created_at.localeCompare(b.created_at)
}

/** A pending approval is mine when it is scoped to me, or unscoped and I may decide approvals (owner/admin). */
export function approvalIsMine(a: Approval, memberId: string, canDecideUnscoped: boolean): boolean {
  if (a.status !== 'pending' || a.approver !== 'human') return false
  return a.approver_ref ? a.approver_ref === memberRef(memberId) : canDecideUnscoped
}

export function splitMyWork(tasks: readonly Task[], approvals: readonly Approval[], memberId: string, canDecideUnscoped: boolean): MyWork {
  const mine = tasks.filter((t) => {
    const who = taskAssignee(t)
    return who?.type === 'member' && who.id === memberId && !isTerminalTask(t.status)
  })
  return {
    tasks: mine.filter((t) => t.kind !== 'review').sort(byUrgency),
    reviews: mine.filter((t) => t.kind === 'review').sort(byUrgency),
    approvals: approvals.filter((a) => approvalIsMine(a, memberId, canDecideUnscoped)),
  }
}

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024

/** Same limit the server applies to uploads (25 MB); checked first so the person is not made to wait for a 413. */
export function validateAttachment(file: { size: number } | null): string | null {
  if (!file) return null
  if (file.size === 0) return 'That file is empty'
  if (file.size > MAX_UPLOAD_BYTES) return 'Files can be at most 25 MB'
  return null
}
