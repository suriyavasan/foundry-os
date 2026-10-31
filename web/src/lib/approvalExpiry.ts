import type { Approval } from '@/api/types'

/**
 * M18 A1: every pending approval has a deadline (`expires_at`). A human approval that times out is declined, never approved; a
 * Co-founder approval that times out goes to a human. This is the plain-language line for it.
 */

/** "5 h", "20 min", "3 days", "under a minute" for a span in ms. */
export function formatSpan(ms: number): string {
  const min = Math.round(ms / 60_000)
  if (ms < 30_000) return 'under a minute'
  if (min < 1) return '1 min'
  if (min < 60) return `${min} min`
  const hours = Math.round(ms / 3_600_000)
  if (hours < 48) return `${hours} h`
  const days = Math.round(ms / 86_400_000)
  return `${days} days`
}

/** The expiry line for a pending approval, or null when there is none (no deadline, or it is not pending). */
export function expiryText(a: Pick<Approval, 'status' | 'approver' | 'expires_at'>, now: number = Date.now()): string | null {
  if (a.status !== 'pending' || !a.expires_at) return null
  const left = new Date(a.expires_at).getTime() - now
  if (Number.isNaN(left)) return null
  if (a.approver === 'founder') {
    return left <= 0 ? "Goes to you now: the Co-founder didn't decide" : `Goes to you in ${formatSpan(left)} if the Co-founder doesn't decide`
  }
  if (a.approver === 'human') {
    return left <= 0 ? 'Declines automatically any moment now' : `Declines automatically in ${formatSpan(left)}`
  }
  return null
}

/** The label of the `expired` status: the reader must know it was declined, not approved. */
export const EXPIRED_LABEL = 'Expired · declined by timeout'
