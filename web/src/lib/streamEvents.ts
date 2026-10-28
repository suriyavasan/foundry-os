import { qk } from '@/api/keys'
import type { Agent, AuditEvent, CompanyEvent } from '@/api/types'
import { normalizeLive } from './liveState'
import { ID_RE, type Panel } from './panels'

/**
 * Pure core of `useCompanyStream`: what a CompanyEvent from the SSE outbox does to the client.
 *
 * The M2 spec fixes the event *types* but not their payloads, so everything here is defensive:
 * payload fields are read as optional (`agent_id`, `task_id`, `run_id`, `title`, `status`,
 * `summary`, `message`), an unrecognised payload degrades to "invalidate and refetch", and an
 * unknown event type is ignored. Nothing is ever fabricated from a missing field.
 */

export type QueryKey = readonly unknown[]

export type CacheEffect =
  | { kind: 'patchAgentLive'; agentId: string; live: NonNullable<Agent['live']> }
  | { kind: 'invalidate'; key: QueryKey }

export type FeedTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'
export type FeedKind = 'run' | 'task' | 'agent' | 'approval' | 'artifact' | 'activity' | 'decision' | 'note' | 'alert' | 'guardrail'

export interface FeedItem {
  /** Stable id for de-duplication across SSE replays and REST seeds. */
  key: string
  at: string
  kind: FeedKind
  tone: FeedTone
  agentId: string | null
  /** Text without the actor's name (the UI renders the avatar + name separately). */
  text: string
  /** Where clicking the item should go. */
  panel: Panel | null
}

/** Event types the backend may emit (M2 spec §1 CompanyEvent). Used to register SSE listeners. */
export const KNOWN_EVENT_TYPES = [
  'task.created',
  'task.updated',
  'run.queued',
  'run.started',
  'run.event',
  'run.finished',
  'agent.updated',
  'agent.activity',
  'artifact.created',
  'approval.created',
  'approval.decided',
  'activity',
  'budget.changed',
  'policy.updated',
  'company.updated',
  // M4 skills & HR (docs/api.md: company events)
  'skill.installed',
  'skill.updated',
  'skill.quarantined',
  'skill.archived',
  'skill_library.seeded',
  'agent.skills_changed',
  'role_template.created',
  'hire.created',
  'hire.requested',
  // M5 Founder's notes, AI Co-founder, memory, alerts (docs/specs/m5-autonomous-company.md §2, §7)
  'note.created',
  'note.updated',
  'note.triaged',
  'note.replied',
  'cofounder.woke',
  'memory.created',
  'memory.updated',
  'alert.raised',
  'alert.resolved',
  // M6 guardrails & finance (docs/specs/m6-guardrails-and-governance.md). `guardrail.event` is specified; the
  // others are assumed names (an event no listener is registered for is simply never delivered).
  'guardrail.event',
  'guardrail.rule_changed',
  'system.emergency_stop',
  'system.resumed',
  'expense.created',
  // M7 accounts & team (docs/specs/m7 §5). Notifications are per user: the event only tells the client to look again.
  'notification.created',
  'notification.updated',
  'member.updated',
  'invitation.updated',
  // M8 reachability & identity (assumed names; invalidate-only)
  'reachability.updated',
  'tunnel.updated',
  'connected_account.updated',
  'domain.updated',
  'mailbox.updated',
  'hosted_ui.updated',
  // M10 reflections, standards, command center (docs/specs/m10 §4-§5)
  'reflection.created',
  'reflection.updated',
  'reflection.responded',
  'standard.adopted',
  'standard.updated',
  'command.updated',
  // M14 GitHub-backed projects (sync state, pull requests, intake)
  'repository.updated',
  // M16 market radar: briefs, items and idea decisions have their own events; fetches, sources and keywords
  // arrive as `activity` events whose action starts with `radar.` (handled below).
  'radar.brief.created',
  'radar.item.updated',
  'radar.idea.created',
  'radar.idea.accepted',
  'radar.idea.rejected',
  'radar.idea.reopened',
  'radar.idea.merged',
  'radar.idea.done',
  'radar.idea.expired',
] as const

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null

function agentIdOf(p: Record<string, unknown>): string | null {
  return str(p.agent_id) ?? str(obj(p.agent)?.id) ?? null
}

/** Parse one SSE message into a CompanyEvent. Accepts a full envelope or a bare payload for a named event. */
export function parseCompanyEvent(
  namedType: string | null,
  data: string,
  lastEventId: string | null | undefined,
): CompanyEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  const o = obj(parsed)
  if (!o) return null
  const idRaw = o.id ?? lastEventId
  const id = typeof idRaw === 'number' ? idRaw : Number(idRaw)
  const safeId = Number.isFinite(id) ? id : 0
  const envelopeType = str(o.type)
  if (envelopeType && 'payload' in o) {
    return {
      id: safeId,
      type: envelopeType,
      payload: obj(o.payload) ?? {},
      created_at: str(o.created_at) ?? undefined,
    }
  }
  if (namedType && namedType !== 'message') {
    return { id: safeId, type: namedType, payload: o, created_at: str(o.created_at) ?? undefined }
  }
  return null
}

/** Cache work caused by an event. */
export function effectsForEvent(cid: string, ev: CompanyEvent): CacheEffect[] {
  const p = ev.payload
  const inv = (key: QueryKey): CacheEffect => ({ kind: 'invalidate', key })

  switch (ev.type) {
    case 'agent.activity': {
      const agentId = agentIdOf(p)
      const live = normalizeLive(p.live ?? p)
      if (agentId && live) return [{ kind: 'patchAgentLive', agentId, live }]
      return [inv(qk.agents(cid))]
    }
    case 'agent.updated':
      return [inv(qk.agents(cid)), inv(qk.overview(cid)), inv(qk.departments(cid))]
    case 'task.created':
    case 'task.updated':
      return [inv(qk.tasksAll(cid)), inv(qk.overview(cid))]
    case 'run.queued':
    case 'run.started':
      return [inv(qk.runsAll(cid)), inv(qk.tasksAll(cid))]
    case 'run.finished':
      return [inv(qk.runsAll(cid)), inv(qk.tasksAll(cid)), inv(qk.usageAll(cid)), inv(qk.financeAll(cid)), inv(qk.artifactsAll(cid))]
    case 'run.event': {
      const runId = str(p.run_id)
      return runId ? [inv(qk.runEvents(cid, runId))] : [inv(qk.runsAll(cid))]
    }
    case 'artifact.created':
      return [inv(qk.artifactsAll(cid))]
    case 'approval.created':
    case 'approval.decided':
      return [inv(qk.approvalsAll(cid)), inv(qk.tasksAll(cid))]
    case 'activity':
      // M16: a source, keyword or fetch change is only announced as an audit event named `radar.*`.
      return typeof p.action === 'string' && p.action.startsWith('radar.') ? [inv(qk.activityAll(cid)), inv(qk.radarAll(cid))] : [inv(qk.activityAll(cid))]
    case 'budget.changed':
    case 'policy.updated':
      // The AI setup (management model, work mode) lives in the policy: the gate re-reads it.
      return [inv(qk.policy(cid)), inv(qk.usageAll(cid)), inv(qk.financeAll(cid)), inv(qk.tasksAll(cid)), inv(qk.aiSetup(cid))]
    case 'company.updated':
      return [inv(qk.companyDetail(cid)), inv(qk.companies)]
    case 'skill.installed':
    case 'skill.updated':
    case 'skill.quarantined':
    case 'skill.archived':
    case 'skill_library.seeded':
    case 'agent.skills_changed':
    case 'role_template.created':
      return [inv(qk.skillsAll(cid)), inv(qk.roleTemplates(cid)), inv(qk.activityAll(cid))]
    case 'hire.created':
    case 'hire.requested':
      return [inv(qk.agents(cid)), inv(qk.tasksAll(cid)), inv(qk.approvalsAll(cid)), inv(qk.overview(cid)), inv(qk.departments(cid)), inv(qk.skillsAll(cid)), inv(qk.activityAll(cid))]
    case 'note.created':
    case 'note.updated':
      return [inv(qk.notesAll(cid)), inv(qk.cofounderSummary(cid)), inv(qk.activityAll(cid))]
    case 'note.replied': {
      const noteId = str(p.note_id)
      return [inv(qk.notesAll(cid)), ...(noteId ? [inv(qk.note(cid, noteId))] : []), inv(qk.cofounderSummary(cid)), inv(qk.activityAll(cid))]
    }
    case 'note.triaged':
      // Triage creates real records (goals, projects, tasks, schedules, memory): refresh what shows them.
      return [
        inv(qk.notesAll(cid)),
        inv(qk.goals(cid)),
        inv(qk.projects(cid)),
        inv(qk.tasksAll(cid)),
        inv(qk.schedules(cid)),
        inv(qk.memoryAll(cid)),
        inv(qk.cofounderSummary(cid)),
        inv(qk.activityAll(cid)),
      ]
    case 'cofounder.woke':
      return [inv(qk.cofounder(cid)), inv(qk.tasksAll(cid)), inv(qk.runsAll(cid))]
    case 'memory.created':
    case 'memory.updated':
      return [inv(qk.memoryAll(cid)), inv(qk.activityAll(cid))]
    case 'alert.raised':
    case 'alert.resolved':
      return [inv(qk.alertsAll(cid)), inv(qk.cofounderSummary(cid))]
    case 'guardrail.event':
      // A decision can open an approval (peer review / approval tiers) and moves the rate-limit counters.
      return [inv(qk.guardrailsAll(cid)), inv(qk.approvalsAll(cid)), inv(qk.activityAll(cid))]
    case 'guardrail.rule_changed':
      return [inv(qk.guardrailsAll(cid)), inv(qk.activityAll(cid))]
    case 'system.emergency_stop':
    case 'system.resumed':
      return [inv(qk.systemStatus), inv(qk.companyDetail(cid)), inv(qk.companies), inv(qk.runsAll(cid)), inv(qk.tasksAll(cid)), inv(qk.agents(cid))]
    case 'expense.created':
      return [inv(qk.financeAll(cid)), inv(qk.activityAll(cid))]
    case 'notification.created':
    case 'notification.updated':
      return [inv(qk.notificationsAll)]
    case 'member.updated':
    case 'invitation.updated':
      return [inv(qk.membersAll(cid)), inv(qk.agents(cid)), inv(qk.activityAll(cid))]
    case 'reachability.updated':
    case 'tunnel.updated':
      return [inv(qk.reachability), inv(qk.hostedUi(cid))]
    case 'connected_account.updated':
      return [inv(qk.connectedAccounts(cid)), inv(qk.reachability), inv(qk.emailAll(cid)), inv(qk.activityAll(cid))]
    case 'domain.updated':
      return [inv(qk.domainAll(cid)), inv(qk.reachability), inv(qk.approvalsAll(cid)), inv(qk.activityAll(cid))]
    case 'mailbox.updated':
      return [inv(qk.emailAll(cid)), inv(qk.approvalsAll(cid)), inv(qk.activityAll(cid))]
    case 'hosted_ui.updated':
      return [inv(qk.hostedUi(cid)), inv(qk.approvalsAll(cid)), inv(qk.activityAll(cid))]
    case 'reflection.created':
    case 'reflection.updated':
      return [inv(qk.reflectionsAll(cid)), inv(qk.activityAll(cid))]
    case 'reflection.responded': {
      const id = str(p.reflection_id) ?? str(obj(p.reflection)?.id)
      return [inv(qk.reflectionsAll(cid)), ...(id ? [inv(qk.reflection(cid, id))] : []), inv(qk.activityAll(cid))]
    }
    case 'standard.adopted':
    case 'standard.updated':
      return [inv(qk.standardsAll(cid)), inv(qk.reflectionsAll(cid)), inv(qk.activityAll(cid))]
    case 'command.updated':
      return [inv(qk.commandFeed(cid)), inv(qk.notesAll(cid))]
    case 'repository.updated':
      return [inv(qk.reposAll(cid)), inv(qk.activityAll(cid))]
    // M16 market radar: briefs, items and idea decisions refresh the radar views; a brief also adds an artifact, and an
    // idea that became work also touches tasks, projects and goals.
    case 'radar.item.updated':
      return [inv(qk.radarAll(cid))]
    case 'radar.brief.created':
      return [inv(qk.radarAll(cid)), inv(qk.artifactsAll(cid)), inv(qk.activityAll(cid))]
    case 'radar.idea.created':
    case 'radar.idea.accepted':
    case 'radar.idea.rejected':
    case 'radar.idea.reopened':
    case 'radar.idea.merged':
    case 'radar.idea.done':
    case 'radar.idea.expired':
      return [inv(qk.radarAll(cid)), inv(qk.tasksAll(cid)), inv(qk.goals(cid)), inv(qk.projects(cid)), inv(qk.activityAll(cid))]
    default:
      return []
  }
}

/** Collapse duplicate invalidations (a burst of events should cause one refetch per key). */
export function dedupeEffects(effects: CacheEffect[]): CacheEffect[] {
  const seen = new Set<string>()
  const out: CacheEffect[] = []
  // Later live patches for the same agent supersede earlier ones.
  for (let i = effects.length - 1; i >= 0; i--) {
    const e = effects[i]!
    const id = e.kind === 'invalidate' ? `i:${JSON.stringify(e.key)}` : `p:${e.agentId}`
    if (seen.has(id)) continue
    seen.add(id)
    out.unshift(e)
  }
  return out
}

/** Immutable update of a cached agent list; returns the same reference when nothing changes. */
export function patchAgentLive<T extends Agent[] | undefined>(agents: T, agentId: string, live: NonNullable<Agent['live']>): T {
  if (!agents) return agents
  let changed = false
  const next = agents.map((a) => {
    if (a.id !== agentId) return a
    changed = true
    return { ...a, live }
  })
  return (changed ? next : agents) as T
}

/** Same for a single cached agent. */
export function patchOneAgentLive(agent: Agent | undefined, agentId: string, live: NonNullable<Agent['live']>): Agent | undefined {
  return agent && agent.id === agentId ? { ...agent, live } : agent
}

/* ------------------------------ live activity feed ------------------------------ */

const RUN_EVENT_TYPES_WITH_TEXT = new Set(['progress', 'decision', 'message', 'tool', 'tool_call', 'note', 'status'])

function taskPanel(p: Record<string, unknown>): Panel | null {
  const id = str(p.task_id) ?? str(obj(p.task)?.id)
  return id ? { kind: 'task', id } : null
}

function notePanel(p: Record<string, unknown>): Panel | null {
  const id = str(p.note_id) ?? str(obj(p.note)?.id)
  return id && ID_RE.test(id) ? { kind: 'note', id } : null
}

/** One human-readable line per event, or null when the event is not worth showing. */
export function feedItemForEvent(ev: CompanyEvent, now: string = new Date().toISOString()): FeedItem | null {
  const p = ev.payload
  const at = ev.created_at ?? str(p.occurred_at) ?? now
  const key = `ev:${ev.id}:${ev.type}`
  const agentId = agentIdOf(p)
  const title = str(p.title) ?? str(obj(p.task)?.title)
  const status = str(p.status)
  const base = { key, at, agentId }

  switch (ev.type) {
    case 'agent.activity': {
      const live = normalizeLive(p.live ?? p)
      const text = live?.summary ?? str(p.summary)
      if (!text) return null
      return { ...base, kind: 'agent', tone: live?.state === 'blocked' ? 'warning' : 'accent', text, panel: live?.current_task ? { kind: 'task', id: live.current_task.id } : taskPanel(p) }
    }
    case 'task.created':
      return { ...base, kind: 'task', tone: 'neutral', text: title ? `New task: ${title}` : 'New task created', panel: taskPanel(p) }
    case 'task.updated': {
      const label = status ? status.replace(/_/g, ' ') : null
      const tone: FeedTone = status === 'done' ? 'success' : status === 'failed' ? 'danger' : status === 'blocked' ? 'warning' : 'neutral'
      const text = title ? (label ? `${title} is now ${label}` : `Updated: ${title}`) : label ? `Task is now ${label}` : 'Task updated'
      return { ...base, kind: 'task', tone, text, panel: taskPanel(p) }
    }
    case 'run.queued':
      return { ...base, kind: 'run', tone: 'neutral', text: title ? `Queued a run for ${title}` : 'Run queued', panel: taskPanel(p) }
    case 'run.started':
      return { ...base, kind: 'run', tone: 'accent', text: title ? `Started working on ${title}` : 'Run started', panel: taskPanel(p) }
    case 'run.finished': {
      const ok = status === 'succeeded'
      const tone: FeedTone = ok ? 'success' : status === 'cancelled' ? 'neutral' : 'danger'
      const verb = status ? status.replace(/_/g, ' ') : 'finished'
      return { ...base, kind: 'run', tone, text: title ? `Run ${verb}: ${title}` : `Run ${verb}`, panel: taskPanel(p) }
    }
    case 'run.event': {
      const type = str(p.type) ?? str(obj(p.event)?.type)
      const inner = obj(p.payload) ?? obj(obj(p.event)?.payload) ?? p
      const text = str(inner.message) ?? str(inner.summary) ?? str(inner.title)
      if (!text || (type && !RUN_EVENT_TYPES_WITH_TEXT.has(type))) return null
      return { ...base, kind: type === 'decision' ? 'decision' : 'run', tone: 'neutral', text, panel: taskPanel(p) }
    }
    case 'artifact.created': {
      const id = str(p.artifact_id) ?? str(p.id)
      return { ...base, kind: 'artifact', tone: 'neutral', text: title ? `Produced ${title}` : 'New artifact', panel: id ? { kind: 'artifact', id } : { kind: 'artifacts' } }
    }
    case 'approval.created':
      return { ...base, kind: 'approval', tone: 'warning', text: title ? `Requested approval: ${title}` : 'Approval requested', panel: { kind: 'approvals' } }
    case 'approval.decided':
      return { ...base, kind: 'approval', tone: status === 'approved' ? 'success' : 'neutral', text: title ? `Approval ${status ?? 'decided'}: ${title}` : `Approval ${status ?? 'decided'}`, panel: { kind: 'approvals' } }
    case 'activity': {
      const summary = str(p.summary)
      if (!summary) return null
      const actorType = str(p.actor_type)
      return { ...base, agentId: actorType === 'agent' ? str(p.actor_id) : null, kind: 'activity', tone: 'neutral', text: summary, panel: null }
    }
    case 'note.created':
      return { ...base, agentId: null, kind: 'note', tone: 'neutral', text: 'The Founder wrote a note', panel: notePanel(p) ?? { kind: 'notes' } }
    case 'note.triaged': {
      const category = str(p.category) ?? str(obj(p.triage)?.category)
      return { ...base, kind: 'note', tone: 'success', text: category ? `The AI Co-founder triaged a note as ${category}` : 'The AI Co-founder triaged a note', panel: notePanel(p) ?? { kind: 'notes' } }
    }
    case 'note.replied': {
      const byHuman = str(p.author_type) === 'human'
      return { ...base, agentId: byHuman ? null : agentId, kind: 'note', tone: 'neutral', text: byHuman ? 'The Founder replied on a note' : 'The AI Co-founder replied to a note', panel: notePanel(p) ?? { kind: 'notes' } }
    }
    case 'reflection.created': {
      const rid = str(p.reflection_id) ?? str(p.id)
      return { ...base, kind: 'note', tone: 'accent', text: title ? `The AI Co-founder shared a reflection: ${title}` : 'The AI Co-founder shared a reflection', panel: rid && ID_RE.test(rid) ? { kind: 'reflection', id: rid } : { kind: 'reflections' } }
    }
    case 'standard.adopted':
      return { ...base, agentId: null, kind: 'note', tone: 'success', text: title ? `Standard adopted: ${title}` : 'A company standard was adopted', panel: { kind: 'standards' } }
    case 'alert.raised': {
      const sev = str(p.severity)
      return { ...base, kind: 'alert', tone: sev === 'critical' ? 'danger' : sev === 'info' ? 'neutral' : 'warning', text: title ? `Alert: ${title}` : 'An alert was raised', panel: null }
    }
    case 'guardrail.event': {
      const decision = str(p.decision)
      // Allowed actions are the norm and would drown the feed; only decisions that held something back are shown.
      if (!decision || decision === 'allow') return null
      const action = str(p.action)
      const tone: FeedTone = decision === 'deny' || decision === 'rate_limited' ? 'danger' : 'warning'
      const verb = decision === 'deny' ? 'Blocked' : decision === 'rate_limited' ? 'Rate limited' : decision === 'peer_review' ? 'Sent to peer review' : 'Needs approval'
      return { ...base, kind: 'guardrail', tone, text: action ? `${verb}: ${action}` : verb, panel: { kind: 'guardrails' } }
    }
    default:
      return null
  }
}

/** Seed the feed from the REST audit log so it is never empty after load. */
export function feedItemForAudit(e: AuditEvent): FeedItem {
  return {
    key: `audit:${e.id}`,
    at: e.created_at,
    kind: 'activity',
    tone: 'neutral',
    agentId: e.actor_type === 'agent' ? e.actor_id : null,
    text: e.summary,
    panel: e.target_type === 'agent' && e.target_id ? { kind: 'agent', id: e.target_id } : null,
  }
}

/**
 * Merge feed sources newest-first. A stream `activity` event mirrors an AuditEvent, so it is dropped
 * when the same text at the same instant is already present from the REST seed.
 */
export function mergeFeed(sources: FeedItem[][], limit = 60): FeedItem[] {
  const seenKeys = new Set<string>()
  const seenText = new Set<string>()
  const all = sources.flat().sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
  const out: FeedItem[] = []
  for (const item of all) {
    if (seenKeys.has(item.key)) continue
    const textKey = `${item.at}|${item.text}`
    if (seenText.has(textKey)) continue
    seenKeys.add(item.key)
    seenText.add(textKey)
    out.push(item)
    if (out.length >= limit) break
  }
  return out
}
