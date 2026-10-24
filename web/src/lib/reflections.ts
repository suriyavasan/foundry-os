import type {
  CompanyStandard,
  CompanyStandardCreate,
  Reflection,
  ReflectionEvidence,
  ReflectionKind,
  ReflectionReaction,
  ReflectionResponse,
  ReflectionStatus,
} from '@/api/types'
import type { Tone } from '@/components/ui/Badge'
import type { Panel } from './panels'
import { panelForRef, refTypeLabel } from './refs'
import { AI_COFOUNDER_LABEL, YOU_FOUNDER_LABEL } from './terms'

/**
 * Reflections and company standards (docs/specs/m10 §4): pure helpers. The rule that matters: a standard exists only
 * because a human endorsed a proposal (or wrote it). The AI Co-founder proposes; it never adopts or retires.
 */

export const REFLECTION_KINDS: ReflectionKind[] = ['observation', 'standard_proposal', 'retro', 'risk', 'praise']

export const REFLECTION_KIND_LABEL: Record<ReflectionKind, string> = {
  observation: 'Observation',
  standard_proposal: 'Standard proposal',
  retro: 'Retro',
  risk: 'Risk',
  praise: 'Praise',
}

export const REFLECTION_KIND_TONE: Record<ReflectionKind, Tone> = {
  observation: 'info',
  standard_proposal: 'accent',
  retro: 'neutral',
  risk: 'warning',
  praise: 'success',
}

export const REFLECTION_STATUSES: ReflectionStatus[] = ['open', 'endorsed', 'revised', 'rejected', 'adopted', 'archived']

export const REFLECTION_STATUS_LABEL: Record<ReflectionStatus, string> = {
  open: 'Open',
  endorsed: 'Endorsed',
  revised: 'Revised',
  rejected: 'Challenged',
  adopted: 'Adopted',
  archived: 'Archived',
}

export const REFLECTION_STATUS_TONE: Record<ReflectionStatus, Tone> = {
  open: 'info',
  endorsed: 'success',
  revised: 'accent',
  rejected: 'warning',
  adopted: 'success',
  archived: 'neutral',
}

export const REFLECTION_STATUS_HELP: Record<ReflectionStatus, string> = {
  open: 'Waiting for your reaction.',
  endorsed: 'You agreed with it.',
  revised: 'The AI Co-founder revised it after your response. Look at the new version.',
  rejected: 'You challenged it.',
  adopted: 'Adopted as a company standard. Every agent now follows it.',
  archived: 'Closed.',
}

export function reflectionKindLabel(k: string): string {
  return (REFLECTION_KIND_LABEL as Record<string, string>)[k] ?? k.replace(/_/g, ' ')
}

/** Newest first; ties by id so the order is stable. */
export function sortReflections<T extends Pick<Reflection, 'created_at' | 'id'>>(list: T[]): T[] {
  return [...list].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : a.id < b.id ? 1 : -1))
}

/** Plain-text preview of a markdown body: no syntax, one line, cut on a word. */
export function reflectionExcerpt(body: string, max = 180): string {
  const plain = body
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  if (plain.length <= max) return plain
  const cut = plain.slice(0, max)
  const space = cut.lastIndexOf(' ')
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`
}

/* ---------------------------------- evidence chips --------------------------------- */

export interface EvidenceChip {
  key: string
  typeLabel: string
  label: string
  panel: Panel | null
}

/** Evidence refs as chips; a chip opens the panel of its record when the type is one the UI can show. */
export function evidenceChips(evidence: ReflectionEvidence[] | null | undefined): EvidenceChip[] {
  return (evidence ?? []).map((e, i) => ({
    key: `${e.type}:${e.id}:${i}`,
    typeLabel: refTypeLabel(e.type),
    label: e.label?.trim() || refTypeLabel(e.type),
    panel: panelForRef(e.type, e.id),
  }))
}

/* ------------------------------------- reactions ------------------------------------ */

export const REACTION_LABEL: Record<ReflectionReaction, string> = { endorse: 'Endorse', challenge: 'Challenge', refine: 'Refine', comment: 'Comment' }

export const REACTION_PAST: Record<ReflectionReaction, string> = { endorse: 'endorsed', challenge: 'challenged', refine: 'asked to refine', comment: 'commented' }

export const REACTION_TONE: Record<ReflectionReaction, Tone> = { endorse: 'success', challenge: 'warning', refine: 'accent', comment: 'neutral' }

/** Statuses in which a reaction still makes sense. Closed reflections show their status only. */
export function canReact(r: Pick<Reflection, 'status'>): boolean {
  return r.status === 'open' || r.status === 'revised' || r.status === 'endorsed'
}

export function isStandardProposal(r: Pick<Reflection, 'kind' | 'proposed_standard'>): boolean {
  return r.kind === 'standard_proposal'
}

/** Whether endorsing this reflection adopts a company standard. Said out loud in the UI before the click. */
export function endorsementAdopts(r: Pick<Reflection, 'kind' | 'proposed_standard' | 'status'>): boolean {
  return isStandardProposal(r) && !!r.proposed_standard?.trim() && r.status !== 'adopted'
}

export const ADOPTION_NOTICE = 'Endorsing adopts this as a company standard. Every agent will be given it as a rule to follow, and only you can retire it.'

export function adoptionNotice(r: Pick<Reflection, 'kind' | 'proposed_standard' | 'status'>): string | null {
  return endorsementAdopts(r) ? ADOPTION_NOTICE : null
}

export interface ReactionDraftCheck {
  ok: boolean
  body: string
  message: string | null
}

/** Challenge, refine and comment need words; an endorsement may be silent. */
export function validateReaction(reaction: ReflectionReaction, text: string): ReactionDraftCheck {
  const body = text.trim()
  if (reaction !== 'endorse' && !body) {
    const ask = reaction === 'challenge' ? 'Say what you disagree with so the AI Co-founder can answer it.' : reaction === 'refine' ? 'Say what should change so the AI Co-founder can revise it.' : 'Write a comment first.'
    return { ok: false, body, message: ask }
  }
  if (body.length > 4000) return { ok: false, body, message: 'Keep the response under 4,000 characters.' }
  return { ok: true, body, message: null }
}

/** Placeholder for the inline composer, per reaction. */
export function reactionPlaceholder(reaction: ReflectionReaction): string {
  switch (reaction) {
    case 'endorse':
      return 'Optional: add a note with your endorsement.'
    case 'challenge':
      return 'What do you disagree with, and why?'
    case 'refine':
      return 'What should change before this is right?'
    default:
      return 'Write a comment.'
  }
}

/** The button label that sends the composer. */
export function sendLabel(reaction: ReflectionReaction, adopts: boolean): string {
  if (reaction === 'endorse') return adopts ? 'Endorse and adopt as a standard' : 'Endorse'
  return reaction === 'challenge' ? 'Send challenge' : reaction === 'refine' ? 'Ask to refine' : 'Send comment'
}

/** The toast after a successful reaction. */
export function reactionResultText(reaction: ReflectionReaction, adopted: boolean): string {
  if (reaction === 'endorse' && adopted) return 'Adopted as a company standard. Agents will follow it from their next run.'
  if (reaction === 'endorse') return 'Endorsed.'
  return `Sent. The ${AI_COFOUNDER_LABEL} will answer or revise.`
}

/** Who wrote a response: the signed-in human, another person, or an agent (the AI Co-founder by name). */
export function responseAuthor(r: Pick<ReflectionResponse, 'author_type' | 'author_id'>, ctx: { meUserId?: string | null; agentName?: (id: string) => string | null }): string {
  if (r.author_type === 'human') return r.author_id && ctx.meUserId === r.author_id ? YOU_FOUNDER_LABEL : 'Team member'
  const name = r.author_id ? ctx.agentName?.(r.author_id) : null
  return name ? `${name} · ${AI_COFOUNDER_LABEL}` : AI_COFOUNDER_LABEL
}

/* ------------------------------------- standards ------------------------------------- */

export function splitStandards(list: CompanyStandard[]): { active: CompanyStandard[]; retired: CompanyStandard[] } {
  const byAdopted = (a: CompanyStandard, b: CompanyStandard) => (b.adopted_at ?? '').localeCompare(a.adopted_at ?? '')
  return { active: list.filter((s) => s.status === 'active').sort(byAdopted), retired: list.filter((s) => s.status === 'retired').sort(byAdopted) }
}

export const STANDARD_TITLE_MAX = 120
export const STANDARD_STATEMENT_MAX = 500

export interface StandardForm {
  title: string
  statement: string
  appliesTo: string
}

export function emptyStandardForm(s?: CompanyStandard): StandardForm {
  return { title: s?.title ?? '', statement: s?.statement ?? '', appliesTo: (s?.applies_to ?? []).join(', ') }
}

export function parseAppliesTo(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((t) => t.trim().toLowerCase().replace(/\s+/g, '-')).filter(Boolean))].slice(0, 20)
}

export type StandardFormErrors = Partial<Record<keyof StandardForm, string>>

export function validateStandardForm(f: StandardForm): StandardFormErrors {
  const e: StandardFormErrors = {}
  const t = f.title.trim()
  if (!t) e.title = 'Give the standard a short title'
  else if (t.length > STANDARD_TITLE_MAX) e.title = `Use at most ${STANDARD_TITLE_MAX} characters`
  const s = f.statement.trim()
  if (!s) e.statement = 'Write the rule itself, in one or two sentences'
  else if (s.length > STANDARD_STATEMENT_MAX) e.statement = `Use at most ${STANDARD_STATEMENT_MAX} characters`
  return e
}

export function toStandardBody(f: StandardForm): CompanyStandardCreate {
  const appliesTo = parseAppliesTo(f.appliesTo)
  return { title: f.title.trim(), statement: f.statement.trim(), ...(appliesTo.length ? { applies_to: appliesTo } : null) }
}

export function standardSourceNote(s: CompanyStandard): string {
  return s.source_reflection_id ? 'Adopted from a reflection you endorsed' : 'Written by you'
}
