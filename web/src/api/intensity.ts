import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { Intensity, IntensityUpdate } from './types'
import { isNotDeployed } from './work'

const path = (cid: string | undefined) => `/companies/${cid}/intensity`

/** The company intensity (M23). A backend without it answers 404: callers hide the control (`isNotDeployed`). */
export function useIntensity(cid: string | undefined) {
  return useQuery({
    queryKey: qk.intensity(cid ?? ''),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
    queryFn: ({ signal }) => apiFetch<Intensity>(path(cid), { signal }),
  })
}

/** Set a level (owner/admin). Writes the preset into the policy, so the policy and activity are re-read too. */
export function useSetIntensity(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: IntensityUpdate) => apiFetch<Intensity>(path(cid), { method: 'PUT', body }),
    onSuccess: async (data) => {
      qc.setQueryData(qk.intensity(cid), data)
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.policy(cid) }),
        qc.invalidateQueries({ queryKey: qk.intensity(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.schedules(cid) }),
      ])
    },
  })
}
