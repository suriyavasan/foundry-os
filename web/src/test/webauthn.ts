import { vi } from 'vitest'

/** A buffer of recognisable bytes, for round-trip checks. */
export const buf = (...bytes: number[]): ArrayBuffer => new Uint8Array(bytes).buffer

/** Server-side WebAuthn options as the backend sends them: base64url strings where the browser wants buffers. */
export const creationJson = {
  rp: { id: 'localhost', name: 'Foundry AI OS' },
  user: { id: 'AQID', name: 'maya', displayName: 'Maya Chen' },
  challenge: 'BAUG',
  pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
  excludeCredentials: [{ type: 'public-key', id: 'CQoL' }],
  authenticatorSelection: { residentKey: 'required', userVerification: 'preferred' },
}
export const requestJson = { challenge: 'BwgJ', rpId: 'localhost', userVerification: 'preferred', allowCredentials: [] as unknown[] }

export function fakeAttestation() {
  return {
    id: 'cred-1',
    rawId: buf(1, 2, 3),
    type: 'public-key',
    authenticatorAttachment: 'platform',
    response: { clientDataJSON: buf(250, 251), attestationObject: buf(252, 253), getTransports: () => ['internal', 'hybrid'] },
    getClientExtensionResults: () => ({}),
  }
}

export function fakeAssertion() {
  return {
    id: 'cred-1',
    rawId: buf(1, 2, 3),
    type: 'public-key',
    response: { clientDataJSON: buf(250, 251), authenticatorData: buf(1, 1), signature: buf(9, 9), userHandle: buf(7) },
    getClientExtensionResults: () => ({}),
  }
}

export const domError = (name: string, message = name) => new DOMException(message, name)

export interface WebAuthnMock {
  create: ReturnType<typeof vi.fn>
  get: ReturnType<typeof vi.fn>
}

/** Install `PublicKeyCredential` and `navigator.credentials`. Pass `null` for a browser without WebAuthn. */
export function installWebAuthn(
  opts: { conditional?: boolean; create?: (o: CredentialCreationOptions) => unknown; get?: (o: CredentialRequestOptions) => unknown } | null = {},
): WebAuthnMock {
  const create = vi.fn(async (o: CredentialCreationOptions) => (opts?.create ? opts.create(o) : fakeAttestation()))
  const get = vi.fn(async (o: CredentialRequestOptions) => {
    if (opts?.get) return opts.get(o)
    // A conditional request waits for the person; it only ends when it is aborted.
    if (o.mediation === 'conditional') {
      return new Promise((_resolve, reject) => o.signal?.addEventListener('abort', () => reject(domError('AbortError'))))
    }
    return fakeAssertion()
  })
  if (opts === null) {
    vi.stubGlobal('PublicKeyCredential', undefined)
    Object.defineProperty(navigator, 'credentials', { value: undefined, configurable: true })
  } else {
    const conditional = opts.conditional === true
    class PKC {
      static isConditionalMediationAvailable = async () => conditional
    }
    vi.stubGlobal('PublicKeyCredential', PKC)
    Object.defineProperty(navigator, 'credentials', { value: { create, get }, configurable: true })
  }
  return { create, get }
}

export function uninstallWebAuthn() {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'credentials')
  Reflect.deleteProperty(window, 'isSecureContext')
}

/** Pretend the page is (not) a secure context, as it is on a plain http:// LAN address. */
export function setSecureContext(secure: boolean) {
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true })
}
