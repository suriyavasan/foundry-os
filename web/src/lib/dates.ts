/** Calendar dates in a company's IANA timezone (budgets and "today" follow company time, not the browser). */

export function isoDateInZone(date: Date, timeZone: string): string {
  try {
    // en-CA formats as YYYY-MM-DD.
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
  } catch {
    return date.toISOString().slice(0, 10)
  }
}

export function monthStartIso(todayIso: string): string {
  return `${todayIso.slice(0, 7)}-01`
}

export function isSameMonth(dayIso: string, todayIso: string): boolean {
  return dayIso.slice(0, 7) === todayIso.slice(0, 7)
}

/** Human interval: 900 -> "15 min", 3600 -> "1 h", 90 -> "90 s". */
export function formatInterval(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} d`
  if (seconds % 3600 === 0) return `${seconds / 3600} h`
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}
