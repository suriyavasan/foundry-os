import type { AgentKind, AgentLifecycleAction, AgentStatus } from '@/api/types'

export interface LifecycleActionSpec {
  action: AgentLifecycleAction
  label: string
  /** Needs a confirmation dialog explaining consequences. */
  confirm: boolean
  destructive: boolean
  variant: 'primary' | 'secondary' | 'danger'
  consequence: string
}

const SPECS: Record<AgentLifecycleAction, LifecycleActionSpec> = {
  activate: {
    action: 'activate',
    label: 'Activate',
    confirm: false,
    destructive: false,
    variant: 'primary',
    consequence: 'The agent becomes an active employee.',
  },
  reject: {
    action: 'reject',
    label: 'Reject',
    confirm: true,
    destructive: true,
    variant: 'danger',
    consequence:
      'The proposed hire is rejected and cannot be activated afterwards. The record and its history are kept for audit.',
  },
  pause: {
    action: 'pause',
    label: 'Pause',
    confirm: false,
    destructive: false,
    variant: 'secondary',
    consequence: 'The agent stops being eligible for new work until resumed.',
  },
  resume: {
    action: 'resume',
    label: 'Resume',
    confirm: false,
    destructive: false,
    variant: 'primary',
    consequence: 'The agent becomes active again.',
  },
  retire: {
    action: 'retire',
    label: 'Retire',
    confirm: true,
    destructive: true,
    variant: 'danger',
    consequence:
      'The agent is retired immediately and becomes read-only. This cannot be undone. History and audit records are preserved; nothing is deleted.',
  },
}

/** Mirrors the transition table in docs/api.md. Server remains the authority (409 invalid_transition). */
const BY_STATUS: Record<AgentStatus, AgentLifecycleAction[]> = {
  proposed: ['activate', 'reject'],
  active: ['pause', 'retire'],
  paused: ['resume', 'retire'],
  retiring: [],
  retired: [],
  rejected: [],
}

export function availableLifecycleActions(
  status: AgentStatus,
  kind?: AgentKind,
): LifecycleActionSpec[] {
  // The server never retires the founder in M1 (409); M7 adds founder succession.
  return (BY_STATUS[status] ?? [])
    .filter((a) => !(a === 'retire' && kind === 'founder'))
    .map((a) => SPECS[a])
}

export function isReadOnlyStatus(status: AgentStatus): boolean {
  return status === 'retired'
}

export const pastTense: Record<AgentLifecycleAction, string> = {
  activate: 'activated',
  reject: 'rejected',
  pause: 'paused',
  resume: 'resumed',
  retire: 'retired',
}
