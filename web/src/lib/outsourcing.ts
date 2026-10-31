import type { Marketplace, OutsourceJob, OutsourceStatus } from '@/api/types'
import type { Tone } from '@/components/ui'
import { usdToMicros } from './money'

/**
 * Pure helpers for M22 outsourcing. HR drafts a freelance job, a person approves it, posts it on the marketplace's own site and
 * records what happened. Foundry never posts, messages, hires or pays on a marketplace, so every "next step" below is something a
 * person does outside Foundry. The brief, title and notes were written by an AI employee (or a person): plain text.
 */

export const MARKETPLACE_LABEL: Record<Marketplace, string> = { fiverr: 'Fiverr', upwork: 'Upwork', other: 'Another marketplace' }

export const OUTSOURCE_STATUS_LABEL: Record<OutsourceStatus, string> = {
  draft: 'Draft',
  pending_approval: 'Waiting for approval',
  approved: 'Approved',
  posted: 'Posted',
  hired: 'Hired',
  delivered: 'Delivered',
  closed: 'Closed',
  cancelled: 'Cancelled',
  declined: 'Declined',
}

export const OUTSOURCE_STATUS_TONE: Record<OutsourceStatus, Tone> = {
  draft: 'neutral',
  pending_approval: 'warning',
  approved: 'info',
  posted: 'info',
  hired: 'accent',
  delivered: 'success',
  closed: 'neutral',
  cancelled: 'neutral',
  declined: 'danger',
}

export const isOpenJob = (j: Pick<OutsourceJob, 'status'>): boolean => !['closed', 'cancelled', 'declined'].includes(j.status)

/** Freelancers shown as "Contractors": hired (working) or delivered (done, not yet closed). */
export const isContractor = (j: Pick<OutsourceJob, 'status' | 'freelancer_name'>): boolean => (j.status === 'hired' || j.status === 'delivered') && !!j.freelancer_name

export type JobAction = 'posted' | 'hired' | 'delivered' | 'paid' | 'raise-budget' | 'close' | 'cancel'

/** The steps a person can record now, in the order they usually happen. The server validates every transition again. */
export function availableActions(j: OutsourceJob): JobAction[] {
  switch (j.status) {
    case 'approved':
      return ['posted', 'hired', 'raise-budget', 'cancel']
    case 'posted':
      return ['hired', 'raise-budget', 'cancel']
    case 'hired':
      return [...(j.paid_micros == null ? (['delivered', 'paid'] as const) : (['delivered'] as const)), 'hired', 'raise-budget', 'close', ...(j.paid_micros == null ? (['cancel'] as const) : [])]
    case 'delivered':
      return [...(j.paid_micros == null ? (['paid'] as const) : []), 'close', ...(j.paid_micros == null ? (['cancel'] as const) : [])]
    case 'pending_approval':
    case 'draft':
      return ['cancel']
    default:
      return []
  }
}

/** One line of what happens next (and who does it). */
export function nextStep(j: OutsourceJob): string {
  switch (j.status) {
    case 'draft':
    case 'pending_approval':
      return 'A person decides the approval first. Nothing is posted until then.'
    case 'approved':
      return `Approved. Post it on ${MARKETPLACE_LABEL[j.marketplace]} yourself (or pick a freelancer directly), then record it here.`
    case 'posted':
      return 'Waiting for you to hire someone. Record who and the agreed price when you do.'
    case 'hired':
      return j.paid_micros == null ? 'Work in progress. Record the delivery, and the payment once you have paid on the marketplace.' : 'Work in progress. Record the delivery when it arrives.'
    case 'delivered':
      return j.paid_micros == null ? 'Delivered. Record the payment you made, then close the job.' : 'Delivered and paid. Close the job when you are happy.'
    case 'closed':
      return 'Done.'
    case 'cancelled':
      return 'Cancelled. Nothing was posted or paid by Foundry.'
    case 'declined':
      return 'The approval was declined (or timed out). HR can draft another job.'
  }
}

/** Helper link only: the marketplace's own public search. Never fetched by Foundry. Only https links the server built are used. */
export const safeOpenUrl = (j: Pick<OutsourceJob, 'open_url'>): string | null => (j.open_url && /^https:\/\//.test(j.open_url) ? j.open_url : null)

/** The brief and its facts as one block of text to paste into the marketplace's own form. */
export function briefText(j: Pick<OutsourceJob, 'title' | 'brief' | 'skills' | 'budget_micros'>): string {
  const lines = [j.title, '', j.brief]
  if (j.skills.length) lines.push('', `Skills: ${j.skills.join(', ')}`)
  lines.push('', `Budget: up to $${(j.budget_micros / 1_000_000).toFixed(2)}`)
  return lines.join('\n')
}

/** A dollar amount typed by a person -> USD number for the API (the server converts to micros); null when invalid or not above zero. */
export function parseUsd(input: string, opts: { allowZero?: boolean } = {}): number | null {
  const micros = usdToMicros(input)
  if (micros === null || (micros === 0 && !opts.allowZero)) return null
  return micros / 1_000_000
}

/** Is `url` something we can accept as a link for this marketplace (https, and its own domain for Fiverr/Upwork)? The server decides for real. */
export function listingUrlProblem(marketplace: Marketplace, url: string): string | null {
  let u: URL
  try {
    u = new URL(url.trim())
  } catch {
    return 'Paste the full link, starting with https://'
  }
  if (u.protocol !== 'https:') return 'The link must start with https://'
  if (u.username || u.password) return 'The link must not contain a username or password'
  const domain = marketplace === 'fiverr' ? 'fiverr.com' : marketplace === 'upwork' ? 'upwork.com' : null
  if (domain && u.hostname !== domain && !u.hostname.endsWith(`.${domain}`)) return `That is not a ${MARKETPLACE_LABEL[marketplace]} link (it must be on ${domain})`
  return null
}
