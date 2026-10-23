import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { CreateDepartmentRequest, Department, UpdateDepartmentRequest } from './types'

export function useDepartments(companyId: string | undefined) {
  return useQuery({
    queryKey: qk.departments(companyId ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Department>(`/companies/${companyId}/departments`, {}, signal),
    enabled: !!companyId,
  })
}

async function invalidateDepartmentRelated(qc: QueryClient, companyId: string) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: qk.departments(companyId) }),
    qc.invalidateQueries({ queryKey: qk.overview(companyId) }),
    qc.invalidateQueries({ queryKey: qk.activityAll(companyId) }),
  ])
}

export function useCreateDepartment(companyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateDepartmentRequest) =>
      apiFetch<Department>(`/companies/${companyId}/departments`, { method: 'POST', body }),
    onSuccess: () => invalidateDepartmentRelated(qc, companyId),
  })
}

export function useUpdateDepartment(companyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: UpdateDepartmentRequest & { id: string }) =>
      apiFetch<Department>(`/companies/${companyId}/departments/${id}`, { method: 'PATCH', body }),
    onSuccess: () => invalidateDepartmentRelated(qc, companyId),
  })
}
