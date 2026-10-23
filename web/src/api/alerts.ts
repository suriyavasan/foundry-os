import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { Alert } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`

/** Open alerts only (`status=open`). The strip hides itself on a 404. */
export function useOpenAlerts(cid: string | undefined) {
  return useQuery({
    queryKey: qk.alerts(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Alert>(`${co(cid)}/alerts`, { status: 'open' }, signal, 5),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}

export function useResolveAlert(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<Alert>(`${co(cid)}/alerts/${id}/resolve`, { method: 'POST', body: {} }),
    onSuccess: (_a, id) => {
      qc.setQueryData<Alert[]>(qk.alerts(cid), (old) => old?.filter((a) => a.id !== id))
      void qc.invalidateQueries({ queryKey: qk.alertsAll(cid) })
      void qc.invalidateQueries({ queryKey: qk.cofounder(cid) })
      void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
    },
  })
}
