import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import {
  authStatusOf,
  describeLoginFailure,
  describeSetupFailure,
  formatWait,
  isMissingEndpoint,
  isTotpCode,
  loginFailureText,
  loginNeedsSecondFactor,
  normalizeAuthSession,
  normalizeTotp,
  suggestUsername,
  validateDisplayName,
  validateEmail,
  validateUsername,
} from './auth'

const user = { id: 'u1', username: 'maya', display_name: 'Maya', email: null }

describe('auth state machine', () => {
  it('is loading until the session answers', () => {
    expect(authStatusOf({ isPending: true, data: undefined, error: null })).toEqual({ kind: 'loading' })
  })

  it('maps the three server states', () => {
    expect(authStatusOf({ isPending: false, data: { state: 'setup_required', user: null, setup_token_required: true }, error: null })).toEqual({ kind: 'setup_required', setupTokenRequired: true })
    expect(authStatusOf({ isPending: false, data: { state: 'setup_required', user: null }, error: null })).toEqual({ kind: 'setup_required', setupTokenRequired: false })
    expect(authStatusOf({ isPending: false, data: { state: 'unauthenticated', user: null }, error: null })).toEqual({ kind: 'unauthenticated' })
    const session = { state: 'authenticated' as const, user }
    expect(authStatusOf({ isPending: false, data: session, error: null })).toEqual({ kind: 'authenticated', session })
  })

  it('never treats an authenticated state without a user as signed in', () => {
    expect(authStatusOf({ isPending: false, data: { state: 'authenticated', user: null }, error: null })).toEqual({ kind: 'unauthenticated' })
  })

  it('keeps the legacy owner-token flow when /auth/session is 404 or 405', () => {
    for (const status of [404, 405]) {
      expect(authStatusOf({ isPending: false, data: undefined, error: new ApiError(status, 'not_found', 'x') })).toEqual({ kind: 'legacy' })
    }
    expect(isMissingEndpoint(new ApiError(404, 'x', 'x'))).toBe(true)
    expect(isMissingEndpoint(new ApiError(500, 'x', 'x'))).toBe(false)
  })

  it('surfaces other failures instead of pretending to be legacy', () => {
    const e = new ApiError(500, 'internal_error', 'boom')
    expect(authStatusOf({ isPending: false, data: undefined, error: e })).toEqual({ kind: 'error', error: e })
  })
})

describe('normalizeAuthSession', () => {
  it('reads the documented shape', () => {
    expect(normalizeAuthSession({ state: 'authenticated', user: { ...user, two_factor_enabled: true } })).toEqual({ state: 'authenticated', user: { ...user, two_factor_enabled: true } })
  })
  it('derives the state from the user when it is missing, and never invents one', () => {
    expect(normalizeAuthSession({ user }).state).toBe('authenticated')
    expect(normalizeAuthSession({}).state).toBe('unauthenticated')
    expect(normalizeAuthSession(null).user).toBeNull()
  })
  it('accepts totp_enabled as the 2FA flag and drops the user unless authenticated', () => {
    expect(normalizeAuthSession({ state: 'authenticated', user: { ...user, totp_enabled: true } }).user?.two_factor_enabled).toBe(true)
    expect(normalizeAuthSession({ state: 'unauthenticated', user }).user).toBeNull()
  })
  it('recognises a two-factor-required answer in either spelling', () => {
    expect(loginNeedsSecondFactor(normalizeAuthSession({ state: 'unauthenticated', two_factor_required: true }))).toBe(true)
    expect(loginNeedsSecondFactor(normalizeAuthSession({ state: 'two_factor_required' }))).toBe(true)
    expect(loginNeedsSecondFactor(normalizeAuthSession({ state: 'authenticated', user }))).toBe(false)
  })
})

describe('usernames and names', () => {
  it('accepts [a-z0-9._-]{3,32}', () => {
    for (const ok of ['abc', 'maya.chen', 'a_b-c.9', 'x'.repeat(32)]) expect(validateUsername(ok), ok).toBeNull()
  })
  it('rejects everything else with a specific reason', () => {
    expect(validateUsername('')).toMatch(/Choose/)
    expect(validateUsername('ab')).toMatch(/at least 3/)
    expect(validateUsername('x'.repeat(33))).toMatch(/at most 32/)
    expect(validateUsername('Maya')).toMatch(/lowercase/)
    expect(validateUsername('ma ya')).toMatch(/only lowercase/)
    expect(validateUsername('maya@home')).toMatch(/only lowercase/)
  })
  it('validates the display name and optional email', () => {
    expect(validateDisplayName('  ')).toMatch(/Enter/)
    expect(validateDisplayName('Maya')).toBeNull()
    expect(validateEmail('')).toBeNull()
    expect(validateEmail('', { required: true })).toMatch(/Enter/)
    expect(validateEmail('nope')).toMatch(/valid/)
    expect(validateEmail('maya@example.com')).toBeNull()
  })
  it('suggests a username from a name', () => {
    expect(suggestUsername('Maya Chen')).toBe('maya.chen')
    expect(suggestUsername('  José  Ñandú ')).toBe('jose.nandu')
    expect(suggestUsername('😀')).toBe('')
    expect(validateUsername(suggestUsername('Maya Chen'))).toBeNull()
  })
  it('normalises authenticator codes', () => {
    expect(normalizeTotp('123 456')).toBe('123456')
    expect(isTotpCode('123 456')).toBe(true)
    expect(isTotpCode('12345')).toBe(false)
    expect(isTotpCode('12345a')).toBe(false)
  })
})

describe('login failures are generic', () => {
  it('gives one message for bad credentials, whatever the status', () => {
    for (const s of [400, 401, 403, 422]) {
      const f = describeLoginFailure(new ApiError(s, 'unauthenticated', 'No such user "maya"'))
      expect(f.kind).toBe('invalid')
      expect(f.message).toBe('Incorrect username or password.')
    }
  })
  it('words a wrong authenticator code differently', () => {
    expect(describeLoginFailure(new ApiError(401, 'x', 'x'), { secondFactor: true }).kind).toBe('second_factor_invalid')
  })
  it('detects a second-factor request from the code or the reason', () => {
    expect(describeLoginFailure(new ApiError(401, 'two_factor_required', 'x')).kind).toBe('two_factor_required')
    expect(describeLoginFailure(new ApiError(401, 'unauthenticated', 'x', { reason: 'two_factor_required' })).kind).toBe('two_factor_required')
  })
  it('reports rate limits and lockouts with the wait the server gave', () => {
    const rl = describeLoginFailure(new ApiError(429, 'rate_limited', 'x', { retry_after_seconds: 45 }))
    expect(rl).toMatchObject({ kind: 'rate_limited', retryAfterSeconds: 45 })
    expect(loginFailureText(rl, 30)).toBe('Too many sign-in attempts. Try again in 30 seconds.')
    expect(loginFailureText(rl, 0)).toBeNull()
    const locked = describeLoginFailure(new ApiError(423, 'account_locked', 'x', { retry_after: 600 }))
    expect(locked.kind).toBe('locked')
    expect(loginFailureText(locked, 600)).toMatch(/10 minutes/)
    const unknown = describeLoginFailure(new ApiError(429, 'rate_limited', 'x'))
    expect(loginFailureText(unknown, 0)).toBe('Too many sign-in attempts. Try again in a little while.')
  })
  it('handles network and server errors', () => {
    expect(describeLoginFailure(new ApiError(0, 'network_error', 'Could not reach')).kind).toBe('network')
    expect(describeLoginFailure(new ApiError(500, 'internal_error', 'boom')).message).toMatch(/server had a problem/)
    expect(describeLoginFailure(new Error('x')).kind).toBe('other')
  })
  it('formats waits', () => {
    expect(formatWait(30)).toBe('30 seconds')
    expect(formatWait(120)).toBe('2 minutes')
    expect(formatWait(7200)).toBe('2 hours')
  })
})

describe('setup failures', () => {
  it('asks for the setup token when the server does', () => {
    const f = describeSetupFailure(new ApiError(403, 'setup_token_required', 'x'))
    expect(f.needsToken).toBe(true)
    expect(f.fields.setup_token).toMatch(/Enter the setup token/)
  })
  it('says a wrong token was not accepted, also via details.reason', () => {
    const f = describeSetupFailure(new ApiError(403, 'forbidden', 'x', { reason: 'invalid_setup_token' }))
    expect(f.needsToken).toBe(true)
    expect(f.fields.setup_token).toMatch(/not accepted/)
  })
  it('maps validation errors onto fields', () => {
    const f = describeSetupFailure(new ApiError(422, 'validation_error', 'bad', [{ loc: ['body', 'username'], msg: 'taken' }]))
    expect(f.fields).toEqual({ username: 'taken' })
    expect(f.form).toBeNull()
  })
  it('detects that someone else finished setup first', () => {
    expect(describeSetupFailure(new ApiError(409, 'conflict', 'x')).alreadySetUp).toBe(true)
  })
})
