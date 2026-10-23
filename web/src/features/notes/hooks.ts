import { useCallback, useMemo, useSyncExternalStore } from 'react'
import { useAllTasks, useGoals, useProjects, useSchedules } from '@/api'
import type { RefTitles, SeenReplies } from '@/lib/notes'
import { readSeenReplies, writeSeenReplies } from '@/lib/storage'

/* Which AI replies were opened in this browser. A tiny external store so every widget and panel agrees. */
const listeners = new Set<() => void>()
const cache = new Map<string, SeenReplies>()

function snapshot(cid: string): SeenReplies {
  let v = cache.get(cid)
  if (!v) {
    v = readSeenReplies(cid)
    cache.set(cid, v)
  }
  return v
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => void listeners.delete(cb)
}

/** Test hook: forget what this browser had marked as read. */
export function resetSeenReplies() {
  cache.clear()
  listeners.forEach((l) => l())
}

/** Replies the Founder has seen, plus `markSeen(noteId, replyAt)`; unread badges derive from it. */
export function useSeenReplies(cid: string) {
  const seen = useSyncExternalStore(subscribe, () => snapshot(cid))
  const markSeen = useCallback(
    (noteId: string, at: string) => {
      const cur = snapshot(cid)
      if (cur[noteId] && cur[noteId] >= at) return
      const next = { ...cur, [noteId]: at }
      cache.set(cid, next)
      writeSeenReplies(cid, next)
      listeners.forEach((l) => l())
    },
    [cid],
  )
  return { seen, markSeen }
}

/** Names of goals, projects, tasks and schedules from the cached lists, so action chips read "Launch v1" rather than "Goal". */
export function useRefTitles(cid: string): RefTitles {
  const goals = useGoals(cid)
  const projects = useProjects(cid)
  const tasks = useAllTasks(cid)
  const schedules = useSchedules(cid)
  return useMemo(
    () => ({
      goal: new Map((goals.data ?? []).map((g) => [g.id, g.title])),
      project: new Map((projects.data ?? []).map((p) => [p.id, p.name])),
      task: new Map((tasks.data ?? []).map((t) => [t.id, t.title])),
      schedule: new Map((schedules.data ?? []).map((s) => [s.id, s.name])),
    }),
    [goals.data, projects.data, tasks.data, schedules.data],
  )
}
