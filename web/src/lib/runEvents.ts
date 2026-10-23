import type { RunEvent } from '@/api/types'

/** Merge run events idempotently by `seq` (the server insert is idempotent on (run_id, seq) too). */
export function mergeRunEvents(prev: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  if (incoming.length === 0) return prev
  const bySeq = new Map<number, RunEvent>()
  for (const e of prev) bySeq.set(e.seq, e)
  for (const e of incoming) bySeq.set(e.seq, e)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

export function lastSeq(events: RunEvent[]): number {
  return events.length === 0 ? 0 : events[events.length - 1]!.seq
}

/** One-line text for a run event row. Falls back to the event type; never invents content. */
export function describeRunEvent(e: RunEvent): string {
  const p = e.payload ?? {}
  for (const k of ['message', 'summary', 'title', 'text', 'content', 'status', 'name', 'tool']) {
    const v = p[k]
    if (typeof v === 'string' && v.trim()) return v
  }
  return e.type.replace(/[._]/g, ' ')
}
