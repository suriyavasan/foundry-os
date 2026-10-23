import type { Agent, AgentLive, LiveRunRef, LiveState, LiveTaskRef } from '@/api/types'

/** Visual state of an avatar ring. */
export type RingState = 'working' | 'idle' | 'blocked' | 'paused' | 'proposed' | 'failed' | 'offline' | 'retired'

export interface RingStyle {
  /** CSS custom property (from tokens.css) that colours the ring. */
  token: string
  /** Dashed ring (not yet a real employee). */
  dashed: boolean
  /** Subtle animated pulse. Disabled by prefers-reduced-motion in CSS. */
  pulse: boolean
  /** Whole avatar is muted. */
  dimmed: boolean
  label: string
}

export const RING_STYLE: Record<RingState, RingStyle> = {
  working: { token: '--live-working', dashed: false, pulse: true, dimmed: false, label: 'Working' },
  idle: { token: '--live-idle', dashed: false, pulse: false, dimmed: false, label: 'Idle' },
  blocked: { token: '--live-blocked', dashed: false, pulse: false, dimmed: false, label: 'Blocked' },
  paused: { token: '--live-paused', dashed: false, pulse: false, dimmed: true, label: 'Paused' },
  proposed: { token: '--live-proposed', dashed: true, pulse: false, dimmed: false, label: 'Proposed' },
  failed: { token: '--live-failed', dashed: false, pulse: false, dimmed: false, label: 'Failed' },
  offline: { token: '--live-paused', dashed: false, pulse: false, dimmed: true, label: 'Offline' },
  retired: { token: '--live-paused', dashed: true, pulse: false, dimmed: true, label: 'Retired' },
}

const FAILED_RUN = new Set(['failed', 'timed_out'])

/**
 * Which ring an agent gets. Lifecycle status wins over live activity: a paused employee is paused
 * even if a stale `live` block still says working. Without a `live` block (M1 backend) an active
 * agent is simply idle: we never invent activity.
 */
export function ringStateFor(agent: Pick<Agent, 'status' | 'live'>): RingState {
  switch (agent.status) {
    case 'proposed':
      return 'proposed'
    case 'paused':
    case 'retiring':
      return 'paused'
    case 'retired':
    case 'rejected':
      return 'retired'
    default:
      break
  }
  const live = agent.live
  if (!live) return 'idle'
  switch (live.state) {
    case 'working':
      return 'working'
    case 'blocked':
      return 'blocked'
    case 'paused':
      return 'paused'
    case 'offline':
      return 'offline'
    case 'idle':
    default:
      return live.current_run?.status && FAILED_RUN.has(live.current_run.status) ? 'failed' : 'idle'
  }
}

export function ringStyleFor(agent: Pick<Agent, 'status' | 'live'>): RingStyle & { state: RingState } {
  const state = ringStateFor(agent)
  return { state, ...RING_STYLE[state] }
}

const LIVE_STATES: readonly LiveState[] = ['idle', 'working', 'blocked', 'paused', 'offline']

function asString(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null
}

function normalizeTaskRef(v: unknown): LiveTaskRef | null {
  if (typeof v === 'string' && v) return { id: v }
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    const id = asString(o.id)
    if (!id) return null
    return { id, title: asString(o.title), status: asString(o.status) as LiveTaskRef['status'] }
  }
  return null
}

function normalizeRunRef(v: unknown): LiveRunRef | null {
  if (typeof v === 'string' && v) return { id: v }
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    const id = asString(o.id)
    if (!id) return null
    return { id, status: asString(o.status) as LiveRunRef['status'], started_at: asString(o.started_at) }
  }
  return null
}

/**
 * Defensive parse of an `activity-now` payload (agent list `live` field or an `agent.activity`
 * event). The spec fixes the keys but not the shape of `current_task`/`current_run`, so both a bare
 * id string and an object are accepted. Returns null when the payload has no valid `state`.
 */
export function normalizeLive(raw: unknown): AgentLive | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const state = o.state as LiveState
  if (!LIVE_STATES.includes(state)) return null
  return {
    state,
    current_task: normalizeTaskRef(o.current_task),
    current_run: normalizeRunRef(o.current_run),
    summary: asString(o.summary),
    since: asString(o.since),
  }
}
