import { ApiError } from '@/api/client'
import type { InvitationCreated, InvitationStatus } from '@/api/types'

/** Invitations live seven days (docs/specs/m7 §4). Shown as a fact about the link, never computed from the clock. */
export const INVITE_VALID_DAYS = 7

const TOKEN_RE = /^[A-Za-z0-9_-]{16,256}$/
/** A syntactically plausible invitation token (the server decides whether it is valid). */
export const looksLikeInviteToken = (t: string | undefined): t is string => !!t && TOKEN_RE.test(t)

/**
 * The shareable link for a freshly created invitation: the server's `invite_url` (made absolute against this
 * origin when relative), else built from the one-time token. Null when the server gave neither.
 */
export function inviteLink(created: Pick<InvitationCreated, 'invite_url' | 'token'>, origin: string): string | null {
  const url = created.invite_url
  if (url) return /^https?:\/\//i.test(url) ? url : `${origin.replace(/\/$/, '')}${url.startsWith('/') ? '' : '/'}${url}`
  if (created.token) return `${origin.replace(/\/$/, '')}/invite/${encodeURIComponent(created.token)}`
  return null
}

export type InviteOutcome =
  | { kind: 'email'; text: string }
  | { kind: 'link'; text: string; link: string }
  | { kind: 'link_missing'; text: string }

/** What the invite dialog tells the Founder after creating an invitation. */
export function inviteOutcome(created: InvitationCreated, origin: string): InviteOutcome {
  if (created.delivery === 'email') return { kind: 'email', text: `An invitation email was sent to ${created.email}.` }
  const link = inviteLink(created, origin)
  if (!link) return { kind: 'link_missing', text: 'The invitation was created, but the server did not return a link. Revoke it and invite again.' }
  return { kind: 'link', link, text: 'No email provider is connected, so nothing was sent. Share this link yourself.' }
}

export const INVITATION_STATUS_TONE: Record<InvitationStatus, 'warning' | 'success' | 'neutral' | 'danger'> = {
  pending: 'warning',
  accepted: 'success',
  expired: 'neutral',
  revoked: 'danger',
}
export const INVITATION_STATUS_LABEL: Record<InvitationStatus, string> = { pending: 'Pending', accepted: 'Accepted', expired: 'Expired', revoked: 'Revoked' }

/** Invitations that can still be revoked. */
export const isRevocable = (status: InvitationStatus): boolean => status === 'pending'

export type InviteFailureKind = 'invalid' | 'wrong_account' | 'username_taken' | 'validation' | 'network' | 'other'

export interface InviteFailure {
  kind: InviteFailureKind
  message: string
  fields: Partial<Record<'username' | 'display_name' | 'password', string>>
}

/**
 * Turn a failed accept into words. An unknown, expired, used or revoked token all read the same on purpose, so the
 * page cannot be used to probe which tokens exist.
 */
export function describeInviteFailure(err: unknown): InviteFailure {
  if (!(err instanceof ApiError)) return { kind: 'other', message: 'Could not accept the invitation. Try again.', fields: {} }
  if (err.code === 'network_error') return { kind: 'network', message: err.message, fields: {} }
  if (err.status === 404 || err.status === 410 || /^invalid_invitation|invitation_(invalid|expired|revoked|used)/.test(err.code)) {
    return {
      kind: 'invalid',
      message: 'This invitation link is not valid. It may have expired, already been used, or been revoked. Ask the person who invited you for a new one.',
      fields: {},
    }
  }
  if (err.code === 'email_mismatch' || err.code === 'wrong_account') {
    return { kind: 'wrong_account', message: 'This invitation was sent to a different email address than the account you are signed in with. Sign out and use the right account.', fields: {} }
  }
  if (err.status === 409) return { kind: 'username_taken', message: err.message, fields: { username: 'That username is already taken' } }
  if (err.status === 422) {
    const fe = err.fieldErrors
    const fields: InviteFailure['fields'] = {}
    for (const k of ['username', 'display_name', 'password'] as const) if (fe[k]) fields[k] = fe[k]
    return { kind: 'validation', message: Object.keys(fields).length ? '' : err.message, fields }
  }
  return { kind: 'other', message: err.message, fields: {} }
}
