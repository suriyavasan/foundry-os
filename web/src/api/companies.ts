import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
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
