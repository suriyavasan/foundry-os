import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'
import { qk } from './keys'

/** 4xx are definitive (401 included: retrying cannot help); only transient failures are retried, twice. */
export function shouldRetry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return count < 2
}

/** The server refuses company work until the AI setup is ready (409 `ai_setup_required`, spec M10 §1). */
export const isAiSetupRequired = (error: unknown): boolean => error instanceof ApiError && error.status === 409 && error.code === 'ai_setup_required'

export const isUnauthenticated = (error: unknown): boolean => error instanceof ApiError && error.status === 401

/**
 * The app's QueryClient. A 401 from any request means the owner session is missing or expired:
 * re-check `/me` once so the login gate (features/auth/AuthGate) takes over, instead of every widget
 * showing its own error.
 */
export function createQueryClient(): QueryClient {
  const onUnauthenticated = (error: unknown, key?: readonly unknown[]) => {
    // Something the UI believed was ready is not: re-read the readiness so the AI gate takes over.
    if (isAiSetupRequired(error)) void client.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'company' && q.queryKey[2] === 'ai-setup' })
    if (!isUnauthenticated(error)) return
    // `/me` itself failing with 401 is already what the gate reads.
    if (key && (key[0] === qk.me[0] || (key[0] === qk.authSession[0] && key[1] === qk.authSession[1]))) return
    void client.invalidateQueries({ queryKey: qk.me })
    // M7: the cookie session may have expired or been revoked; the session state decides between app and login page.
    void client.invalidateQueries({ queryKey: qk.authSession })
  }
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error, query) => onUnauthenticated(error, query.queryKey) }),
    mutationCache: new MutationCache({ onError: (error) => onUnauthenticated(error) }),
    defaultOptions: {
      queries: { staleTime: 10_000, refetchOnWindowFocus: false, retry: shouldRetry },
    },
  })
  return client
}
