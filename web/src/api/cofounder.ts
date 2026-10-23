import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { CofounderSummary, CofounderWakeResult } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`

/** What the AI Co-founder is doing and why: goals, plan, decisions, blockers, alerts, last run. */
export function useCofounderSummary(cid: string | undefined) {
  return useQuery({
    queryKey: qk.cofounderSummary(cid ?? ''),
    queryFn: ({ signal }) => apiFetch<CofounderSummary>(`${co(cid)}/cofounder/summary`, { signal }),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

/** Ask the AI Co-founder to run now. The server coalesces: at most one planning task is open. */
export function useWakeCofounder(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<CofounderWakeResult>(`${co(cid)}/cofounder/wake`, { method: 'POST' }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.cofounder(cid) })
      void qc.invalidateQueries({ queryKey: qk.tasksAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
    },
  })
}
