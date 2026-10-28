import type { Tone } from '@/components/ui'
import type { BriefKind, IdeaKind, IdeaOrigin, IdeaStatus, ItemStatus, KeywordKind, RadarIdea, RadarSource, ThemeKind } from '@/api/radar'

/**
 * Pure helpers of the Radar panel. Everything the radar shows from outside (item titles, excerpts, brief summaries,
 * idea text) is untrusted: it is only ever rendered as text, and a link is only built for an http(s) address.
 */

/** The address as an `href`, or null for anything that is not a plain http(s) link (`javascript:`, `data:`, relative...). */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const u = new URL(url)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null
  } catch {
    return null
  }
}

/** The host part of a link for display, or the raw text when it is not a link. */
export function hostOf(url: string | null | undefined): string {
  if (!url) return ''
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** Anything the owner types as a feed or page address: http(s) only. */
export function isHttpUrl(value: string): boolean {
  return safeHref(value.trim()) !== null
}

/* ------------------------------------ tabs ------------------------------------ */

export const RADAR_TABS = ['briefs', 'ideas', 'feed', 'sources', 'keywords'] as const
export type RadarTab = (typeof RADAR_TABS)[number]
export const isRadarTab = (v: string | null): v is RadarTab => !!v && (RADAR_TABS as readonly string[]).includes(v)

/* ------------------------------------ labels ------------------------------------ */

export const BRIEF_KIND_LABEL: Record<BriefKind, string> = { daily: 'Daily radar', self_critique: 'Self-critique', ui_inspiration: 'UI inspiration' }

export const THEME_KIND: Record<ThemeKind, { label: string; tone: Tone }> = {
  critique: { label: 'Critique', tone: 'danger' },
  opportunity: { label: 'Opportunity', tone: 'success' },
  competitor: { label: 'Competitor', tone: 'warning' },
  ui: { label: 'UI', tone: 'info' },
  risk: { label: 'Risk', tone: 'warning' },
}

export const IDEA_STATUS: Record<IdeaStatus, { label: string; tone: Tone }> = {
  open: { label: 'Open', tone: 'accent' },
  accepted: { label: 'Accepted', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'neutral' },
  merged: { label: 'Merged', tone: 'neutral' },
  done: { label: 'Done', tone: 'success' },
  expired: { label: 'Expired', tone: 'neutral' },
}

export const IDEA_KIND_LABEL: Record<IdeaKind, string> = { fix: 'Fix', feature: 'Feature', ui: 'UI', positioning: 'Positioning', research: 'Research' }
export const IDEA_ORIGIN_LABEL: Record<IdeaOrigin, string> = { radar: 'From the radar', self_critique: 'From self-critique', ui_inspiration: 'From UI inspiration', owner: 'Added by you' }

export const IDEA_FILTERS: Array<{ value: IdeaStatus | 'all'; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'done', label: 'Done' },
  { value: 'merged', label: 'Merged' },
  { value: 'expired', label: 'Expired' },
  { value: 'all', label: 'All' },
]

export const ITEM_STATUS: Record<ItemStatus, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'accent' },
  seen: { label: 'Seen', tone: 'neutral' },
  used: { label: 'Used', tone: 'success' },
  dismissed: { label: 'Dismissed', tone: 'neutral' },
}

export const ITEM_STATUS_FILTERS: Array<{ value: ItemStatus | 'all'; label: string }> = [
  { value: 'all', label: 'Any status' },
  { value: 'new', label: 'New' },
  { value: 'seen', label: 'Seen' },
  { value: 'used', label: 'Used' },
  { value: 'dismissed', label: 'Dismissed' },
]

export const KEYWORD_KIND: Record<KeywordKind, { label: string; hint: string }> = {
  product: { label: 'Product', hint: 'Your own product or company name' },
  competitor: { label: 'Competitor', hint: 'Someone you compete with' },
  topic: { label: 'Topic', hint: 'A subject worth watching' },
  negative: { label: 'Negative', hint: 'An item that matches this is dropped' },
}

export const KEYWORD_ORIGIN_LABEL = { derived: 'Derived from your profile and goals', owner: 'Added by you', agent: 'Added by agents' } as const

export const SOURCE_CATEGORIES = ['social', 'forum', 'news', 'blog', 'launch site', 'review site', 'UI inspiration', 'research', 'changelog'] as const

const FETCH_STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: 'Fetched', tone: 'success' },
  not_modified: { label: 'Unchanged', tone: 'success' },
  robots_disallowed: { label: 'Blocked by robots.txt', tone: 'warning' },
  http_4xx: { label: 'Refused (4xx)', tone: 'danger' },
  http_5xx: { label: 'Site error (5xx)', tone: 'danger' },
  timeout: { label: 'Timed out', tone: 'danger' },
  network_error: { label: 'Network error', tone: 'danger' },
  too_large: { label: 'Too large', tone: 'danger' },
  blocked_destination: { label: 'Blocked address', tone: 'danger' },
  parse_error: { label: 'Could not read it', tone: 'danger' },
  rate_limited: { label: 'Rate limited', tone: 'warning' },
  content_type: { label: 'Wrong content type', tone: 'danger' },
  redirect_refused: { label: 'Redirect refused', tone: 'danger' },
  config_error: { label: 'Needs a value', tone: 'warning' },
  needs_token: { label: 'Needs a token', tone: 'warning' },
  no_keywords: { label: 'No keywords to ask for', tone: 'warning' },
}

export function fetchStatus(status: string | null | undefined): { label: string; tone: Tone } {
  if (!status) return { label: 'Not fetched yet', tone: 'neutral' }
  return FETCH_STATUS[status] ?? { label: status.replace(/_/g, ' '), tone: 'neutral' }
}

const DISABLED_REASON: Record<string, string> = {
  owner: 'Switched off by you',
  failing: 'Switched off after ten failures in a row',
  robots_disallowed: "Switched off: the site's robots.txt forbids it",
  needs_token: 'Off: needs an access token this app does not hold',
  search_only: 'Read by agents through web search',
  needs_values: 'Off: needs a value from you (a handle, publication or repository)',
  terms: 'Off: the site terms restrict automated use',
  unreachable: 'Off: it could not be reached when the list was checked',
  suggestion: 'Suggested by an agent; off until you enable it',
}

export function disabledReason(reason: string | null | undefined): string | null {
  if (!reason) return null
  return DISABLED_REASON[reason] ?? reason.replace(/_/g, ' ')
}

/** How a source is read, as one honest sentence. */
export function readVia(source: Pick<RadarSource, 'read_via' | 'access_method'>): string {
  if (source.read_via === 'web_search') return 'Read by agents through web search, not fetched'
  switch (source.access_method) {
    case 'rss':
      return 'RSS feed'
    case 'atom':
      return 'Atom feed'
    case 'json_api':
      return 'JSON API'
    case 'json_api_free_token':
      return 'JSON API (free token)'
    default:
      return source.access_method.replace(/_/g, ' ')
  }
}

export function intervalLabel(seconds: number): string {
  if (seconds % 86_400 === 0) return seconds === 86_400 ? 'daily' : `every ${seconds / 86_400} days`
  if (seconds % 3_600 === 0) return seconds === 3_600 ? 'hourly' : `every ${seconds / 3_600} h`
  return `every ${Math.round(seconds / 60)} min`
}

/** Points and comments when the source gave them; any other engagement field is ignored (it could be anything). */
export function engagementText(engagement: Record<string, unknown> | null | undefined): string | null {
  if (!engagement) return null
  const parts: string[] = []
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.round(v) : null)
  const points = num(engagement.points)
  const comments = num(engagement.comments)
  if (points !== null) parts.push(`${points.toLocaleString('en-US')} ${points === 1 ? 'point' : 'points'}`)
  if (comments !== null) parts.push(`${comments.toLocaleString('en-US')} ${comments === 1 ? 'comment' : 'comments'}`)
  return parts.length ? parts.join(' · ') : null
}

/** The owner may decide an open idea; a rejected one can be reopened. Viewers never see either. */
export function ideaActions(idea: Pick<RadarIdea, 'status'>, canDecide: boolean): Array<'accept' | 'reject' | 'reopen'> {
  if (!canDecide) return []
  if (idea.status === 'open') return ['accept', 'reject']
  if (idea.status === 'rejected') return ['reopen']
  return []
}

/** Priority (about 1 to 100) -> a short word. `priority_score` is `impact * confidence * 4 / effort`. */
export function priorityLabel(score: number): string {
  if (score >= 40) return 'Very high'
  if (score >= 20) return 'High'
  if (score >= 10) return 'Medium'
  return 'Low'
}

/** Total of the evidence an idea cites, for a one-line summary. */
export function evidenceCount(e: RadarIdea['evidence']): number {
  return e.item_ids.length + e.artifact_ids.length + e.task_ids.length + e.urls.length
}
