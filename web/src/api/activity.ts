import { useInfiniteQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { ActivityFilters, AuditEvent, Page } from './types'

export function useActivity(companyId: string | undefined, filters: ActivityFilters = {}, pageSize = 50) {
  return useInfiniteQuery({
    queryKey: qk.activity(companyId ?? '', filters),
    enabled: !!companyId,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      apiFetch<Page<AuditEvent>>(`/companies/${companyId}/activity`, {
        query: { limit: pageSize, cursor: pageParam, ...filters },
        signal,
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}
