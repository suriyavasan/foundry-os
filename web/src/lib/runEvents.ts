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

/* ---------------------------- work graph steps (M18 Part B) ---------------------------- */

/** The self-check may send an agent back to work at most this many times (`MAX_REVISIONS` in the work graph). */
export const MAX_GRAPH_REVISIONS = 2

export const isGraphStep = (e: Pick<RunEvent, 'type'>): boolean => e.type === 'graph.node'

/**
 * A `graph.node` run event ({node, outcome, summary, ...}) as a short plain-language step line: "Self-check passed",
 * "Revising (1/2)", "Waiting for your answer". An outcome this client does not know yet falls back to the server's own summary
 * (`prepare: ok`), never to an invented sentence.
 */
export function describeGraphStep(e: Pick<RunEvent, 'payload'>): string {
  const p = e.payload ?? {}
  const node = typeof p.node === 'string' ? p.node : ''
  const outcome = typeof p.outcome === 'string' ? p.outcome : ''
  const [base = '', ...rest] = outcome.split('/')
  const key = `${node}:${base}`
  switch (key) {
    case 'prepare:ok':
      return 'Prepared the task'
    case 'act:proposed':
      return 'Proposed a result'
    case 'act:completed':
      return 'Finished the work'
    case 'act:failed':
      return 'The attempt failed'
    case 'act:cancelled':
    case 'check:cancelled':
      return 'Cancelled'
    case 'act:waiting_founder':
      return 'Asked you a question'
    case 'act:waiting_approval':
      return 'Needs an approval first'
    case 'act:session_lost':
      return 'Lost its earlier session, starting fresh'
    case 'check:passed':
      return 'Self-check passed'
    case 'check:failed': {
      if (rest.includes('revise')) {
        const n = typeof p.revisions === 'number' ? p.revisions : null
        return n === null ? 'Revising' : `Revising (${n}/${MAX_GRAPH_REVISIONS})`
      }
      return 'Self-check found problems; sent for review'
    }
    case 'check:inconclusive':
      return 'Self-check was inconclusive'
    case 'check:skipped':
      return 'Self-check skipped'
    case 'wait_founder:waiting':
      return 'Waiting for your answer'
    case 'wait_founder:resumed':
      return 'Got your answer, continuing'
    case 'wait_approval:waiting':
      return 'Waiting for an approval'
    case 'wait_approval:resumed':
      return 'Approval decided, continuing'
    case 'finish:completed':
    case 'finish:checked':
      return 'Finished'
    case 'graph:resumed_after_wait':
      return 'Resumed after waiting'
    case 'graph:continued_from_checkpoint':
      return 'Continued from where the earlier run stopped'
  }
  if (node === 'finish') return 'Finished'
  if (typeof p.summary === 'string' && p.summary.trim()) return p.summary
  return `${node} ${outcome}`.trim() || 'Step'
}
