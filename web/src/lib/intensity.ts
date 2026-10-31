import { formatInterval } from './dates'

/** The five levels, lowest to highest. The server owns what each level does; these are only the names. */
export const INTENSITY_LEVELS = [
  { level: 1, name: 'Conserve' },
  { level: 2, name: 'Light' },
  { level: 3, name: 'Balanced' },
  { level: 4, name: 'Busy' },
  { level: 5, name: 'Continuous' },
] as const

export const BUDGETS_STILL_APPLY = 'Budgets still apply. Intensity changes the pace, never what the company may spend.'

/** Fallback wording; the server's own `warning` text is shown when it sends one. */
export const PLAN_ONLY_WARNING = 'Personal plans assume ordinary individual use; high intensity may hit usage limits - add an API key for heavy autonomy.'

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * What the values in force mean, in plain words. Derived from the server's `effective` map (never from a copy of
 * the presets), so a hand-edited policy is described as it really is. A field the server does not send is left out.
 */
export function describeIntensity(effective: Record<string, unknown>): string[] {
  const lines: string[] = []
  const beat = num(effective.heartbeat_interval_seconds)
  if (beat !== null) lines.push(`The Co-founder checks in every ${formatInterval(beat)}.`)
  const conc = num(effective.max_concurrent_runs)
  if (conc !== null) lines.push(`Up to ${plural(conc, 'agent works', 'agents work')} at once.`)
  const planning = num(effective.max_planning_runs_per_hour)
  if (planning !== null) lines.push(`Each agent plans at most ${plural(planning, 'time', 'times')} an hour.`)
  if (effective.initiative_enabled === false) lines.push('It does not start new work on its own.')
  else if (effective.initiative_enabled === true) {
    const every = num(effective.initiative_interval_seconds)
    lines.push(every !== null ? `When it is idle for ${formatInterval(every)}, it starts new work on its own.` : 'When it is idle, it starts new work on its own.')
  }
  if (effective.meetings_enabled === false) lines.push('No agent meetings.')
  else if (effective.meetings_enabled === true) {
    const meetings = num(effective.max_meetings_per_company_per_day)
    lines.push(meetings !== null ? `Up to ${plural(meetings, 'meeting', 'meetings')} a day between agents.` : 'Agents may hold meetings.')
  }
  if (effective.self_check_enabled === false) lines.push('Agents do not double-check their own results before finishing.')
  else if (effective.self_check_enabled === true) lines.push('Agents double-check their own results before finishing.')
  return lines
}

/** Levels 4 and 5 are the ones that need a confirmation on a plan-only stack. */
export const needsPlanConfirm = (level: number, planOnly: boolean): boolean => planOnly && level >= 4
