import { afterEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { buf, creationJson, domError, fakeAssertion, fakeAttestation, installWebAuthn, requestJson, setSecureContext, uninstallWebAuthn } from '@/test/webauthn'
import {
  INSECURE_ORIGIN_MESSAGE,
  base64urlToBuffer,
  bufferToBase64url,
  defaultPasskeyName,
  describePasskeyFailure,
  parseCreationOptions,
  parseRequestOptions,
  passkeyEnvironment,
  passkeyFailureText,
  serializeCredential,
  validatePasskeyName,
} from './webauthn'

afterEach(() => uninstallWebAuthn())

describe('base64url', () => {
  it('round-trips bytes, including ones that need - and _', () => {
    const bytes = buf(0, 250, 251, 252, 253, 254, 255, 62, 63)
    const text = bufferToBase64url(bytes)
    expect(text).not.toMatch(/[+/=]/)
    expect(new Uint8Array(base64urlToBuffer(text))).toEqual(new Uint8Array(bytes))
  })
  it('decodes unpadded input', () => {
    expect(Array.from(new Uint8Array(base64urlToBuffer('AQID')))).toEqual([1, 2, 3])
  })
})

describe('options from the server', () => {
  it('turns creation options into buffers', () => {
    const o = parseCreationOptions(creationJson)
    expect(Array.from(new Uint8Array(o.challenge as ArrayBuffer))).toEqual([4, 5, 6])
    expect(Array.from(new Uint8Array(o.user.id as ArrayBuffer))).toEqual([1, 2, 3])
    expect(Array.from(new Uint8Array(o.excludeCredentials![0]!.id as ArrayBuffer))).toEqual([9, 10, 11])
    expect(o.rp.id).toBe('localhost')
  })
  it('turns request options into buffers and leaves an empty allow list alone', () => {
    const o = parseRequestOptions(requestJson)
    expect(Array.from(new Uint8Array(o.challenge as ArrayBuffer))).toEqual([7, 8, 9])
    expect(o.allowCredentials).toEqual([])
  })
})

describe('credentials to the server', () => {
  it('serialises a created credential', () => {
    const j = serializeCredential(fakeAttestation() as unknown as PublicKeyCredential) as { id: string; rawId: string; authenticatorAttachment?: string; response: Record<string, unknown> }
    expect(j.id).toBe('cred-1')
    expect(j.rawId).toBe('AQID')
    expect(j.response.clientDataJSON).toBe(bufferToBase64url(buf(250, 251)))
    expect(j.response.attestationObject).toBe(bufferToBase64url(buf(252, 253)))
    expect(j.response.transports).toEqual(['internal', 'hybrid'])
    expect(j.authenticatorAttachment).toBe('platform')
  })
  it('serialises an assertion', () => {
    const j = serializeCredential(fakeAssertion() as unknown as PublicKeyCredential) as { id: string; rawId: string; authenticatorAttachment?: string; response: Record<string, unknown> }
    expect(j.response.signature).toBe(bufferToBase64url(buf(9, 9)))
    expect(j.response.userHandle).toBe(bufferToBase64url(buf(7)))
    expect(j.response.attestationObject).toBeUndefined()
  })
})

describe('environment', () => {
  it('is unavailable when the backend sent no passkey fields', () => {
    installWebAuthn()
    expect(passkeyEnvironment({})).toBe('backend_unavailable')
    expect(passkeyEnvironment(undefined)).toBe('backend_unavailable')
  })
  it('is insecure when the server says the origin is unsupported, or the page is not a secure context', () => {
    installWebAuthn()
    expect(passkeyEnvironment({ passkey_supported_origin: false })).toBe('insecure_origin')
    setSecureContext(false)
    expect(passkeyEnvironment({ passkey_supported_origin: true })).toBe('insecure_origin')
  })
  it('is unsupported without WebAuthn and ready with it', () => {
    installWebAuthn(null)
    expect(passkeyEnvironment({ passkey_supported_origin: true })).toBe('unsupported_browser')
    installWebAuthn()
    expect(passkeyEnvironment({ passkey_supported_origin: true })).toBe('ready')
  })
})

describe('failures', () => {
  it('reads a cancelled or timed-out prompt as cancelled', () => {
    const f = describePasskeyFailure(domError('NotAllowedError'))
    expect(f.kind).toBe('cancelled')
    expect(f.message).toMatch(/cancelled or timed out/)
  })
  it('keeps our own abort quiet', () => {
    expect(passkeyFailureText(describePasskeyFailure(domError('AbortError')), 0)).toBeNull()
  })
  it('explains a secure-context failure and a missing API', () => {
    expect(describePasskeyFailure(domError('SecurityError')).message).toBe(INSECURE_ORIGIN_MESSAGE)
    expect(describePasskeyFailure(domError('NotSupportedError')).kind).toBe('unsupported')
    expect(describePasskeyFailure(domError('InvalidStateError'), 'register').kind).toBe('already_registered')
  })
  it('maps API errors', () => {
    expect(describePasskeyFailure(new ApiError(429, 'rate_limited', 'x', { retry_after_seconds: 42 }))).toMatchObject({ kind: 'rate_limited', retryAfterSeconds: 42 })
    expect(describePasskeyFailure(new ApiError(404, 'not_found', 'x')).kind).toBe('missing')
    expect(describePasskeyFailure(new ApiError(403, 'reauth_required', 'x'), 'register').kind).toBe('reauth')
    expect(describePasskeyFailure(new ApiError(403, 'reauthentication_required', 'x'), 'register').kind).toBe('reauth') // the backend's code
    expect(describePasskeyFailure(new ApiError(409, 'conflict', 'x'), 'register').kind).toBe('last_method')
    // never reveals whether the username exists
    expect(describePasskeyFailure(new ApiError(401, 'unauthenticated', 'No user named maya')).message).not.toMatch(/maya/)
  })
})

describe('names', () => {
  it('suggests a name from the browser', () => {
    expect(defaultPasskeyName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36')).toBe('Mac · Chrome')
    expect(defaultPasskeyName('')).toBe('This device')
  })
  it('validates', () => {
    expect(validatePasskeyName('  ')).toBe('Give the passkey a name')
    expect(validatePasskeyName('x'.repeat(81))).toMatch(/at most 80/)
    expect(validatePasskeyName('Work laptop')).toBeNull()
  })
})
