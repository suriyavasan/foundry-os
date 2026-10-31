import type { Meeting, MeetingDetail, MeetingStatus } from '@/api/types'
import type { CollabLink } from './orgMap'

/**
 * Pure helpers for M21 agent meetings. The backend types `outcome` and a message's `proposals` as free objects, so they are
 * read defensively here: a missing or odd field degrades to "nothing", never to an invented value.
 */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)

export const MEETING_STATUS_LABEL: Record<MeetingStatus, string> = {
  scheduled: 'Scheduled',
  running: 'In progress',
  concluded: 'Concluded',
  cancelled: 'Cancelled',
  failed: 'Failed',
}

export const MEETING_STATUS_TONE: Record<MeetingStatus, 'neutral' | 'accent' | 'success' | 'danger'> = {
  scheduled: 'neutral',
  running: 'accent',
  concluded: 'success',
  cancelled: 'neutral',
  failed: 'danger',
}

/** A meeting that can still be cancelled. */
export const isMeetingOpen = (m: Pick<Meeting, 'status'>): boolean => m.status === 'scheduled' || m.status === 'running'

export interface OutcomeDecision {
  title: string
  rationale: string | null
}

/** How one action item ended: a task was created, the owner never agreed, or creating the task was refused. */
export type ActionItemState = 'task' | 'agreed' | 'not_agreed' | 'refused'

export interface OutcomeActionItem {
  id: string
  title: string
  description: string | null
  ownerAgentId: string | null
  accepted: boolean
  taskId: string | null
  refused: string | null
  state: ActionItemState
}

export interface MeetingOutcome {
  decisions: OutcomeDecision[]
  actionItems: OutcomeActionItem[]
}

export function readOutcome(outcome: MeetingDetail['outcome'] | null | undefined): MeetingOutcome {
  const o = obj(outcome)
  const decisions: OutcomeDecision[] = []
  for (const d of Array.isArray(o?.decisions) ? o.decisions : []) {
    const r = obj(d)
    const title = str(r?.title)
    if (r && title) decisions.push({ title, rationale: str(r.rationale) })
  }
  const actionItems: OutcomeActionItem[] = []
  for (const [i, it] of (Array.isArray(o?.action_items) ? o.action_items : []).entries()) {
    const r = obj(it)
    const title = str(r?.title)
    if (!r || !title) continue
    const taskId = str(r.task_id)
    const refused = str(r.refused)
    const accepted = r.accepted === true
    actionItems.push({
      id: str(r.id) ?? `item-${i}`,
      title,
      description: str(r.description),
      ownerAgentId: str(r.owner_agent_id),
      accepted,
      taskId,
      refused,
      state: taskId ? 'task' : refused ? 'refused' : accepted ? 'agreed' : 'not_agreed',
    })
  }
  return { decisions, actionItems }
}

/** The text that says how an action item ended. */
export function actionItemNote(item: OutcomeActionItem): string {
  switch (item.state) {
    case 'task':
      return 'Task created'
    case 'refused':
      return `No task: ${item.refused}`
    case 'agreed':
      return 'Agreed'
    case 'not_agreed':
      return 'Not agreed'
  }
}

/** The one-line "this one passed" marker the backend records for a skipped turn (`proposals.pass`). */
export function isPassMessage(proposals: unknown): boolean {
  return obj(proposals)?.pass === true
}

/** `round 2/2`. A meeting that has not opened a round yet is shown as round 1. */
export function roundLabel(m: Pick<Meeting, 'round' | 'max_rounds'>): string {
  return `round ${Math.min(Math.max(1, m.round), Math.max(1, m.max_rounds))}/${Math.max(1, m.max_rounds)}`
}

/** Participants in speaking order (organizer first). */
export function orderedParticipants<T extends { position: number; role: string }>(ps: readonly T[]): T[] {
  return [...ps].sort((a, b) => a.position - b.position || (a.role === 'organizer' ? -1 : b.role === 'organizer' ? 1 : 0))
}

/** "Ana, Raj, Lee" */
export function participantNames(m: Pick<Meeting, 'participants'>): string {
  return orderedParticipants(m.participants)
    .map((p) => p.name)
    .join(', ')
}

/** The live-activity line: "Meeting · Ana, Raj, Lee · Plan the launch · round 2/2". */
export function meetingLine(m: Pick<Meeting, 'participants' | 'title' | 'round' | 'max_rounds'>): string {
  return ['Meeting', participantNames(m), m.title, roundLabel(m)].filter(Boolean).join(' · ')
}

/**
 * Pairs of agents who share a meeting that is running right now, one `meeting` link per pair, ordered so a pair appears once
 * (the org map caps how many lines touch one circle).
 */
export function meetingLinksFor(meetings: readonly Pick<Meeting, 'status' | 'participants'>[]): CollabLink[] {
  const seen = new Set<string>()
  const out: CollabLink[] = []
  for (const m of meetings) {
    if (m.status !== 'running') continue
    const ids = [...new Set(m.participants.map((p) => p.agent_id))].sort()
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const key = `${ids[i]}|${ids[j]}`
        if (seen.has(key)) continue
        seen.add(key)
        out.push({ a: ids[i]!, b: ids[j]!, kind: 'meeting' })
      }
    }
  }
  return out
}

export interface MessageProposals {
  proposed: Array<{ id: string; title: string; ownerAgentId: string | null }>
  accepted: number
  declined: number
}

/** What a message proposed / accepted / declined (`proposals` on a MeetingMessage). */
export function readProposals(proposals: unknown): MessageProposals {
  const o = obj(proposals)
  const proposed: MessageProposals['proposed'] = []
  for (const [i, p] of (Array.isArray(o?.proposed) ? o.proposed : []).entries()) {
    const r = obj(p)
    const title = str(r?.title)
    if (r && title) proposed.push({ id: str(r.id) ?? `p-${i}`, title, ownerAgentId: str(r.owner_agent_id) })
  }
  return {
    proposed,
    accepted: Array.isArray(o?.accepted) ? o.accepted.length : 0,
    declined: Array.isArray(o?.declined) ? o.declined.length : 0,
  }
}
