import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { Health, Me, RuntimeList } from './types'

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
