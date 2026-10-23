import type { Priority, RunStatus, TaskAction, TaskStatus } from '@/api/types'

export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'accent'

export const TASK_STATUSES: TaskStatus[] = [
  'in_progress',
  'in_review',
  'revision_requested',
  'blocked',
  'ready',
  'backlog',
  'failed',
  'done',
  'cancelled',
]

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  backlog: 'Backlog',
  ready: 'Ready',
  in_progress: 'In progress',
  in_review: 'In review',
  revision_requested: 'Revision requested',
  blocked: 'Blocked',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const TASK_STATUS_TONE: Record<TaskStatus, StatusTone> = {
  backlog: 'neutral',
  ready: 'info',
  in_progress: 'accent',
  in_review: 'info',
  revision_requested: 'warning',
  blocked: 'warning',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
}

export const TERMINAL: ReadonlySet<TaskStatus> = new Set(['done', 'failed', 'cancelled'])
export const isTerminalTask = (s: TaskStatus) => TERMINAL.has(s)

/** Statuses the Work widget lists as "needs attention / in motion". */
export const ACTIVE_WORK: ReadonlySet<TaskStatus> = new Set(['in_progress', 'blocked', 'in_review', 'revision_requested'])

export interface TaskActionSpec {
  action: TaskAction
  label: string
  /** Asks for a reason (sent as `{reason}`). */
  needsReason: boolean
  destructive: boolean
}

const ACTIONS: Record<TaskAction, TaskActionSpec> = {
  cancel: { action: 'cancel', label: 'Cancel task', needsReason: false, destructive: true },
  block: { action: 'block', label: 'Block', needsReason: true, destructive: false },
  unblock: { action: 'unblock', label: 'Unblock', needsReason: false, destructive: false },
  retry: { action: 'retry', label: 'Retry', needsReason: false, destructive: false },
}

/**
 * Manual actions the owner may take, from the M2 state machine: cancel any non-terminal task, block any
 * open (non-blocked) task, unblock a blocked one, retry a failed one. The server stays the authority
 * (409 `invalid_transition`).
 */
export function allowedTaskActions(status: TaskStatus): TaskActionSpec[] {
  const out: TaskAction[] = []
  if (status === 'blocked') out.push('unblock')
  else if (!TERMINAL.has(status)) out.push('block')
  if (status === 'failed') out.push('retry')
  if (!TERMINAL.has(status)) out.push('cancel')
  return out.map((a) => ACTIONS[a])
}

export const PRIORITIES: Priority[] = ['urgent', 'high', 'normal', 'low']
export const PRIORITY_LABEL: Record<Priority, string> = { urgent: 'Urgent', high: 'High', normal: 'Normal', low: 'Low' }
export const PRIORITY_TONE: Record<Priority, StatusTone> = { urgent: 'danger', high: 'warning', normal: 'neutral', low: 'neutral' }

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: 'Queued',
  starting: 'Starting',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
  timed_out: 'Timed out',
}
export const RUN_STATUS_TONE: Record<RunStatus, StatusTone> = {
  queued: 'neutral',
  starting: 'info',
  running: 'accent',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  timed_out: 'danger',
}
export const isRunActive = (s: RunStatus) => s === 'queued' || s === 'starting' || s === 'running'

/** Done / total over a set of tasks (cancelled tasks are excluded from the denominator). */
export function progressOf(statuses: TaskStatus[]): { done: number; total: number } {
  let done = 0
  let total = 0
  for (const s of statuses) {
    if (s === 'cancelled') continue
    total++
    if (s === 'done') done++
  }
  return { done, total }
}
