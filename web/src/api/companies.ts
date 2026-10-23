import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { Company, CreateCompanyRequest, Overview, UpdateCompanyRequest } from './types'

export function useCompanies() {
  return useQuery({
    queryKey: qk.companies,
    queryFn: ({ signal }) => fetchAllPages<Company>('/companies', {}, signal),
  })
}

export function useCompany(companyId: string | undefined) {
  return useQuery({
    queryKey: qk.companyDetail(companyId ?? ''),
    queryFn: ({ signal }) => apiFetch<Company>(`/companies/${companyId}`, { signal }),
    enabled: !!companyId,
  })
}

export function useOverview(companyId: string | undefined) {
  return useQuery({
    queryKey: qk.overview(companyId ?? ''),
    queryFn: ({ signal }) => apiFetch<Overview>(`/companies/${companyId}/overview`, { signal }),
    enabled: !!companyId,
  })
}

export function useCreateCompany() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateCompanyRequest) => apiFetch<Company>('/companies', { method: 'POST', body }),
    onSuccess: async (company) => {
      qc.setQueryData(qk.companyDetail(company.id), company)
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.companies }),
        qc.invalidateQueries({ queryKey: qk.me }),
      ])
    },
  })
}

export function useUpdateCompany(companyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateCompanyRequest) =>
      apiFetch<Company>(`/companies/${companyId}`, { method: 'PATCH', body }),
    onSuccess: async (company) => {
      qc.setQueryData(qk.companyDetail(companyId), company)
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.companies }),
        qc.invalidateQueries({ queryKey: qk.overview(companyId) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(companyId) }),
        qc.invalidateQueries({ queryKey: qk.me }),
      ])
    },
  })
}

/** Where the one-line idea of a new company ended up. */
export type IdeaDestination = 'note' | 'strategic_goal' | 'skipped' | 'failed'

/**
 * The first-run wizard asks for a one-line idea. It becomes the first Founder's note, which the AI Co-founder
 * triages into goals and tasks (M5). On a backend without notes the idea is kept as the company's strategic goal
 * rather than dropped; any other failure is reported so the caller can say so.
 */
export async function seedCompanyIdea(companyId: string, idea: string): Promise<IdeaDestination> {
  const text = idea.trim()
  if (!text) return 'skipped'
  try {
    await apiFetch(`/companies/${companyId}/notes`, { method: 'POST', body: { body: text } })
    return 'note'
  } catch (e) {
    if (!(e instanceof ApiError && (e.status === 404 || e.status === 405))) return 'failed'
  }
  try {
    await apiFetch(`/companies/${companyId}`, { method: 'PATCH', body: { strategic_goal: text } })
    return 'strategic_goal'
  } catch {
    return 'failed'
  }
}
