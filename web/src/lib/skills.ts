import type {
  AgentSkill,
  AuditEvent,
  RoleFamily,
  RoleTemplate,
  ScanVerdict,
  Skill,
  SkillDetail,
  SkillFilters,
  SkillScanFinding,
  SkillScanReport,
  SkillSourceKind,
  SkillStatus,
  SkillTrust,
  Task,
} from '@/api/types'
import { ROLE_FAMILIES } from './roleFamily'

/** Skills (M4): pure helpers behind the Skills widget, library table, catalog and detail. */

export type SkillTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

/* ------------------------------ badges & labels ------------------------------ */

export interface VerdictInfo {
  label: string
  tone: SkillTone
  /** Needs a human's attention: caution or dangerous. */
  flagged: boolean
  help: string
}

const VERDICTS: Record<ScanVerdict, VerdictInfo> = {
  safe: { label: 'Safe', tone: 'success', flagged: false, help: 'The security scan found nothing concerning.' },
  caution: { label: 'Caution', tone: 'warning', flagged: true, help: 'The scan found patterns that need a look (shell access, network calls, file writes). Review the findings.' },
  dangerous: { label: 'Dangerous', tone: 'danger', flagged: true, help: 'The scan found patterns that are risky or malicious-looking. Do not enable it without reading the findings.' },
  unscanned: { label: 'Unscanned', tone: 'neutral', flagged: false, help: 'The scanner was not available, so this built-in skill was stored without a scan.' },
}

/** Unknown verdicts (a newer backend) are shown as-is and treated as flagged: never silently "safe". */
export function scanVerdictInfo(verdict: string): VerdictInfo {
  return (VERDICTS as Record<string, VerdictInfo>)[verdict] ?? { label: verdict || 'Unknown', tone: 'warning', flagged: true, help: 'Unrecognised scan verdict.' }
}

const STATUSES: Record<SkillStatus, { label: string; tone: SkillTone; help: string }> = {
  active: { label: 'Active', tone: 'success', help: 'Can be assigned to agents.' },
  quarantined: { label: 'Quarantined', tone: 'danger', help: 'Stored for inspection but never assigned to agents or given to the runtime.' },
  archived: { label: 'Archived', tone: 'neutral', help: 'Retired from the library; never given to the runtime.' },
}

export function skillStatusInfo(status: string) {
  return (STATUSES as Record<string, (typeof STATUSES)[SkillStatus]>)[status] ?? { label: status, tone: 'neutral' as const, help: '' }
}

/** True when a human should look at it: quarantined, or the scan says caution/dangerous. */
export function isFlagged(skill: Pick<Skill, 'status' | 'scan_verdict'>): boolean {
  return skill.status === 'quarantined' || scanVerdictInfo(skill.scan_verdict).flagged
}

export const SOURCE_KIND_LABEL: Record<SkillSourceKind, string> = {
  hermes_bundled: 'Hermes bundled',
  hermes_optional: 'Hermes optional',
  github: 'GitHub',
  company_authored: 'Company authored',
  uploaded: 'Uploaded',
}
export const SOURCE_KINDS = Object.keys(SOURCE_KIND_LABEL) as SkillSourceKind[]
export const sourceKindLabel = (k: string) => (SOURCE_KIND_LABEL as Record<string, string>)[k] ?? k

export const TRUST_LABEL: Record<SkillTrust, string> = { builtin: 'Built-in', trusted: 'Trusted source', community: 'Community' }
export const trustLabel = (t: string) => (TRUST_LABEL as Record<string, string>)[t] ?? t

/* -------------------------------- filtering --------------------------------- */

export interface LibraryFilters {
  q: string
  status: SkillStatus | 'all'
  source: SkillSourceKind | 'all'
  tag: string | 'all'
}
export const NO_FILTERS: LibraryFilters = { q: '', status: 'all', source: 'all', tag: 'all' }

const matchesQuery = (s: Skill, q: string) => {
  const hay = [s.name, s.slug, s.description, s.category ?? '', s.author ?? '', ...s.tags].join(' ').toLowerCase()
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((t) => hay.includes(t))
}

export function filterSkills(skills: Skill[], f: LibraryFilters): Skill[] {
  return skills.filter(
    (s) =>
      (f.status === 'all' || s.status === f.status) &&
      (f.source === 'all' || s.source_kind === f.source) &&
      (f.tag === 'all' || s.tags.includes(f.tag) || s.category === f.tag) &&
      (f.q.trim() === '' || matchesQuery(s, f.q)),
  )
}

/** Quarantined first, then caution/dangerous, then the rest; alphabetical inside each group. */
export function sortSkills(skills: Skill[]): Skill[] {
  const rank = (s: Skill) => (s.status === 'quarantined' ? 0 : isFlagged(s) ? 1 : s.status === 'archived' ? 3 : 2)
  return [...skills].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
}

/** Tag options: tags and categories in use, most common first. */
export function tagOptions(skills: Skill[]): string[] {
  const counts = new Map<string, number>()
  for (const s of skills) for (const t of new Set([...s.tags, ...(s.category ? [s.category] : [])])) counts.set(t, (counts.get(t) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([t]) => t)
}

/** Only these filters go to the server; the rest is client-side. Kept so URLs/filters are explicit. */
export function toServerFilters(f: LibraryFilters): SkillFilters {
  return {
    ...(f.status !== 'all' ? { status: f.status } : {}),
    ...(f.source !== 'all' ? { source_kind: f.source } : {}),
    ...(f.tag !== 'all' ? { tag: f.tag } : {}),
    ...(f.q.trim() ? { q: f.q.trim() } : {}),
  }
}

/* -------------------------------- summaries --------------------------------- */

export interface LibrarySummary {
  total: number
  active: number
  quarantined: number
  archived: number
  flagged: number
}

export function summarizeLibrary(skills: Skill[]): LibrarySummary {
  return {
    total: skills.length,
    active: skills.filter((s) => s.status === 'active').length,
    quarantined: skills.filter((s) => s.status === 'quarantined').length,
    archived: skills.filter((s) => s.status === 'archived').length,
    flagged: skills.filter((s) => s.status !== 'archived' && isFlagged(s)).length,
  }
}

export interface FamilySkillCount {
  family: RoleFamily
  count: number
}

/**
 * Skills per role family: how many distinct active library skills the family's role templates ship
 * with by default. A skill used by several families counts in each. Families with no template skill
 * are omitted; nothing is estimated.
 */
export function skillsByFamily(skills: Skill[], templates: RoleTemplate[]): FamilySkillCount[] {
  const active = new Set(skills.filter((s) => s.status === 'active').map((s) => s.slug))
  const byFamily = new Map<RoleFamily, Set<string>>()
  for (const t of templates) {
    const set = byFamily.get(t.family) ?? new Set<string>()
    for (const slug of t.default_skill_slugs) if (active.has(slug)) set.add(slug)
    byFamily.set(t.family, set)
  }
  return ROLE_FAMILIES.filter((f) => (byFamily.get(f)?.size ?? 0) > 0).map((family) => ({ family, count: byFamily.get(family)!.size }))
}

/** skill id -> number of agents it is assigned to, from each agent's `GET /agents/{id}/skills`. */
export function assignedAgentCounts(perAgent: Array<AgentSkill[] | undefined>): Map<string, number> {
  const out = new Map<string, number>()
  for (const list of perAgent) for (const a of list ?? []) out.set(a.skill.id, (out.get(a.skill.id) ?? 0) + 1)
  return out
}

/* ------------------------------- scan report -------------------------------- */

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, dangerous: 1, medium: 2, caution: 2, low: 3, info: 4 }
export const severityTone = (sev: string | undefined): SkillTone =>
  sev === 'critical' || sev === 'high' || sev === 'dangerous' ? 'danger' : sev === 'medium' || sev === 'caution' ? 'warning' : 'neutral'

export interface ReadScanReport {
  verdict: ScanVerdict | string
  scanner_version?: string
  summary?: string
  finding_count: number
  findings: SkillScanFinding[]
}

/** `scan_report` is a free-form object on the wire; read the documented members defensively. */
export function readScanReport(detail: Pick<SkillDetail, 'scan_report' | 'scan_verdict'>): ReadScanReport {
  const r = (detail.scan_report ?? {}) as SkillScanReport
  const findings = (Array.isArray(r.findings) ? r.findings : [])
    .filter((f): f is SkillScanFinding => !!f && typeof f === 'object')
    .sort((a, b) => (SEVERITY_RANK[a.severity ?? ''] ?? 9) - (SEVERITY_RANK[b.severity ?? ''] ?? 9))
  return {
    verdict: typeof r.verdict === 'string' ? r.verdict : detail.scan_verdict,
    scanner_version: typeof r.scanner_version === 'string' ? r.scanner_version : undefined,
    summary: typeof r.summary === 'string' ? r.summary : undefined,
    finding_count: typeof r.finding_count === 'number' ? r.finding_count : findings.length,
    findings,
  }
}

/** The `SKILL.md` entry of a skill's file list (root level, case-insensitive). */
export function skillMdPath(files: Array<{ path: string }>): string | null {
  return files.find((f) => f.path === 'SKILL.md')?.path ?? files.find((f) => f.path.toLowerCase() === 'skill.md')?.path ?? null
}

/* ---------------------------- HR activity & hiring ---------------------------- */

const HR_ACTIONS = new Set([
  'hire.created',
  'hire.requested',
  'skill.installed',
  'skill.updated',
  'skill.quarantined',
  'skill.unquarantined',
  'skill.archived',
  'skill_library.seeded',
  'skill_library.seed_requested',
  'agent.skills_changed',
  'role_template.created',
])
export const isHrAction = (action: string) => HR_ACTIONS.has(action)

export interface HrActivityItem {
  key: string
  at: string
  action: string
  count: number
  text: string
  targetType: string
  targetId: string | null
  tone: SkillTone
}

const BURST_MS = 5 * 60_000
const PLURAL_TEXT: Record<string, (n: number) => string> = {
  'skill.installed': (n) => `${n} skills installed`,
  'skill.updated': (n) => `${n} skills updated`,
  'skill.quarantined': (n) => `${n} skills quarantined`,
  'agent.skills_changed': (n) => `Skills changed for ${n} agents`,
}
const ACTION_TONE: Record<string, SkillTone> = { 'skill.quarantined': 'danger', 'skill.unquarantined': 'warning', 'hire.created': 'success', 'hire.requested': 'info' }

/**
 * HR-relevant audit events, newest first, with bursts of the same action (seeding installs 35 skills in
 * one go) collapsed into one line so a bulk import does not bury real hires.
 */
export function collapseHrActivity(events: AuditEvent[], limit = 6): HrActivityItem[] {
  const hr = events.filter((e) => isHrAction(e.action)).sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
  const out: HrActivityItem[] = []
  let prevTs = 0 // timestamp of the previous (newer) event, to measure the gap inside a burst
  for (const e of hr) {
    const ts = new Date(e.created_at).getTime()
    const last = out[out.length - 1]
    if (last && last.action === e.action && PLURAL_TEXT[e.action] && Math.abs(prevTs - ts) <= BURST_MS) {
      last.count += 1
      last.text = PLURAL_TEXT[e.action]!(last.count)
      last.targetId = null
    } else {
      out.push({ key: e.id, at: e.created_at, action: e.action, count: 1, text: e.summary, targetType: e.target_type, targetId: e.target_id, tone: ACTION_TONE[e.action] ?? 'neutral' })
    }
    prevTs = ts
  }
  return out.slice(0, limit)
}

const TERMINAL: ReadonlySet<string> = new Set(['done', 'failed', 'cancelled'])

/** Hiring tasks that are not finished (the HR agent still has to act). */
export function openHiringRequests(tasks: Task[]): Task[] {
  return tasks.filter((t) => t.kind === 'hiring' && !TERMINAL.has(t.status)).sort((a, b) => b.created_at.localeCompare(a.created_at))
}
