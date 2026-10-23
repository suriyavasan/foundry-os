import { describe, expect, it } from 'vitest'
import type { TaskStatus } from '@/api/types'
import { allowedTaskActions, isRunActive, progressOf } from './taskStatus'

const acts = (s: TaskStatus) => allowedTaskActions(s).map((a) => a.action)

describe('allowedTaskActions', () => {
  it('lets the owner block or cancel open work', () => {
    for (const s of ['backlog', 'ready', 'in_progress', 'in_review', 'revision_requested'] as const) {
      expect(acts(s)).toEqual(['block', 'cancel'])
    }
  })
  it('offers unblock (not block) for a blocked task', () => {
    expect(acts('blocked')).toEqual(['unblock', 'cancel'])
  })
  it('offers retry for a failed task and nothing destructive', () => {
    expect(acts('failed')).toEqual(['retry'])
  })
  it('offers nothing for done or cancelled tasks', () => {
    expect(acts('done')).toEqual([])
    expect(acts('cancelled')).toEqual([])
  })
  it('asks for a reason only when blocking', () => {
    expect(allowedTaskActions('ready').find((a) => a.action === 'block')?.needsReason).toBe(true)
    expect(allowedTaskActions('ready').find((a) => a.action === 'cancel')?.needsReason).toBe(false)
  })
})

describe('progressOf', () => {
  it('counts done over non-cancelled tasks', () => {
    expect(progressOf(['done', 'done', 'in_progress', 'cancelled'])).toEqual({ done: 2, total: 3 })
    expect(progressOf([])).toEqual({ done: 0, total: 0 })
  })
})

describe('isRunActive', () => {
  it('is true only for non-terminal run states', () => {
    expect(isRunActive('running')).toBe(true)
    expect(isRunActive('queued')).toBe(true)
    expect(isRunActive('succeeded')).toBe(false)
    expect(isRunActive('timed_out')).toBe(false)
  })
})
