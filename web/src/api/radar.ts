import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { Page } from './types'
import { isNotDeployed } from './work'

/**
 * M16 market radar (docs/specs/m16-self-critique-and-market-radar.md). Hand-written mirror of the owner half of
 * `backend/app/schemas/radar.py` (the agent API is not used by the browser). Everything in an item, brief or idea that
 * came from the open internet is untrusted plain text: the UI renders it as text, never as markup, and links to it
 * open with `rel="noopener noreferrer nofollow"`.
 */

const co = (cid: string | undefined) => `/companies/${cid}`
const radar = (cid: string | undefined) => `${co(cid)}/radar`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/* ---------------------------------- types ---------------------------------- */

export type SourceCategory = 'social' | 'forum' | 'news' | 'blog' | 'launch site' | 'review site' | 'UI inspiration' | 'research' | 'changelog'
export type SourceOrigin = 'seed' | 'owner' | 'agent'
export type QueryMode = 'none' | 'per_keyword'
export type KeywordKind = 'product' | 'competitor' | 'topic' | 'negative'
export type KeywordOrigin = 'derived' | 'owner' | 'agent'
export type ItemStatus = 'new' | 'seen' | 'used' | 'dismissed'
export type BriefKind = 'daily' | 'self_critique' | 'ui_inspiration'
export type ThemeKind = 'critique' | 'opportunity' | 'competitor' | 'ui' | 'risk'
export type IdeaKind = 'fix' | 'feature' | 'ui' | 'positioning' | 'research'
export type IdeaOrigin = 'radar' | 'self_critique' | 'ui_inspiration' | 'owner'
export type IdeaStatus = 'open' | 'accepted' | 'rejected' | 'merged' | 'done' | 'expired'

export interface RadarCategoryStat {
  category: string
  sources: number
  enabled: number
  failing: number
  items: number
}

export interface RadarFailingSource {
  id: string
  name: string
  category: string
  last_status: string | null
  last_error: string | null
  consecutive_failures: number
  disabled_reason: string | null
}

export interface RadarItemCounts {
  new: number
  seen: number
  used: number
  dismissed: number
  flagged: number
}

/** `GET /radar`. `available: false` is data (a subsystem is missing), not an error. */
export interface RadarStatus {
  available: boolean
  milestone?: string | null
  /** policy.radar_enabled, the master switch. */
  enabled: boolean
  self_critique_enabled: boolean
  sources_total: number
  sources_enabled: number
  sources_fetched: number
  sources_search_only: number
  categories: RadarCategoryStat[]
  items: RadarItemCounts
  open_ideas: number
  ideas_accepted_this_week: number
  keywords_enabled: number
  last_fetched_at: string | null
  last_brief_at: string | null
  failing_sources: RadarFailingSource[]
  runner_agent_id: string | null
  runner_agent_name: string | null
  runner_role: 'market_researcher' | 'cofounder' | 'none'
  search_queries_per_day: number
}

export interface RadarSource {
  id: string
  slug: string
  name: string
  url: string
  category: string
  access_method: string
  adapter: string
  fetch_url: string | null
  search_template: string | null
  query_mode: string
  keep_unmatched: boolean
  enabled: boolean
  origin: SourceOrigin
  edited_by_owner: boolean
  interval_seconds: number
  tos_note: string | null
  rate_note: string | null
  last_fetched_at: string | null
  next_fetch_at: string | null
  last_status: string | null
  last_error: string | null
  robots_override: boolean
  template_values: Record<string, unknown>
  consecutive_failures: number
  disabled_reason: string | null
  items_total: number
  created_at: string
  updated_at: string
  read_via: 'fetch' | 'web_search'
}

export interface RadarSourceCreate {
  name: string
  url: string
  fetch_url: string
  category?: SourceCategory
  adapter?: string
  interval_seconds?: number
  query_mode?: QueryMode
  keep_unmatched?: boolean
  template_values?: Record<string, string>
  robots_override?: boolean
}

export interface RadarSourceUpdate {
  name?: string
  url?: string
  category?: SourceCategory
  fetch_url?: string
  search_template?: string
  query_mode?: QueryMode
  keep_unmatched?: boolean
  enabled?: boolean
  interval_seconds?: number
  template_values?: Record<string, string>
  robots_override?: boolean
}

export interface RadarProbeItem {
  title: string
  url: string
  excerpt?: string | null
  published_at?: string | null
  kind?: string | null
}

/** `POST /radar/sources/{id}/test`: one fetch, nothing stored. */
export interface RadarProbeResult {
  source_id: string | null
  url: string
  robots: string
  http_status: number | null
  status: string
  items: RadarProbeItem[]
  requests: number
  error: string | null
}

export interface RadarFetchNowResult {
  enqueued: boolean
  job_id?: string | null
  message?: string | null
}

export interface RadarKeyword {
  id: string
  term: string
  kind: KeywordKind
  origin: KeywordOrigin
  weight: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface RadarKeywordCreate {
  term: string
  kind?: KeywordKind
  weight?: number
}

export interface RadarKeywordUpdate {
  term?: string
  kind?: KeywordKind
  weight?: number
  enabled?: boolean
}

/** Plain text read from the open internet: untrusted. */
export interface RadarItem {
  id: string
  source_id: string | null
  source_slug: string | null
  source_name: string | null
  category: string | null
  url: string
  title: string
  excerpt: string
  author: string | null
  published_at: string | null
  fetched_at: string
  kind: string
  image_url: string | null
  engagement: Record<string, unknown>
  also_seen_on: string[]
  matched_keywords: string[]
  match_score: number
  /** The text contains instruction-like phrases: show a "flagged text" badge. */
  flagged: boolean
  injection_flags: string[]
  status: ItemStatus
  relevance: number | null
}

export interface RadarItemFilters {
  source?: string
  category?: string
  status?: ItemStatus
  q?: string
  flagged?: boolean
}

export interface BriefTheme {
  title: string
  kind: ThemeKind
  item_ids: string[]
  why?: string | null
}

export interface RadarBrief {
  id: string
  kind: BriefKind
  period_start: string
  period_end: string
  author_agent_id: string | null
  author_name: string | null
  task_id: string | null
  artifact_id: string | null
  /** Written by an agent from outside text: untrusted. */
  summary: string
  themes: BriefTheme[]
  item_count: number
  ideas_created: number
  created_at: string
}

export interface RadarBriefDetail extends RadarBrief {
  items: RadarItem[]
  ideas: RadarIdea[]
}

export interface IdeaEvidence {
  item_ids: string[]
  artifact_ids: string[]
  task_ids: string[]
  urls: string[]
}

export interface RadarIdea {
  id: string
  /** title, problem and proposal are written by agents from outside text: untrusted. */
  title: string
  problem: string
  proposal: string
  kind: IdeaKind
  origin: IdeaOrigin
  evidence: IdeaEvidence
  impact: number
  effort: number
  confidence: number
  priority_score: number
  status: IdeaStatus
  decided_by_type: string | null
  decided_by_id: string | null
  decided_by_name?: string | null
  decision_note: string | null
  decided_at: string | null
  task_id: string | null
  task_title?: string | null
  task_status?: string | null
  project_id: string | null
  project_name?: string | null
  goal_id: string | null
  goal_title?: string | null
  merged_into_id: string | null
  created_by_agent_id: string | null
  created_by_name?: string | null
  age_days: number
  created_at: string
  updated_at: string
}

export type IdeaDecision =
  | { action: 'accept'; project_id: string; note?: string }
  | { action: 'reject'; note?: string }
  | { action: 'reopen'; note?: string }
  | { action: 'merge'; merge_into_id: string; note?: string }

/** The radar part of the company policy (all optional on the wire: a backend without M16 sends none of it). */
export interface RadarPolicyFields {
  radar_enabled?: boolean
  radar_max_fetches_per_day?: number
  radar_max_items_per_day?: number
  radar_max_ideas_per_day?: number
  radar_max_open_ideas?: number
  radar_auto_accept?: boolean
  radar_max_goals_per_week?: number
  radar_agent_sources?: boolean
  self_critique_enabled?: boolean
  radar_contact_email?: string | null
}

/* ---------------------------------- reads ---------------------------------- */

export function useRadarStatus(cid: string | undefined) {
  return useQuery({
    queryKey: qk.radarStatus(cid ?? ''),
    enabled: !!cid,
    retry,
    queryFn: ({ signal }) => apiFetch<RadarStatus>(radar(cid), { signal }),
  })
}

export function useRadarBriefs(cid: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.radarBriefs(cid ?? ''),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => fetchAllPages<RadarBrief>(`${radar(cid)}/briefs`, {}, signal, 3),
  })
}

export function useRadarBrief(cid: string | undefined, id: string | null | undefined) {
  return useQuery({
    queryKey: qk.radarBrief(cid ?? '', id ?? ''),
    enabled: !!cid && !!id,
    retry,
    queryFn: ({ signal }) => apiFetch<RadarBriefDetail>(`${radar(cid)}/briefs/${id}`, { signal }),
  })
}

export function useRadarIdeas(cid: string | undefined, status: IdeaStatus | 'all' = 'all', enabled = true) {
  return useQuery({
    queryKey: qk.radarIdeas(cid ?? '', status),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => fetchAllPages<RadarIdea>(`${radar(cid)}/ideas`, { status: status === 'all' ? undefined : status }, signal, 3),
  })
}

/** The newest 200 items that match the filters (the feed is a window on the day, not an archive). */
export function useRadarItems(cid: string | undefined, filters: RadarItemFilters = {}, enabled = true) {
  return useQuery({
    queryKey: qk.radarItems(cid ?? '', filters),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) =>
      fetchAllPages<RadarItem>(`${radar(cid)}/items`, { source: filters.source, category: filters.category, status: filters.status, q: filters.q, flagged: filters.flagged }, signal, 1),
  })
}

export function useRadarSources(cid: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.radarSources(cid ?? ''),
    enabled: !!cid && enabled,
    retry,
    queryFn: ({ signal }) => fetchAllPages<RadarSource>(`${radar(cid)}/sources`, {}, signal, 3),
  })
}

export function useRadarKeywords(cid: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.radarKeywords(cid ?? ''),
    enabled: !!cid && enabled,
    retry,
    queryFn: async ({ signal }) => (await apiFetch<Page<RadarKeyword>>(`${radar(cid)}/keywords`, { query: { limit: 200 }, signal })).items,
  })
}

/* -------------------------------------- mutations -------------------------------------- */

function useRefreshRadar(cid: string) {
  const qc = useQueryClient()
  return () => Promise.all([qc.invalidateQueries({ queryKey: qk.radarAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })])
}

/** Accepting an idea creates a task (and maybe a project), so those views refresh as well. */
function useRefreshWork(cid: string) {
  const qc = useQueryClient()
  const refreshRadar = useRefreshRadar(cid)
  return () =>
    Promise.all([refreshRadar(), qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }), qc.invalidateQueries({ queryKey: qk.projects(cid) }), qc.invalidateQueries({ queryKey: qk.goals(cid) })])
}

export function useCreateRadarSource(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: (body: RadarSourceCreate) => apiFetch<RadarSource>(`${radar(cid)}/sources`, { method: 'POST', body }),
    onSuccess: refresh,
  })
}

export function useUpdateRadarSource(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: ({ id, ...body }: RadarSourceUpdate & { id: string }) => apiFetch<RadarSource>(`${radar(cid)}/sources/${id}`, { method: 'PATCH', body }),
    onSuccess: refresh,
  })
}

export function useDeleteRadarSource(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`${radar(cid)}/sources/${id}`, { method: 'DELETE' }),
    onSuccess: refresh,
  })
}

/** One test fetch of a source. Nothing is stored, so no cache is touched; `gcTime: 0` keeps nothing about the call. */
export function useTestRadarSource(cid: string) {
  return useMutation({
    gcTime: 0,
    mutationFn: (id: string) => apiFetch<RadarProbeResult>(`${radar(cid)}/sources/${id}/test`, { method: 'POST' }),
  })
}

/** Restore sources that a newer seed list added, without overwriting any the owner edited. */
export function useReseedRadarSources(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: () => apiFetch<{ counts: Record<string, number> }>(`${radar(cid)}/sources/reseed`, { method: 'POST' }),
    onSuccess: refresh,
  })
}

export function useFetchRadarNow(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: () => apiFetch<RadarFetchNowResult>(`${radar(cid)}/fetch-now`, { method: 'POST' }),
    onSuccess: refresh,
  })
}

export function useCreateRadarKeyword(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: (body: RadarKeywordCreate) => apiFetch<RadarKeyword>(`${radar(cid)}/keywords`, { method: 'POST', body }),
    onSuccess: refresh,
  })
}

export function useUpdateRadarKeyword(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: ({ id, ...body }: RadarKeywordUpdate & { id: string }) => apiFetch<RadarKeyword>(`${radar(cid)}/keywords/${id}`, { method: 'PATCH', body }),
    onSuccess: refresh,
  })
}

export function useDeleteRadarKeyword(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`${radar(cid)}/keywords/${id}`, { method: 'DELETE' }),
    onSuccess: refresh,
  })
}

/** Derive the keyword list again from the company profile and goals (never edits or removes the owner's). */
export function useDeriveRadarKeywords(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: () => apiFetch<unknown>(`${radar(cid)}/keywords/derive`, { method: 'POST' }),
    onSuccess: refresh,
  })
}

export function useUpdateRadarItem(cid: string) {
  const refresh = useRefreshRadar(cid)
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'dismissed' | 'new' }) => apiFetch<RadarItem>(`${radar(cid)}/items/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: refresh,
  })
}

/** Owner decision on an idea: accept into a project, reject, merge into another, or reopen a rejected one. */
export function useDecideRadarIdea(cid: string) {
  const refresh = useRefreshWork(cid)
  return useMutation({
    mutationFn: ({ id, ...body }: IdeaDecision & { id: string }) => apiFetch<RadarIdea>(`${radar(cid)}/ideas/${id}`, { method: 'PATCH', body }),
    onSuccess: refresh,
  })
}
