import { ApiError } from '@/api/client'
import type { AuthSession } from '@/api/types'
import { formatWait, reasonOf, retryAfter, type LoginFailure } from '@/lib/auth'

/**
 * WebAuthn in the browser, without a library: base64url <-> ArrayBuffer, the JSON forms the server speaks, support
 * detection and plain-language errors. The server sends and receives the "JSON" shapes of the WebAuthn spec
 * (base64url strings where the browser API wants buffers). Nothing here talks to the network.
 */

/* ---------------------------------- base64url ---------------------------------- */

export function bufferToBase64url(buf: ArrayBuffer | ArrayBufferView): string {
  const bytes = ArrayBuffer.isView(buf) ? new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength) : new Uint8Array(buf)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function base64urlToBuffer(value: string): ArrayBuffer {
  const b64 = value.replace(/-/g, '+').replace(/_/g, '/')
  const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4))
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}

/* ------------------------------- options (server -> browser) ------------------------------- */

type Json = Record<string, unknown>
const isObj = (v: unknown): v is Json => !!v && typeof v === 'object' && !Array.isArray(v)

function descriptors(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined
  return list.filter(isObj).map((d) => ({ ...(d as unknown as PublicKeyCredentialDescriptor), id: base64urlToBuffer(String(d.id)) }))
}

/** `publicKey` for `navigator.credentials.create`. */
export function parseCreationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const user = isObj(json.user) ? json.user : {}
  const out = { ...json, challenge: base64urlToBuffer(String(json.challenge)), user: { ...user, id: base64urlToBuffer(String(user.id)) } } as unknown as PublicKeyCredentialCreationOptions
  const exclude = descriptors(json.excludeCredentials)
  if (exclude) out.excludeCredentials = exclude
  return out
}

/** `publicKey` for `navigator.credentials.get`. */
export function parseRequestOptions(json: Json): PublicKeyCredentialRequestOptions {
  const out = { ...json, challenge: base64urlToBuffer(String(json.challenge)) } as unknown as PublicKeyCredentialRequestOptions
  const allow = descriptors(json.allowCredentials)
  if (allow) out.allowCredentials = allow
  return out
}

/* ------------------------------ credentials (browser -> server) ------------------------------ */

/** The JSON form of a created or asserted credential, as the server verifies it. */
export function serializeCredential(cred: PublicKeyCredential): Json {
  const r = cred.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse
  const response: Json = { clientDataJSON: bufferToBase64url(r.clientDataJSON) }
  if (r.attestationObject) {
    response.attestationObject = bufferToBase64url(r.attestationObject)
    if (typeof r.getTransports === 'function') response.transports = r.getTransports()
    if (typeof r.getPublicKeyAlgorithm === 'function') response.publicKeyAlgorithm = r.getPublicKeyAlgorithm()
  }
  if (r.authenticatorData) response.authenticatorData = bufferToBase64url(r.authenticatorData)
  if (r.signature) response.signature = bufferToBase64url(r.signature)
  if (r.userHandle) response.userHandle = bufferToBase64url(r.userHandle)
  return {
    id: cred.id,
    rawId: bufferToBase64url(cred.rawId),
    type: cred.type,
    response,
    clientExtensionResults: typeof cred.getClientExtensionResults === 'function' ? cred.getClientExtensionResults() : {},
    ...(cred.authenticatorAttachment ? { authenticatorAttachment: cred.authenticatorAttachment } : null),
  }
}

/* ------------------------------------ the ceremony ------------------------------------ */

function credentialsApi(): CredentialsContainer {
  const c = typeof navigator === 'undefined' ? undefined : navigator.credentials
  if (!c) throw new DOMException('Passkeys are not available in this browser.', 'NotSupportedError')
  return c
}

export async function createCredential(options: Json, signal?: AbortSignal): Promise<Json> {
  const cred = await credentialsApi().create({ publicKey: parseCreationOptions(options), signal })
  if (!cred) throw new DOMException('No passkey was created.', 'NotAllowedError')
  return serializeCredential(cred as PublicKeyCredential)
}

export async function getCredential(options: Json, opts: { conditional?: boolean; signal?: AbortSignal } = {}): Promise<Json> {
  const cred = await credentialsApi().get({
    publicKey: parseRequestOptions(options),
    ...(opts.conditional ? { mediation: 'conditional' as CredentialMediationRequirement } : null),
    signal: opts.signal,
  })
  if (!cred) throw new DOMException('No passkey was chosen.', 'NotAllowedError')
  return serializeCredential(cred as PublicKeyCredential)
}

/* ------------------------------------ support ------------------------------------ */

export const INSECURE_ORIGIN_MESSAGE =
  'Passkeys work on this computer (localhost) or over HTTPS — not on a plain http:// network address. Use your password here, or open Foundry AI OS through its secure public address.'
export const UNSUPPORTED_BROWSER_MESSAGE = 'This browser does not support passkeys. Use your password, or try a current version of Safari, Chrome, Edge or Firefox.'

export type PasskeyEnvironment =
  /** The backend has no passkey endpoints (the session carries no passkey fields): show nothing. */
  | 'backend_unavailable'
  | 'insecure_origin'
  | 'unsupported_browser'
  | 'ready'

export function browserSupportsPasskeys(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential !== 'undefined' && typeof navigator !== 'undefined' && !!navigator.credentials
}

/** Whether passkeys can be used here: the backend's word on the origin first, then the browser's. */
export function passkeyEnvironment(session: Pick<AuthSession, 'passkey_supported_origin'> | null | undefined): PasskeyEnvironment {
  if (typeof session?.passkey_supported_origin !== 'boolean') return 'backend_unavailable'
  if (session.passkey_supported_origin === false || (typeof window !== 'undefined' && window.isSecureContext === false)) return 'insecure_origin'
  return browserSupportsPasskeys() ? 'ready' : 'unsupported_browser'
}

/** Autofill ("conditional UI") is offered only where the browser says it can. */
export async function conditionalMediationAvailable(): Promise<boolean> {
  try {
    const pkc = typeof window === 'undefined' ? undefined : window.PublicKeyCredential
    return !!pkc && typeof pkc.isConditionalMediationAvailable === 'function' && (await pkc.isConditionalMediationAvailable()) === true
  } catch {
    return false
  }
}

/** The message that replaces a passkey button that cannot work here, or null when it can. */
export function passkeyEnvironmentMessage(env: PasskeyEnvironment): string | null {
  if (env === 'insecure_origin') return INSECURE_ORIGIN_MESSAGE
  if (env === 'unsupported_browser') return UNSUPPORTED_BROWSER_MESSAGE
  return null
}

/* ------------------------------------ errors ------------------------------------ */

export type PasskeyFailureKind = 'cancelled' | 'aborted' | 'already_registered' | 'insecure_origin' | 'unsupported' | 'missing' | 'reauth' | 'last_method'

export interface PasskeyFailure extends Omit<LoginFailure, 'kind'> {
  kind: LoginFailure['kind'] | PasskeyFailureKind
}

const domName = (e: unknown): string | null => (e instanceof DOMException || (e instanceof Error && 'name' in e) ? (e as Error).name : null)

/**
 * Plain-language outcome of a failed passkey ceremony or request. `aborted` is our own cancellation (a newer attempt
 * replaced it) and is never shown. Credential problems never say whether a username exists.
 */
export function describePasskeyFailure(err: unknown, action: 'login' | 'register' = 'login'): PasskeyFailure {
  const verb = action === 'login' ? 'Passkey sign-in' : 'Adding the passkey'
  if (err instanceof ApiError) {
    if (err.code === 'network_error') return { kind: 'network', message: err.message, retryAfterSeconds: null }
    if (err.status === 404) return { kind: 'missing', message: 'Passkeys are not available on this server.', retryAfterSeconds: null }
    if (err.status === 429 || err.code === 'rate_limited' || err.code === 'too_many_attempts') {
      return { kind: 'rate_limited', message: action === 'login' ? 'Too many sign-in attempts.' : 'Too many attempts.', retryAfterSeconds: retryAfter(err) }
    }
    if (err.status === 423) return { kind: 'locked', message: 'Sign-in is paused after repeated failed attempts.', retryAfterSeconds: retryAfter(err) }
    // The backend uses `reauthentication_required`; `reauth_required` is accepted too.
    const reauth = (c: string | null | undefined) => c === 'reauthentication_required' || c === 'reauth_required'
    if (err.status === 403 && (reauth(err.code) || reauth(reasonOf(err)))) return { kind: 'reauth', message: 'Confirm your password to continue.', retryAfterSeconds: null }
    if (err.status === 409) return { kind: 'last_method', message: err.message, retryAfterSeconds: null }
    if (err.status >= 500) return { kind: 'other', message: 'The server had a problem. Try again in a moment.', retryAfterSeconds: null }
    if (action === 'login') return { kind: 'invalid', message: 'That passkey was not accepted. Try again, or sign in with your password.', retryAfterSeconds: null }
    return { kind: 'other', message: err.message || 'The passkey was not accepted. Try again.', retryAfterSeconds: null }
  }
  switch (domName(err)) {
    case 'AbortError':
      return { kind: 'aborted', message: '', retryAfterSeconds: null }
    case 'NotAllowedError':
      return { kind: 'cancelled', message: `${verb} was cancelled or timed out. Try again when you are ready.`, retryAfterSeconds: null }
    case 'InvalidStateError':
      return { kind: 'already_registered', message: 'This device already has a passkey for your account.', retryAfterSeconds: null }
    case 'SecurityError':
      return { kind: 'insecure_origin', message: INSECURE_ORIGIN_MESSAGE, retryAfterSeconds: null }
    case 'NotSupportedError':
      return { kind: 'unsupported', message: UNSUPPORTED_BROWSER_MESSAGE, retryAfterSeconds: null }
    default:
      return { kind: 'other', message: `${verb} did not work. Try again, or use your password.`, retryAfterSeconds: null }
  }
}

/** What to show under the button: a quantified wait counts down, anything else is the message. */
export function passkeyFailureText(f: PasskeyFailure, remaining: number): string | null {
  if (f.kind === 'aborted') return null
  if (f.kind === 'rate_limited' || f.kind === 'locked') {
    if (f.retryAfterSeconds === null) return `${f.message} Try again in a little while.`
    return remaining > 0 ? `${f.message} Try again in ${formatWait(remaining)}.` : null
  }
  return f.message
}

/** A suggested name for a new passkey, from what the browser reveals about this device. */
export function defaultPasskeyName(userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent): string {
  const browser = /Edg\//.test(userAgent) ? 'Edge' : /Firefox\//.test(userAgent) ? 'Firefox' : /Chrome\//.test(userAgent) ? 'Chrome' : /Safari\//.test(userAgent) ? 'Safari' : null
  const os = /iPhone|iPad/.test(userAgent) ? 'iPhone' : /Android/.test(userAgent) ? 'Android' : /Mac OS X|Macintosh/.test(userAgent) ? 'Mac' : /Windows/.test(userAgent) ? 'Windows PC' : /Linux/.test(userAgent) ? 'Linux' : null
  if (os && browser) return `${os} · ${browser}`
  return os ?? browser ?? 'This device'
}

export const PASSKEY_NAME_MAX = 80

export function validatePasskeyName(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Give the passkey a name'
  if (v.length > PASSKEY_NAME_MAX) return `Use at most ${PASSKEY_NAME_MAX} characters`
  return null
}
