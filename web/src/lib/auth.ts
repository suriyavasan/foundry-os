import { ApiError } from '@/api/client'
import type { AuthSession, AuthState, AuthUser } from '@/api/types'

/* ------------------------------ session normalising ------------------------------ */

const STATES: readonly AuthState[] = ['setup_required', 'unauthenticated', 'authenticated']
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

function normalizeUser(raw: unknown): AuthUser | null {
  const o = obj(raw)
  const id = str(o?.id)
  if (!o || !id) return null
  const username = str(o.username) ?? str(o.email) ?? id
  const twoFactor = o.two_factor_enabled ?? o.totp_enabled
  return {
    id,
    username,
    display_name: str(o.display_name) ?? username,
    email: str(o.email),
    ...(typeof twoFactor === 'boolean' ? { two_factor_enabled: twoFactor } : null),
  }
}

/**
 * Read `GET /auth/session` / login / setup responses defensively. An unknown `state` is derived from whether a
 * user came with it; a user without a state means authenticated. Nothing is invented: a response that carries
 * neither is `unauthenticated`.
 */
export function normalizeAuthSession(raw: unknown): AuthSession {
  const o = obj(raw) ?? {}
  const user = normalizeUser(o.user)
  const named = str(o.state)
  const twoFactorState = named === 'two_factor_required'
  const state: AuthState = (STATES as readonly string[]).includes(named ?? '') ? (named as AuthState) : user && !twoFactorState ? 'authenticated' : 'unauthenticated'
  return {
    state,
    user: state === 'authenticated' ? user : null,
    ...(o.setup_token_required === true ? { setup_token_required: true } : null),
    ...(o.two_factor_required === true || twoFactorState ? { two_factor_required: true } : null),
    ...(str(o.expires_at) ? { expires_at: str(o.expires_at) } : null),
    ...(typeof o.passkeys_count === 'number' ? { passkeys_count: o.passkeys_count } : null),
    ...(typeof o.passkey_supported_origin === 'boolean' ? { passkey_supported_origin: o.passkey_supported_origin } : null),
  }
}

/* --------------------------------- the state machine --------------------------------- */

/**
 * What the app shell should show. `legacy` is a backend that predates M7 (no `/auth/session`): the owner-token
 * gate of M3b-sec stays exactly as it was.
 */
export type AuthStatus =
  | { kind: 'loading' }
  | { kind: 'legacy' }
  | { kind: 'error'; error: unknown }
  | { kind: 'setup_required'; setupTokenRequired: boolean }
  | { kind: 'unauthenticated' }
  | { kind: 'authenticated'; session: AuthSession }

/** The backend does not serve this endpoint (yet). */
export const isMissingEndpoint = (err: unknown): boolean => err instanceof ApiError && (err.status === 404 || err.status === 405)

export function authStatusOf(q: { isPending: boolean; data: AuthSession | undefined; error: unknown }): AuthStatus {
  if (q.data) {
    switch (q.data.state) {
      case 'setup_required':
        return { kind: 'setup_required', setupTokenRequired: q.data.setup_token_required === true }
      case 'authenticated':
        return q.data.user ? { kind: 'authenticated', session: q.data } : { kind: 'unauthenticated' }
      default:
        return { kind: 'unauthenticated' }
    }
  }
  if (q.error) return isMissingEndpoint(q.error) ? { kind: 'legacy' } : { kind: 'error', error: q.error }
  return q.isPending ? { kind: 'loading' } : { kind: 'legacy' }
}

/* ------------------------------------ usernames ------------------------------------ */

export const USERNAME_PATTERN = /^[a-z0-9._-]{3,32}$/
export const DISPLAY_NAME_MAX = 120
export const EMAIL_MAX = 254

export function validateUsername(value: string): string | null {
  if (value.length === 0) return 'Choose a username'
  if (value.length < 3) return 'Use at least 3 characters'
  if (value.length > 32) return 'Use at most 32 characters'
  if (/[A-Z]/.test(value)) return 'Use lowercase letters only'
  if (!USERNAME_PATTERN.test(value)) return 'Use only lowercase letters, digits, dots, dashes and underscores'
  return null
}

export function validateDisplayName(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Enter the name people should see'
  if (v.length > DISPLAY_NAME_MAX) return `Use at most ${DISPLAY_NAME_MAX} characters`
  return null
}

/** Deliberately loose: the server (and the first email) decide whether the address is real. */
export function validateEmail(value: string, { required = false }: { required?: boolean } = {}): string | null {
  const v = value.trim()
  if (!v) return required ? 'Enter an email address' : null
  if (v.length > EMAIL_MAX || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return 'Enter a valid email address'
  return null
}

/** A suggestion for the username field, from the display name. */
export function suggestUsername(displayName: string): string {
  const base = displayName
    .trim()
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/\s+/g, '.')
    .replace(/[^a-z0-9._-]/g, '')
    .replace(/^[._-]+|[._-]+$/g, '')
  return base.slice(0, 32)
}

/** Six digits, with spaces tolerated. */
export function normalizeTotp(value: string): string {
  return value.replace(/\s+/g, '')
}
export const isTotpCode = (value: string): boolean => /^\d{6}$/.test(normalizeTotp(value))

/* ------------------------------------ login errors ------------------------------------ */

export type LoginFailureKind = 'invalid' | 'second_factor_invalid' | 'two_factor_required' | 'rate_limited' | 'locked' | 'network' | 'other'

export interface LoginFailure {
  kind: LoginFailureKind
  /** Always generic for credential problems: never says whether the username exists. */
  message: string
  retryAfterSeconds: number | null
}

export function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`
  const m = Math.round(seconds / 60)
  if (m < 90) return `${m} minutes`
  return `${Math.round(m / 60)} hours`
}

export function retryAfter(err: ApiError): number | null {
  const d = obj(err.details)
  for (const key of ['retry_after_seconds', 'retry_after']) {
    const v = d?.[key]
    if (typeof v === 'number' && Number.isFinite(v) && v > 0) return v
  }
  return null
}

export const reasonOf = (err: ApiError): string | null => str(obj(err.details)?.reason)

/** Map a failed login to what the form says. `secondFactor` is true while the authenticator-code step is showing. */
export function describeLoginFailure(err: unknown, { secondFactor = false }: { secondFactor?: boolean } = {}): LoginFailure {
  if (!(err instanceof ApiError)) return { kind: 'other', message: 'Could not sign in. Try again.', retryAfterSeconds: null }
  if (err.code === 'network_error') return { kind: 'network', message: err.message, retryAfterSeconds: null }
  const reason = reasonOf(err)
  if (err.code === 'two_factor_required' || reason === 'two_factor_required') {
    return { kind: 'two_factor_required', message: 'Enter the code from your authenticator app.', retryAfterSeconds: null }
  }
  if (err.status === 429 || err.code === 'rate_limited' || err.code === 'too_many_attempts') {
    return { kind: 'rate_limited', message: 'Too many sign-in attempts.', retryAfterSeconds: retryAfter(err) }
  }
  if (err.status === 423 || err.code === 'locked' || err.code === 'account_locked') {
    return { kind: 'locked', message: 'Sign-in is paused after repeated failed attempts.', retryAfterSeconds: retryAfter(err) }
  }
  if (err.status === 401 || err.status === 400 || err.status === 403 || err.status === 422) {
    return secondFactor
      ? { kind: 'second_factor_invalid', message: 'That code was not accepted. Check your authenticator app and try again.', retryAfterSeconds: null }
      : { kind: 'invalid', message: 'Incorrect username or password.', retryAfterSeconds: null }
  }
  return { kind: 'other', message: err.status >= 500 ? 'The server had a problem. Try again in a moment.' : err.message, retryAfterSeconds: null }
}

/**
 * What the form shows for a failure. A wait the server quantified counts down (`remaining` seconds) and the message
 * disappears when it reaches zero; one it did not quantify says so plainly.
 */
export function loginFailureText(f: LoginFailure, remaining: number): string | null {
  if (f.kind === 'rate_limited' || f.kind === 'locked') {
    if (f.retryAfterSeconds === null) return `${f.message} Try again in a little while.`
    return remaining > 0 ? `${f.message} Try again in ${formatWait(remaining)}.` : null
  }
  return f.message
}

/** A successful-looking login response that actually asks for the second factor. */
export const loginNeedsSecondFactor = (res: AuthSession): boolean => res.two_factor_required === true && res.state !== 'authenticated'

/* ------------------------------------ setup errors ------------------------------------ */

export const SETUP_TOKEN_CODES = new Set(['setup_token_required', 'invalid_setup_token', 'setup_token_invalid'])

export interface SetupFailure {
  fields: Partial<Record<'username' | 'display_name' | 'password' | 'email' | 'setup_token', string>>
  form: string | null
  /** The server wants (or rejected) the setup token: show the field even if the session did not say so. */
  needsToken: boolean
  /** Someone else finished setup first. */
  alreadySetUp: boolean
}

export function describeSetupFailure(err: unknown): SetupFailure {
  if (!(err instanceof ApiError)) return { fields: {}, form: 'Could not create the account. Try again.', needsToken: false, alreadySetUp: false }
  const reason = reasonOf(err)
  if (SETUP_TOKEN_CODES.has(err.code) || (reason && SETUP_TOKEN_CODES.has(reason))) {
    const missing = err.code === 'setup_token_required' || reason === 'setup_token_required'
    return {
      fields: { setup_token: missing ? 'Enter the setup token printed by the server.' : 'That setup token was not accepted. Copy it again from the server terminal.' },
      form: null,
      needsToken: true,
      alreadySetUp: false,
    }
  }
  if (err.status === 409 || err.code === 'already_set_up' || err.code === 'setup_complete') {
    return { fields: {}, form: 'This install already has an owner account. Sign in instead.', needsToken: false, alreadySetUp: true }
  }
  if (err.status === 422 || err.code === 'validation_error') {
    const fe = err.fieldErrors
    const fields: SetupFailure['fields'] = {}
    for (const k of ['username', 'display_name', 'password', 'email', 'setup_token'] as const) if (fe[k]) fields[k] = fe[k]
    return { fields, form: Object.keys(fields).length ? null : err.message, needsToken: !!fields.setup_token, alreadySetUp: false }
  }
  if (err.status === 429) return { fields: {}, form: 'Too many attempts. Wait a moment and try again.', needsToken: false, alreadySetUp: false }
  return { fields: {}, form: err.message, needsToken: false, alreadySetUp: false }
}
