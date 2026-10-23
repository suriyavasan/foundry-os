import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'
import { qk } from './keys'

/** 4xx are definitive (401 included: retrying cannot help); only transient failures are retried, twice. */
export function shouldRetry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return count < 2
}

export const isUnauthenticated = (error: unknown): boolean => error instanceof ApiError && error.status === 401

/**
 * The app's QueryClient. A 401 from any request means the owner session is missing or expired:
 * re-check `/me` once so the login gate (features/auth/AuthGate) takes over, instead of every widget
 * showing its own error.
 */
export function createQueryClient(): QueryClient {
  const onUnauthenticated = (error: unknown, key?: readonly unknown[]) => {
    if (!isUnauthenticated(error)) return
    // `/me` itself failing with 401 is already what the gate reads.
    if (key && key[0] === qk.me[0]) return
    void client.invalidateQueries({ queryKey: qk.me })
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
