import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { normalizeAiSetup, normalizeCli, normalizeInstallLog, normalizeModelList, normalizeProviderTest, type ModelOption } from '@/lib/aiSetup'
import { apiFetch } from './client'
import { qk } from './keys'
import type { AiSetupStatus, CliInstallLog, CliRuntimeStatus, ProviderTest } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const noRetryWhenMissing = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/**
 * The readiness status behind the AI gate (spec §1). A 404 (a backend without M10) is an error the caller reads as
 * "do not block": see `aiGateState` in lib/aiSetup. It is never retried.
 */
export function useAiSetup(cid: string | undefined) {
  return useQuery({
    queryKey: qk.aiSetup(cid ?? ''),
    enabled: !!cid,
    retry: noRetryWhenMissing,
    // Cheap, and the owner may fix it from another tab or the CLI: look again when the tab regains focus.
    refetchOnWindowFocus: true,
    queryFn: async ({ signal }): Promise<AiSetupStatus> => {
      const raw = await apiFetch<unknown>(`${co(cid)}/ai-setup`, { signal })
      const status = normalizeAiSetup(raw)
      if (!status) throw new Error('The server answered the AI setup request with something unexpected.')
      return status
    },
  })
}

/** Test a provider and read the five checks (reachable, auth, tool calling, context, model exists). */
export function useProviderTest(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (providerId: string): Promise<ProviderTest> => normalizeProviderTest(await apiFetch<unknown>(`${co(cid)}/model-providers/${providerId}/test`, { method: 'POST', body: {} })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.aiSetup(cid) })
    },
  })
}

/** The provider's model list for the picker. A missing endpoint or an empty list means "type the model name". */
export function useProviderModels(cid: string, providerId: string | null | undefined) {
  return useQuery({
    queryKey: qk.providerModels(cid, providerId ?? ''),
    enabled: !!providerId,
    retry: noRetryWhenMissing,
    staleTime: 60_000,
    queryFn: async ({ signal }): Promise<ModelOption[]> => normalizeModelList(await apiFetch<unknown>(`${co(cid)}/model-providers/${providerId}/models`, { signal })),
  })
}

/* ------------------------------------ CLI runtimes ------------------------------------ */

/** Installation-level status of the coding CLIs (not company scoped). */
export function useCliRuntimes(enabled = true) {
  return useQuery({
    queryKey: qk.cliRuntimes,
    enabled,
    retry: noRetryWhenMissing,
    queryFn: async ({ signal }): Promise<CliRuntimeStatus[]> => {
      const raw = await apiFetch<unknown>('/cli-runtimes', { signal })
      const list = Array.isArray(raw) ? raw : Array.isArray((raw as { items?: unknown } | null)?.items) ? (raw as { items: unknown[] }).items : []
      return list.map(normalizeCli).filter((c): c is CliRuntimeStatus => c !== null)
    },
  })
}

const idPath = (id: string) => `/cli-runtimes/${encodeURIComponent(id)}`

function useCliInvalidate(cid?: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: qk.cliRuntimes })
    if (cid) {
      void qc.invalidateQueries({ queryKey: qk.aiSetup(cid) })
      void qc.invalidateQueries({ queryKey: qk.aiStack(cid) })
    }
  }
}

/** Runs the exact install command the person confirmed. `confirm: true` is sent only from the confirm dialog. */
export function useInstallCli(cid?: string) {
  const invalidate = useCliInvalidate(cid)
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<unknown>(`${idPath(id)}/install`, {
        method: 'POST',
        body: { confirm: true },
      }),
    onSettled: invalidate,
  })
}

/** Tails the (redacted) install log while the install runs. */
export function useCliInstallLog(id: string | null, active: boolean) {
  return useQuery<CliInstallLog>({
    queryKey: qk.cliInstallLog(id ?? ''),
    enabled: !!id,
    retry: false,
    refetchInterval: (q) => (active && q.state.data?.state !== 'succeeded' && q.state.data?.state !== 'failed' ? 1000 : false),
    queryFn: async ({ signal }): Promise<CliInstallLog> => normalizeInstallLog(await apiFetch<unknown>(`${idPath(id!)}/install-log`, { signal })),
  })
}
