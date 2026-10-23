import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  Agent,
  AgentFilters,
  AgentLifecycleAction,
  CreateAgentRequest,
  UpdateAgentRequest,
} from './types'

export function useAgents(companyId: string | undefined, filters: AgentFilters = {}) {
  return useQuery({
    queryKey: qk.agentList(companyId ?? '', filters),
    queryFn: ({ signal }) =>
      fetchAllPages<Agent>(`/companies/${companyId}/agents`, { ...filters }, signal),
    enabled: !!companyId,
  })
}

export function useAgent(companyId: string | undefined, agentId: string | undefined) {
  return useQuery({
    queryKey: qk.agent(companyId ?? '', agentId ?? ''),
    queryFn: ({ signal }) => apiFetch<Agent>(`/companies/${companyId}/agents/${agentId}`, { signal }),
    enabled: !!companyId && !!agentId,
  })
}

/** Agent changes affect lists, the overview counters, department counts and the audit feed. */
async function invalidateAgentRelated(qc: QueryClient, companyId: string) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: qk.agents(companyId) }),
    qc.invalidateQueries({ queryKey: qk.overview(companyId) }),
    qc.invalidateQueries({ queryKey: qk.departments(companyId) }),
    qc.invalidateQueries({ queryKey: qk.activityAll(companyId) }),
  ])
}

export function useCreateAgent(companyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateAgentRequest) =>
      apiFetch<Agent>(`/companies/${companyId}/agents`, { method: 'POST', body }),
    onSuccess: () => invalidateAgentRelated(qc, companyId),
  })
}

export function useUpdateAgent(companyId: string, agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateAgentRequest) =>
      apiFetch<Agent>(`/companies/${companyId}/agents/${agentId}`, { method: 'PATCH', body }),
    onSuccess: (agent) => {
      qc.setQueryData(qk.agent(companyId, agentId), agent)
      return invalidateAgentRelated(qc, companyId)
    },
  })
}

export function useAgentLifecycle(companyId: string, agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ action, reason }: { action: AgentLifecycleAction; reason?: string }) =>
      apiFetch<Agent>(`/companies/${companyId}/agents/${agentId}/${action}`, {
        method: 'POST',
        body: reason ? { reason } : {},
      }),
    onSuccess: (agent) => {
      qc.setQueryData(qk.agent(companyId, agentId), agent)
      return invalidateAgentRelated(qc, companyId)
    },
  })
}
