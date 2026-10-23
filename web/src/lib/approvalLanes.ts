import type { ActionTier, Approval, ApprovalOutbound, DlpFinding } from '@/api/types'
import { TIERS } from './guardrails'

/** Who decides: a human (you), the AI Co-founder, or a peer reviewer agent (M6 maker-checker). */
export type ApprovalLane = 'human' | 'cofounder' | 'peer'

export const LANE_ORDER: ApprovalLane[] = ['human', 'cofounder', 'peer']

export const LANE_LABEL: Record<ApprovalLane, string> = {
  human: 'You decide',
  cofounder: 'AI Co-founder decides',
  peer: 'Peer review',
}

export function approvalLane(a: Pick<Approval, 'approver'>): ApprovalLane {
  return a.approver === 'founder' ? 'cofounder' : a.approver === 'peer' ? 'peer' : 'human'
}

export interface TierGroup {
  /** null = the approval carries no tier (approvals that predate M6, or non-guarded kinds). */
  tier: ActionTier | null
  items: Approval[]
}

/** Group by risk tier in the canonical tier order (riskiest last), untiered approvals at the end. Newest first inside a group. */
export function groupByTier(items: Approval[]): TierGroup[] {
  const byTier = new Map<ActionTier | null, Approval[]>()
  for (const a of items) {
    const tier = a.tier && TIERS.includes(a.tier) ? a.tier : null
    byTier.set(tier, [...(byTier.get(tier) ?? []), a])
  }
  const order: Array<ActionTier | null> = [...TIERS, null]
  return order
    .filter((t) => byTier.has(t))
    .map((tier) => ({ tier, items: [...byTier.get(tier)!].sort((x, y) => (x.created_at < y.created_at ? 1 : x.created_at > y.created_at ? -1 : 0)) }))
}

export function splitByLane(items: Approval[]): Record<ApprovalLane, Approval[]> {
  const out: Record<ApprovalLane, Approval[]> = { human: [], cofounder: [], peer: [] }
  for (const a of items) out[approvalLane(a)].push(a)
  return out
}

/* ------------------------------ outbound content & DLP ------------------------------ */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

/**
 * The exact content under review. Preferred source is the top-level `outbound`; failing that the scope or evidence
 * object (`outbound`, or a bare `body` / `content` / `text`). Plain text only: it is rendered as text, never as HTML.
 */
export function readOutbound(a: Pick<Approval, 'outbound' | 'scope' | 'evidence'>): ApprovalOutbound | null {
  const candidates = [a.outbound, obj(a.scope)?.outbound, obj(a.evidence)?.outbound, a.scope, a.evidence]
  for (const c of candidates) {
    const o = obj(c)
    if (!o) continue
    const body = str(o.body) ?? str(o.content) ?? str(o.text)
    if (!body) continue
    return { body, channel: str(o.channel), to: str(o.to) ?? str(o.recipient), subject: str(o.subject) }
  }
  return null
}

const SEVERITIES = new Set(['low', 'medium', 'high'])

/**
 * DLP findings, whitelisted to `kind`, `severity`, `description`, `count`. Any other field the server might send (a
 * matched value, an excerpt) is dropped here so it can never reach the screen.
 */
export function readDlpFindings(a: Pick<Approval, 'dlp_findings' | 'evidence' | 'scope'>): DlpFinding[] {
  const raw = Array.isArray(a.dlp_findings) ? a.dlp_findings : Array.isArray(obj(a.evidence)?.dlp_findings) ? (obj(a.evidence)!.dlp_findings as unknown[]) : Array.isArray(obj(a.scope)?.dlp_findings) ? (obj(a.scope)!.dlp_findings as unknown[]) : []
  const out: DlpFinding[] = []
  for (const f of raw) {
    const o = obj(f)
    const kind = str(o?.kind) ?? str(o?.type)
    if (!o || !kind) continue
    out.push({
      kind,
      severity: typeof o.severity === 'string' && SEVERITIES.has(o.severity) ? (o.severity as DlpFinding['severity']) : null,
      description: str(o.description),
      count: typeof o.count === 'number' && Number.isFinite(o.count) ? o.count : null,
    })
  }
  return out
}
