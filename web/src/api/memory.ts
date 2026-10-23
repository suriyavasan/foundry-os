import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { CreateMemoryRequest, MemoryEntry } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`

/** All memory entries (bounded); kind / status / search filters are applied client-side (lib/memory). */
export function useMemory(cid: string | undefined) {
  return useQuery({
    queryKey: qk.memory(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<MemoryEntry>(`${co(cid)}/memory`, {}, signal),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

function invalidateMemory(qc: ReturnType<typeof useQueryClient>, cid: string) {
  return Promise.all([qc.invalidateQueries({ queryKey: qk.memoryAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })])
}

/** A human-created entry is verified by that human immediately. */
export function useCreateMemory(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateMemoryRequest) => apiFetch<MemoryEntry>(`${co(cid)}/memory`, { method: 'POST', body }),
    onSuccess: () => invalidateMemory(qc, cid),
  })
}

export function useMemoryDecision(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action, reason }: { id: string; action: 'verify' | 'reject'; reason?: string }) =>
      apiFetch<MemoryEntry>(`${co(cid)}/memory/${id}/${action}`, { method: 'POST', body: reason ? { reason } : {} }),
    onSuccess: () => invalidateMemory(qc, cid),
  })
}
