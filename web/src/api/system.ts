import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { Health, Me, RuntimeList, UpdateStatus } from './types'

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: ({ signal }) => apiFetch<Me>('/me', { signal }) })
}

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: ({ signal }) => apiFetch<Health>('/health', { signal }) })
}

export function useRuntimes() {
  return useQuery({
    queryKey: qk.runtimes,
    queryFn: ({ signal }) => apiFetch<RuntimeList>('/runtimes', { signal }),
  })
}

/**
 * Is a newer release known? Installation owner only: anyone else gets a 403, an older backend a 404, and the banner
 * simply does not render (never an error banner). The server asks GitHub at most once a day, so this is polled lazily.
 */
export function useUpdateStatus() {
  return useQuery({
    queryKey: qk.systemUpdate,
    queryFn: ({ signal }) => apiFetch<UpdateStatus>('/system/update', { signal }),
    retry: false,
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  })
}

/** Switch the daily update check on or off (owner only; audited by the server). */
export function useSetUpdateCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) => apiFetch<UpdateStatus>('/system/update/settings', { method: 'PUT', body: { enabled } }),
    onSuccess: (data) => qc.setQueryData(qk.systemUpdate, data),
  })
}
