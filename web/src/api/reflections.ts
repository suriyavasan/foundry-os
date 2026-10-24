import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sortReflections } from '@/lib/reflections'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  CompanyStandard,
  CompanyStandardCreate,
  CompanyStandardUpdate,
  Reflection,
  ReflectionDetail,
  ReflectionResponse,
  ReflectionResponseCreate,
  ReflectionStatus,
} from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** Reflections, newest first. `status` filters on the server (`?status=`). */
export function useReflections(cid: string | undefined, status?: ReflectionStatus) {
  return useQuery({
    queryKey: qk.reflections(cid ?? '', status ?? 'all'),
    enabled: !!cid,
    retry,
    queryFn: async ({ signal }) => sortReflections(await fetchAllPages<Reflection>(`${co(cid)}/reflections`, { status }, signal)),
  })
}

export function useReflection(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.reflection(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => apiFetch<ReflectionDetail>(`${co(cid)}/reflections/${id}`, { signal }),
  })
}

function useInvalidateReflections(cid: string) {
  const qc = useQueryClient()
  return (id?: string) => {
    void qc.invalidateQueries({ queryKey: qk.reflectionsAll(cid) })
    void qc.invalidateQueries({ queryKey: qk.standardsAll(cid) })
    if (id) void qc.invalidateQueries({ queryKey: qk.reflection(cid, id) })
    void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
  }
}

/**
 * Endorse / challenge / refine / comment. Endorsing a standard proposal adopts it as a company standard (a human act the
 * server records), so the standards list is refreshed with the reflections.
 */
export function useRespondToReflection(cid: string) {
  const refresh = useInvalidateReflections(cid)
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ReflectionResponseCreate }) => apiFetch<ReflectionResponse>(`${co(cid)}/reflections/${id}/responses`, { method: 'POST', body }),
    onSuccess: (_r, v) => refresh(v.id),
  })
}

export function useArchiveReflection(cid: string) {
  const refresh = useInvalidateReflections(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<Reflection>(`${co(cid)}/reflections/${id}/archive`, { method: 'POST' }),
    onSuccess: (_r, id) => refresh(id),
  })
}

/* --------------------------------------- standards --------------------------------------- */

export function useStandards(cid: string | undefined) {
  return useQuery({
    queryKey: qk.standards(cid ?? ''),
    enabled: !!cid,
    retry,
    queryFn: ({ signal }) => fetchAllPages<CompanyStandard>(`${co(cid)}/standards`, {}, signal),
  })
}

export function useCreateStandard(cid: string) {
  const refresh = useInvalidateReflections(cid)
  return useMutation({
    mutationFn: (body: CompanyStandardCreate) => apiFetch<CompanyStandard>(`${co(cid)}/standards`, { method: 'POST', body }),
    onSuccess: () => refresh(),
  })
}

export function useUpdateStandard(cid: string) {
  const refresh = useInvalidateReflections(cid)
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CompanyStandardUpdate }) => apiFetch<CompanyStandard>(`${co(cid)}/standards/${id}`, { method: 'PATCH', body }),
    onSuccess: () => refresh(),
  })
}
