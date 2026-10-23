import { useEffect, useMemo, useState } from 'react'
import { useAgents, useCompany, useMe } from '@/api'
import type { Agent } from '@/api/types'

/** Re-render every `ms` so relative times ("3 min ago") stay honest. */
export function useTick(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

export function useAgentMap(companyId: string) {
  const q = useAgents(companyId)
  const map = useMemo(() => new Map<string, Agent>((q.data ?? []).map((a) => [a.id, a])), [q.data])
  return { query: q, map, agents: q.data ?? [] }
}

export function useCompanyTimezone(companyId: string): string {
  return useCompany(companyId).data?.timezone ?? 'UTC'
}

/** The signed-in human's role in this company (`owner`, `admin`, `viewer`), or undefined while unknown. */
export function useCompanyRole(companyId: string): string | undefined {
  return useMe().data?.memberships.find((m) => m.company_id === companyId)?.role
}

/** Only owners and admins edit policy, rules and budgets (the server enforces this; the UI just does not offer it to others). */
export const canEditPolicy = (role: string | undefined): boolean => role === 'owner' || role === 'admin'
