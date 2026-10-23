import type { Schedule } from '@/api/types'
import { formatInterval } from '@/lib/dates'

export function describeCadence(s: Pick<Schedule, 'interval_seconds' | 'cron' | 'timezone'>): string {
  if (s.interval_seconds) return `Every ${formatInterval(s.interval_seconds)}`
  if (s.cron) return `Cron ${s.cron} (${s.timezone})`
  return 'No cadence set'
}

/** Enabled first, then soonest next run; schedules with no next run last. */
export function sortSchedules(list: Schedule[]): Schedule[] {
  return [...list].sort((a, b) => {
    if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
    if (a.next_run_at && b.next_run_at) return a.next_run_at.localeCompare(b.next_run_at)
    if (a.next_run_at) return -1
    if (b.next_run_at) return 1
    return a.name.localeCompare(b.name)
  })
}
