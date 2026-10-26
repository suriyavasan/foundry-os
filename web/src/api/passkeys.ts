import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isMissingEndpoint, normalizeAuthSession } from '@/lib/auth'
import { createCredential, getCredential } from '@/lib/webauthn'
import { adoptSession } from './auth'
import { apiFetch } from './client'
import { qk } from './keys'
import type {
  AuthSession,
  Page,
  PasskeyDelete,
  PasskeyInfo,
  PasskeyLoginOptions,
  PasskeyLoginOptionsRequest,
  PasskeyLoginVerify,
  PasskeyRegistrationOptions,
  PasskeyRegistrationOptionsRequest,
  PasskeyRegistrationVerify,
  PasskeyRename,
} from './types'

/** The signed-in account's passkeys. A 404 means the backend has none: callers hide the passkey UI. */
export function usePasskeys(enabled = true) {
  return useQuery({
    queryKey: qk.authPasskeys,
    enabled,
    queryFn: async ({ signal }) => (await apiFetch<Page<PasskeyInfo>>('/auth/passkeys', { signal })).items ?? [],
    retry: (n, e) => !isMissingEndpoint(e) && n < 2,
  })
}

/**
 * One registration ceremony: ask the server for options, let the browser create the credential, send it back to be
 * verified. Rejects with an `ApiError` (server) or a `DOMException` (the browser: cancel, timeout, already registered).
 */
export function useRegisterPasskey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ name, signal }: { name?: string; signal?: AbortSignal }): Promise<PasskeyInfo> => {
      const opts = await apiFetch<PasskeyRegistrationOptions>('/auth/passkeys/register/options', {
        method: 'POST',
        body: { ...(name ? { name } : null) } satisfies PasskeyRegistrationOptionsRequest,
        signal,
      })
      const credential = await createCredential(opts.public_key, signal)
      return apiFetch<PasskeyInfo>('/auth/passkeys/register/verify', {
        method: 'POST',
        body: { attempt_id: opts.attempt_id, ...(name ? { name } : null), credential } satisfies PasskeyRegistrationVerify,
        signal,
      })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.authPasskeys })
      void qc.invalidateQueries({ queryKey: qk.authSession })
    },
  })
}

export function useRenamePasskey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string } & PasskeyRename) => apiFetch<PasskeyInfo>(`/auth/passkeys/${id}`, { method: 'PATCH', body: { name } satisfies PasskeyRename }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authPasskeys }),
  })
}

/** The password, when asked for (`reauth_required`), travels in the body of the retry only and is not kept. */
export function useDeletePasskey() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: ({ id, password }: { id: string } & PasskeyDelete) =>
      apiFetch<void>(`/auth/passkeys/${id}`, { method: 'DELETE', ...(password ? { body: { password } satisfies PasskeyDelete } : null) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.authPasskeys })
      void qc.invalidateQueries({ queryKey: qk.authSession })
    },
  })
}

/**
 * Sign in with a passkey. `conditional` is the autofill flavour (the browser offers passkeys inside the username field
 * and the promise stays pending until the person picks one); the modal flavour opens the browser's own passkey sheet.
 * Success sets the same cookies as a password login and adopts the session.
 */
export function usePasskeyLogin() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: async ({ username, conditional, signal }: { username?: string; conditional?: boolean; signal?: AbortSignal }): Promise<AuthSession> => {
      const opts = await apiFetch<PasskeyLoginOptions>('/auth/passkey/login/options', {
        method: 'POST',
        body: { ...(username ? { username } : null) } satisfies PasskeyLoginOptionsRequest,
        signal,
      })
      const credential = await getCredential(opts.public_key, { conditional, signal })
      const raw = await apiFetch<unknown>('/auth/passkey/login/verify', {
        method: 'POST',
        body: { attempt_id: opts.attempt_id, credential } satisfies PasskeyLoginVerify,
        signal,
      })
      return normalizeAuthSession(raw)
    },
    onSuccess: (session) => {
      if (session.state === 'authenticated') adoptSession(qc, session)
    },
  })
}
