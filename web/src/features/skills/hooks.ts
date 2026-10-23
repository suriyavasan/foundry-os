import { useQueries } from '@tanstack/react-query'
import { useMemo } from 'react'
import { fetchAgentSkills, useAgents } from '@/api'
import { qk } from '@/api/keys'
import { assignedAgentCounts } from '@/lib/skills'

/**
 * skill id -> number of agents it is assigned to. There is no aggregate endpoint, so this reads each
 * live agent's `GET /agents/{id}/skills` (a handful of small requests, cached together with the skills
 * keys so assignment changes refresh it).
 */
export function useAssignedCounts(companyId: string) {
  const agents = useAgents(companyId)
  const live = useMemo(() => (agents.data ?? []).filter((a) => a.status !== 'retired' && a.status !== 'rejected'), [agents.data])
  const results = useQueries({
    queries: live.map((a) => ({
      queryKey: qk.agentSkills(companyId, a.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchAgentSkills(companyId, a.id, signal),
      select: (d: Awaited<ReturnType<typeof fetchAgentSkills>>) => d.items,
    })),
  })
  const counts = useMemo(() => assignedAgentCounts(results.map((r) => r.data)), [results])
  const loading = agents.isPending || results.some((r) => r.isPending)
  const failed = agents.isError || results.some((r) => r.isError)
  return { counts, loading, failed }
}
