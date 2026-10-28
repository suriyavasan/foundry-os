import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { normalizeAiConnection, normalizeAiLogin, normalizeAiStack, rankLabel, type AddAiBody, type AiConnection, type AiStackData, type PatchAiBody } from '@/lib/aiStack'
import { apiFetch } from './client'
import { qk } from './keys'
import type { ClaudeLoginStatus } from './types'
import { isNotDeployed } from './work'

export type { AddAiBody, AiConnection, AiOption, AiStackData, PatchAiBody } from '@/lib/aiStack'

const co = (cid: string | undefined) => `/companies/${cid}`
const noRetryWhenMissing = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** Anything that changes the stack also changes readiness (the gate) and the CLI list. */
function useInvalidateStack(cid: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: qk.aiStack(cid) })
    void qc.invalidateQueries({ queryKey: qk.aiSetup(cid) })
    void qc.invalidateQueries({ queryKey: qk.cliRuntimes })
  }
}

/**
 * The company's ordered AI connections (M17). A 404 means the backend predates M17 (callers show "not available").
 * While any connection's sign-in is running (known from the per-connection login queries) it is read every 2 s.
 */
export function useAiStack(cid: string | undefined) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: qk.aiStack(cid ?? ''),
    enabled: !!cid,
    retry: noRetryWhenMissing,
    refetchOnWindowFocus: true,
    refetchInterval: () => {
      if (!cid) return false
      const running = qc.getQueriesData<ClaudeLoginStatus>({ queryKey: qk.aiLoginAll(cid) }).some(([, d]) => d?.login.state === 'running')
      return running ? 2000 : false
    },
    queryFn: async ({ signal }): Promise<AiStackData> => {
      const data = normalizeAiStack(await apiFetch<unknown>(`${co(cid)}/ai-stack`, { signal }))
      if (!data) throw new Error('The server answered the AI request with something unexpected.')
      return data
    },
  })
}

export function useAddAiConnection(cid: string) {
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: async (body: AddAiBody): Promise<AiConnection | null> => normalizeAiConnection(await apiFetch<unknown>(`${co(cid)}/ai-stack`, { method: 'POST', body })),
    onSettled: invalidate,
  })
}

export function usePatchAiConnection(cid: string) {
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: PatchAiBody }): Promise<AiConnection | null> =>
      normalizeAiConnection(await apiFetch<unknown>(`${co(cid)}/ai-stack/${id}`, { method: 'PATCH', body })),
    onSettled: invalidate,
  })
}

/** Optimistic: the list reorders at once and rolls back if the server refuses. */
export function useReorderAiStack(cid: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: (ids: string[]) => apiFetch<unknown>(`${co(cid)}/ai-stack/order`, { method: 'PUT', body: { ids } }),
    onMutate: async (ids) => {
      await qc.cancelQueries({ queryKey: qk.aiStack(cid) })
      const prev = qc.getQueryData<AiStackData>(qk.aiStack(cid))
      if (prev) {
        const byId = new Map(prev.items.map((c) => [c.id, c]))
        const items = ids.flatMap((id, i) => {
          const c = byId.get(id)
          return c ? [{ ...c, position: i, rank_label: rankLabel(i) }] : []
        })
        qc.setQueryData<AiStackData>(qk.aiStack(cid), { ...prev, items })
      }
      return { prev }
    },
    onError: (_e, _ids, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.aiStack(cid), ctx.prev)
    },
    onSettled: invalidate,
  })
}

export function useRemoveAiConnection(cid: string) {
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<unknown>(`${co(cid)}/ai-stack/${id}`, { method: 'DELETE' }),
    onSettled: invalidate,
  })
}

export function useTestAiConnection(cid: string) {
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: async (id: string): Promise<AiConnection | null> => normalizeAiConnection(await apiFetch<unknown>(`${co(cid)}/ai-stack/${id}/test`, { method: 'POST', body: {} })),
    onSettled: invalidate,
  })
}

/* ------------------------------ plan sign-in (B9 rules) ------------------------------ */

/**
 * Sign-in state of one plan connection. Read every 2 s while a sign-in is running. Foundry never sees a password, code or
 * token: only the state and the sign-in link the CLI printed.
 */
export function useAiConnectionLogin(cid: string, id: string, enabled: boolean) {
  const qc = useQueryClient()
  return useQuery<ClaudeLoginStatus>({
    queryKey: qk.aiLogin(cid, id),
    enabled,
    retry: noRetryWhenMissing,
    refetchInterval: (query) => (query.state.data?.login.state === 'running' ? 2000 : false),
    queryFn: async ({ signal }): Promise<ClaudeLoginStatus> => {
      const s = normalizeAiLogin(await apiFetch<unknown>(`${co(cid)}/ai-stack/${id}/login`, { signal }))
      if (!s) throw new Error('The server answered the sign-in request with something unexpected.')
      // A finished sign-in changes the row's status.
      if (s.login.state !== 'running') {
        const prev = qc.getQueryData<ClaudeLoginStatus>(qk.aiLogin(cid, id))
        if (prev && (prev.login.state === 'running' || prev.signed_in !== s.signed_in)) void qc.invalidateQueries({ queryKey: qk.aiStack(cid) })
      }
      return s
    },
  })
}

export function useStartAiConnectionLogin(cid: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: async ({ id, method }: { id: string; method?: 'claudeai' | 'console' }): Promise<{ id: string; status: ClaudeLoginStatus | null }> => ({
      id,
      status: normalizeAiLogin(await apiFetch<unknown>(`${co(cid)}/ai-stack/${id}/login`, { method: 'POST', body: method ? { method } : {} })),
    }),
    onSuccess: ({ id, status }) => {
      if (status) qc.setQueryData(qk.aiLogin(cid, id), status)
    },
    onSettled: invalidate,
  })
}

export function useAiConnectionLogout(cid: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateStack(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<unknown>(`${co(cid)}/ai-stack/${id}/login`, { method: 'DELETE' }),
    onSuccess: (_r, id) => {
      void qc.invalidateQueries({ queryKey: qk.aiLogin(cid, id) })
    },
    onSettled: invalidate,
  })
}
