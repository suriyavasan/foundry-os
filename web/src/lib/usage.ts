import type { CostKind, UsageReport, UsageRow } from '@/api/types'

const KINDS: CostKind[] = ['verified', 'estimated', 'unknown']

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)
const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null

export function emptyRow(key = 'total'): UsageRow {
  return { key, label: null, input_tokens: 0, output_tokens: 0, total_micros: 0, by_kind: { verified: 0, estimated: 0, unknown: 0 } }
}

function addInto(into: UsageRow, from: UsageRow) {
  into.input_tokens += from.input_tokens
  into.output_tokens += from.output_tokens
  into.total_micros += from.total_micros
  for (const k of KINDS) into.by_kind[k] += from.by_kind[k]
}

const KEY_FIELDS = ['key', 'group', 'group_key', 'agent_id', 'project_id', 'model', 'day', 'date']

/**
 * The M2 spec leaves the `/usage` payload open ("aggregates (micros, tokens, by cost_kind)"), so accept
 * the plausible shapes and fold them into one: per row, a `by_cost_kind` map, `verified_micros` /
 * `estimated_micros` / `unknown_micros`, or a single `cost_kind` with `cost_micros`. Rows sharing a key
 * (e.g. one row per kind) are merged. If a row reports only a total with no kind, it counts as
 * `unknown`: we do not guess provenance.
 */
export function normalizeUsage(raw: unknown): UsageReport {
  const root = rec(raw)
  const list = Array.isArray(root?.items) ? root.items : Array.isArray(root?.rows) ? root.rows : Array.isArray(raw) ? raw : []
  const byKey = new Map<string, UsageRow>()

  for (const item of list) {
    const o = rec(item)
    if (!o) continue
    let key: string | null = null
    for (const f of KEY_FIELDS) {
      key = str(o[f])
      if (key) break
    }
    key = key ?? 'total'
    const row = emptyRow(key)
    row.label = str(o.label) ?? str(o.name) ?? null
    row.input_tokens = num(o.input_tokens)
    row.output_tokens = num(o.output_tokens)

    const kinds = rec(o.by_cost_kind) ?? rec(o.by_kind)
    if (kinds) {
      for (const k of KINDS) {
        // The documented API shape is `{verified_micros, estimated_micros, unknown_micros}` (schema CostByKind); the bare names are also read.
        const v = kinds[`${k}_micros`] ?? kinds[k]
        row.by_kind[k] = typeof v === 'number' ? v : num(rec(v)?.cost_micros ?? rec(v)?.micros)
      }
    } else if (KINDS.some((k) => `${k}_micros` in o)) {
      for (const k of KINDS) row.by_kind[k] = num(o[`${k}_micros`])
    } else {
      const total = num(o.cost_micros ?? o.total_micros)
      const kind = KINDS.includes(o.cost_kind as CostKind) ? (o.cost_kind as CostKind) : 'unknown'
      row.by_kind[kind] = total
    }
    row.total_micros = KINDS.reduce((s, k) => s + row.by_kind[k], 0)

    const existing = byKey.get(key)
    if (existing) {
      addInto(existing, row)
      existing.label = existing.label ?? row.label
    } else {
      byKey.set(key, row)
    }
  }

  const rows = [...byKey.values()]
  const totals = emptyRow('total')
  for (const r of rows) addInto(totals, r)
  return { rows, totals }
}

/** Sum a subset of rows (e.g. the days of the current month). */
export function sumRows(rows: UsageRow[]): UsageRow {
  const t = emptyRow('sum')
  for (const r of rows) addInto(t, r)
  return t
}

/** Share of `limit` used, 0..1+, or null when there is no limit. */
export function budgetFraction(usedMicros: number, limitMicros: number | null | undefined): number | null {
  if (limitMicros === null || limitMicros === undefined || limitMicros <= 0) return null
  return usedMicros / limitMicros
}
