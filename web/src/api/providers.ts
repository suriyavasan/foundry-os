import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  CreateCredentialRequest,
  CreateModelProviderRequest,
  Credential,
  DetectResult,
  ModelProvider,
  ProviderTestResult,
  UpdateModelProviderRequest,
} from './types'
import { normalizeDetect, normalizeTestResult } from '@/lib/providers'

const co = (cid: string | undefined) => `/companies/${cid}`

/** M3b is built by another engineer in parallel: a 404 means "not built yet", never an error banner. */
const noRetryWhenMissing = (n: number, e: Error) => !(e instanceof ApiError && (e.status === 404 || e.status === 405)) && n < 2

export function useModelProviders(cid: string | undefined) {
  return useQuery({
    queryKey: qk.modelProviders(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<ModelProvider>(`${co(cid)}/model-providers`, {}, signal),
    enabled: !!cid,
    retry: noRetryWhenMissing,
  })
}

export function useCredentials(cid: string | undefined) {
  return useQuery({
    queryKey: qk.credentials(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Credential>(`${co(cid)}/credentials`, {}, signal),
    enabled: !!cid,
    retry: noRetryWhenMissing,
  })
}

/** Probing local Ollama is an explicit user action (a button), so this is a mutation, not a query. */
export function useDetectProviders(cid: string) {
  return useMutation({
    mutationFn: async (): Promise<DetectResult> => normalizeDetect(await apiFetch<unknown>(`${co(cid)}/model-providers/detect`)),
  })
}

export function useTestProvider(cid: string) {
  return useMutation({
    mutationFn: async (providerId: string): Promise<ProviderTestResult> =>
      normalizeTestResult(await apiFetch<unknown>(`${co(cid)}/model-providers/${providerId}/test`, { method: 'POST', body: {} })),
  })
}

export function useCreateCredential(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateCredentialRequest) => apiFetch<Credential>(`${co(cid)}/credentials`, { method: 'POST', body }),
    // The cached mutation variables would otherwise keep the secret in memory after success.
    gcTime: 0,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.credentials(cid) }),
  })
}

export function useCreateModelProvider(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateModelProviderRequest) => apiFetch<ModelProvider>(`${co(cid)}/model-providers`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.modelProviders(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useUpdateModelProvider(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateModelProviderRequest }) =>
      apiFetch<ModelProvider>(`${co(cid)}/model-providers/${id}`, { method: 'PATCH', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.modelProviders(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}
