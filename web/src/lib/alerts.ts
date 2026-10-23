import type { Alert, AlertSeverity } from '@/api/types'
import { panelForRef } from './refs'
import type { Panel } from './panels'
import type { Tone } from '@/components/ui/Badge'

export const SEVERITY_ORDER: Record<AlertSeverity, number> = { critical: 0, warning: 1, info: 2 }
export const SEVERITY_LABEL: Record<AlertSeverity, string> = { critical: 'Critical', warning: 'Warning', info: 'Info' }
export const SEVERITY_TONE: Record<AlertSeverity, Tone> = { critical: 'danger', warning: 'warning', info: 'info' }

/** More open alerts than this collapse to a single count line (expandable). */
export const ALERT_STRIP_LIMIT = 3

/** Most severe first, then newest first. */
export function sortAlerts(alerts: Alert[]): Alert[] {
  return [...alerts].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3) || (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0),
  )
}

export const openAlerts = (alerts: Alert[] | undefined): Alert[] => (alerts ?? []).filter((a) => a.status === 'open')

export type StripModel =
  | { mode: 'hidden'; items: []; count: 0; worst: null }
  | { mode: 'list' | 'collapsed' | 'expanded'; items: Alert[]; count: number; worst: AlertSeverity }

/**
 * What the strip shows. Nothing without open alerts; up to `limit` alerts listed as rows; beyond that a
 * single "N open alerts" line (worst severity colours it) until the Founder expands it.
 */
export function stripModel(alerts: Alert[] | undefined, expanded: boolean, limit: number = ALERT_STRIP_LIMIT): StripModel {
  const open = sortAlerts(openAlerts(alerts))
  if (open.length === 0) return { mode: 'hidden', items: [], count: 0, worst: null }
  const worst = open[0]!.severity
  if (open.length <= limit) return { mode: 'list', items: open, count: open.length, worst }
  return expanded ? { mode: 'expanded', items: open, count: open.length, worst } : { mode: 'collapsed', items: open.slice(0, 0), count: open.length, worst }
}

/** Where "open the related record" goes; null when the alert is not about a record we can show. */
export function alertPanel(alert: Pick<Alert, 'related_type' | 'related_id'>): Panel | null {
  return panelForRef(alert.related_type, alert.related_id)
}

/** "3 open alerts, 1 critical" */
export function alertCountText(alerts: Alert[]): string {
  const open = openAlerts(alerts)
  const critical = open.filter((a) => a.severity === 'critical').length
  const base = `${open.length} open alert${open.length === 1 ? '' : 's'}`
  return critical > 0 ? `${base}, ${critical} critical` : base
}
