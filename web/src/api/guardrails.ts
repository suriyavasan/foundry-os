import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { normalizeEmergencyStatus, normalizeRateLimits } from '@/lib/guardrails'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { EffectivePermissions, EmergencyStopStatus, GuardrailEvent, GuardrailEventFilters, GuardrailRule, GuardrailRuleCreate, GuardrailRuleUpdate, Page, RateLimitStatus } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/* ------------------------------------- rules ------------------------------------- */

export function useGuardrailRules(cid: string | undefined) {
  return useQuery({
    queryKey: qk.guardrailRules(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<GuardrailRule>(`${co(cid)}/guardrails/rules`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

function useRuleMutation<V, R>(cid: string, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.guardrailsAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ]),
  })
}

export const useCreateGuardrailRule = (cid: string) =>
  useRuleMutation(cid, (body: GuardrailRuleCreate) => apiFetch<GuardrailRule>(`${co(cid)}/guardrails/rules`, { method: 'POST', body }))
export const useUpdateGuardrailRule = (cid: string) =>
  useRuleMutation(cid, ({ id, ...body }: GuardrailRuleUpdate & { id: string }) => apiFetch<GuardrailRule>(`${co(cid)}/guardrails/rules/${id}`, { method: 'PATCH', body }))
export const useDeleteGuardrailRule = (cid: string) =>
  useRuleMutation(cid, (id: string) => apiFetch<void>(`${co(cid)}/guardrails/rules/${id}`, { method: 'DELETE' }))

/* ------------------------------------- events ------------------------------------ */

const EVENT_PAGE = 30

/** Newest first, filtered on the server. The stream (`guardrail.event`) invalidates this, so new rows appear live. */
export function useGuardrailEvents(cid: string | undefined, filters: GuardrailEventFilters = {}) {
  return useInfiniteQuery({
    queryKey: qk.guardrailEvents(cid ?? '', filters),
    enabled: !!cid,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      apiFetch<Page<GuardrailEvent>>(`${co(cid)}/guardrails/events`, { query: { ...filters, limit: EVENT_PAGE, cursor: pageParam }, signal }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry,
  })
}

/* ---------------------------------- rate limits ---------------------------------- */

/** Current usage of every window. Polled every 15 s as well as on `guardrail.event`. */
export function useRateLimits(cid: string | undefined) {
  return useQuery({
    queryKey: qk.rateLimits(cid ?? ''),
    queryFn: async ({ signal }): Promise<RateLimitStatus[]> => normalizeRateLimits(await apiFetch<unknown>(`${co(cid)}/guardrails/rate-limits`, { signal })),
    enabled: !!cid,
    refetchInterval: 15_000,
    retry,
  })
}

/* ----------------------------- effective permissions ----------------------------- */

export function useEffectivePermissions(cid: string | undefined, agentId: string | undefined) {
  return useQuery({
    queryKey: qk.effectivePermissions(cid ?? '', agentId ?? ''),
    queryFn: ({ signal }) => apiFetch<EffectivePermissions>(`${co(cid)}/agents/${agentId}/effective-permissions`, { signal }),
    enabled: !!cid && !!agentId,
    retry,
  })
}

/* ---------------------------------- emergency stop --------------------------------- */

/**
 * The global emergency-stop state. Not company-scoped: a stop halts every company. Polled so the red banner appears
 * and clears even when this tab did not trigger it. `data` is null when the endpoint answers with something that is
 * not a status (the banner then stays hidden rather than guessing).
 */
export function useSystemStatus() {
  return useQuery({
    queryKey: qk.systemStatus,
    queryFn: async ({ signal }): Promise<EmergencyStopStatus | null> => normalizeEmergencyStatus(await apiFetch<unknown>('/system/status', { signal })),
    refetchInterval: 10_000,
    retry,
  })
}

/** Start (`stop`) or end (`resume`) the emergency stop. Every company is affected. */
export function useEmergencyControl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { action: 'stop'; reason?: string } | { action: 'resume' }): Promise<EmergencyStopStatus | null> => {
      const raw =
        v.action === 'stop'
          ? await apiFetch<unknown>('/system/emergency-stop', { method: 'POST', body: v.reason?.trim() ? { reason: v.reason.trim() } : {} })
          : await apiFetch<unknown>('/system/resume', { method: 'POST', body: {} })
      return normalizeEmergencyStatus(raw)
    },
    onSuccess: async (status, v) => {
      // Trust the answer when it is a status; otherwise the refetch below decides.
      qc.setQueryData(qk.systemStatus, status ?? { active: v.action === 'stop', activated_at: null, activated_by: null, reason: null })
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.systemStatus }),
        qc.invalidateQueries({ queryKey: ['company'] }),
        qc.invalidateQueries({ queryKey: qk.companies }),
      ])
    },
  })
}
