import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { authStatusOf, isMissingEndpoint, normalizeAuthSession, type AuthStatus } from '@/lib/auth'
import { ApiError, apiFetch } from './client'
import { qk } from './keys'
import type {
  ApiToken,
  ApiTokenCreate,
  ApiTokenCreated,
  AuthSession,
  LoginRequest,
  PasswordChange,
  SessionInfo,
  SetupRequest,
  TwoFactorEnroll,
  TwoFactorVerifyResult,
} from './types'

/* -------------------------------- the session -------------------------------- */

/**
 * `GET /auth/session` is public and answers with the state (`setup_required | unauthenticated | authenticated`).
 * A 401 is read as unauthenticated; a 404/405 means the backend predates M7 and surfaces as the `legacy` status.
 */
export function useAuthSession() {
  return useQuery({
    queryKey: qk.authSession,
    queryFn: async ({ signal }): Promise<AuthSession> => {
      try {
        return normalizeAuthSession(await apiFetch<unknown>('/auth/session', { signal }))
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return { state: 'unauthenticated', user: null }
        throw e
      }
    },
    retry: (n, e) => !isMissingEndpoint(e) && !(e instanceof ApiError && e.status < 500 && e.status > 0) && n < 2,
    // Cheap, and the cookie can be revoked from another tab: look again when the tab regains focus.
    refetchOnWindowFocus: true,
  })
}

export function useAuthStatus(): AuthStatus {
  const q = useAuthSession()
  return authStatusOf({ isPending: q.isPending, data: q.data, error: q.error })
}

/** The signed-in account, or null (legacy backend, signed out, loading). */
export function useAuthUser() {
  const s = useAuthStatus()
  return s.kind === 'authenticated' ? s.session.user : null
}

/** After the identity changes, nothing cached under the old identity may be shown. */
function resetForNewIdentity(qc: QueryClient) {
  void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'auth' })
}

function adoptSession(qc: QueryClient, session: AuthSession) {
  qc.setQueryData(qk.authSession, session)
  if (session.state === 'authenticated') resetForNewIdentity(qc)
}

export function useSetup() {
  const qc = useQueryClient()
  return useMutation({
    // The password is in the variables: never keep them after the call.
    gcTime: 0,
    mutationFn: async (body: SetupRequest) => normalizeAuthSession(await apiFetch<unknown>('/auth/setup', { method: 'POST', body })),
    onSuccess: (session) => adoptSession(qc, session),
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: async (body: LoginRequest) => normalizeAuthSession(await apiFetch<unknown>('/auth/login', { method: 'POST', body })),
    onSuccess: (session) => {
      // A "password ok, code needed" answer is not a new identity: leave the cache alone.
      if (session.state === 'authenticated') adoptSession(qc, session)
    },
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<void>('/auth/logout', { method: 'POST', body: {} }),
    onSettled: () => signedOut(qc),
  })
}

/** Show the login page and drop everything the previous account could see. */
export function signedOut(qc: QueryClient) {
  qc.setQueryData(qk.authSession, { state: 'unauthenticated', user: null } satisfies AuthSession)
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' })
}

/* ----------------------------------- password ----------------------------------- */

export function useChangePassword() {
  return useMutation({
    gcTime: 0,
    mutationFn: (body: PasswordChange) => apiFetch<void>('/auth/password', { method: 'POST', body }),
  })
}

/* ----------------------------------- sessions ----------------------------------- */

const asList = <T,>(raw: unknown): T[] => (Array.isArray(raw) ? (raw as T[]) : raw && typeof raw === 'object' && Array.isArray((raw as { items?: unknown }).items) ? ((raw as { items: T[] }).items) : [])

export function useAuthSessions(enabled = true) {
  return useQuery({
    queryKey: qk.authSessions,
    enabled,
    queryFn: async ({ signal }) => asList<SessionInfo>(await apiFetch<unknown>('/auth/sessions', { signal })),
    retry: (n, e) => !isMissingEndpoint(e) && n < 2,
  })
}

export function useRevokeSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/sessions/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authSessions }),
  })
}

/** "Log out everywhere": every session, this one included, so the page ends on the login screen. */
export function useRevokeAllSessions() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<void>('/auth/sessions', { method: 'DELETE' }),
    onSuccess: () => signedOut(qc),
  })
}

/* ------------------------------------ tokens ------------------------------------ */

export function useApiTokens(enabled = true) {
  return useQuery({
    queryKey: qk.authTokens,
    enabled,
    queryFn: async ({ signal }) => asList<ApiToken>(await apiFetch<unknown>('/auth/tokens', { signal })),
    retry: (n, e) => !isMissingEndpoint(e) && n < 2,
  })
}

/** The response carries the secret exactly once: it must not linger in the mutation cache. */
export function useCreateApiToken() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (body: ApiTokenCreate) => apiFetch<ApiTokenCreated>('/auth/tokens', { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authTokens }),
  })
}

export function useRevokeApiToken() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/tokens/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authTokens }),
  })
}

/* ------------------------------------- 2FA -------------------------------------- */

/** The enrolment secret is a credential: keep it out of the cache. */
export function useEnrollTwoFactor() {
  return useMutation({
    gcTime: 0,
    mutationFn: () => apiFetch<TwoFactorEnroll>('/auth/2fa/enroll', { method: 'POST', body: {} }),
  })
}

export function useVerifyTwoFactor() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (code: string) => apiFetch<TwoFactorVerifyResult>('/auth/2fa/verify', { method: 'POST', body: { code } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authSession }),
  })
}

export function useDisableTwoFactor() {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (body: { code: string; password?: string }) => apiFetch<void>('/auth/2fa/disable', { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.authSession }),
  })
}
