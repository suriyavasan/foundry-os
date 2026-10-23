import { useMemo } from 'react'
import { useUsage } from '@/api'
import { isoDateInZone, monthStartIso } from '@/lib/dates'
import { useTick } from './hooks'

/** "Today" and the first of the month in the company's timezone. */
export function useCompanyDates(timezone: string) {
  const now = useTick(60_000)
  return useMemo(() => {
    const today = isoDateInZone(new Date(now), timezone)
    return { today, monthStart: monthStartIso(today), weekStart: isoDateInZone(new Date(now - 6 * 86_400_000), timezone) }
  }, [now, timezone])
}

/** Today's spend for one agent, split by provenance. `undefined` while loading, `null` if unavailable. */
export function useAgentSpendToday(companyId: string, agentId: string, timezone: string) {
  const { today } = useCompanyDates(timezone)
  const q = useUsage(companyId, 'agent', today, today)
  if (q.isError || q.isPending) return { loading: q.isPending, unavailable: q.isError, row: null }
  const row = q.data.rows.find((r) => r.key === agentId) ?? null
  return { loading: false, unavailable: false, row }
}
