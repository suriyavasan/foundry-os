import type { RadarBrief, RadarIdea, RadarItem, RadarKeyword, RadarSource, RadarStatus } from '@/api/radar'

const T = '2026-10-27T07:30:00Z'

export function makeRadarStatus(over: Partial<RadarStatus> = {}): RadarStatus {
  return {
    available: true,
    enabled: true,
    self_critique_enabled: true,
    sources_total: 100,
    sources_enabled: 90,
    sources_fetched: 57,
    sources_search_only: 33,
    categories: [],
    items: { new: 12, seen: 3, used: 1, dismissed: 0, flagged: 0 },
    open_ideas: 2,
    ideas_accepted_this_week: 0,
    keywords_enabled: 6,
    last_fetched_at: T,
    last_brief_at: T,
    failing_sources: [],
    runner_agent_id: 'a2',
    runner_agent_name: 'Iris',
    runner_role: 'market_researcher',
    search_queries_per_day: 20,
    ...over,
  }
}

export function makeRadarItem(over: Partial<RadarItem> = {}): RadarItem {
  return {
    id: 'i1',
    source_id: 's1',
    source_slug: 'hn',
    source_name: 'Hacker News',
    category: 'forum',
    url: 'https://news.example.com/item?id=1',
    title: 'Show HN: a rival product',
    excerpt: 'People are comparing it with ours.',
    author: 'pg',
    published_at: T,
    fetched_at: T,
    kind: 'story',
    image_url: null,
    engagement: { points: 120, comments: 33 },
    also_seen_on: [],
    matched_keywords: ['acme'],
    match_score: 9,
    flagged: false,
    injection_flags: [],
    status: 'new',
    relevance: null,
    ...over,
  }
}

export function makeRadarBrief(over: Partial<RadarBrief> = {}): RadarBrief {
  return {
    id: 'b1',
    kind: 'daily',
    period_start: '2026-10-26T07:30:00Z',
    period_end: T,
    author_agent_id: 'a2',
    author_name: 'Iris',
    task_id: 't1',
    artifact_id: 'art1',
    summary: 'People like the pricing page of a rival.',
    themes: [{ title: 'Pricing is clearer elsewhere', kind: 'critique', item_ids: ['i1'], why: 'Three posts say so' }],
    item_count: 14,
    ideas_created: 1,
    created_at: T,
    ...over,
  }
}

export function makeRadarIdea(over: Partial<RadarIdea> = {}): RadarIdea {
  return {
    id: 'idea1',
    title: 'Simplify the pricing page',
    problem: 'Visitors cannot tell the plans apart.',
    proposal: 'Show three plans in one table.',
    kind: 'ui',
    origin: 'radar',
    evidence: { item_ids: ['i1'], artifact_ids: [], task_ids: [], urls: ['https://example.com/post'] },
    impact: 4,
    effort: 2,
    confidence: 3,
    priority_score: 24,
    status: 'open',
    decided_by_type: null,
    decided_by_id: null,
    decision_note: null,
    decided_at: null,
    task_id: null,
    project_id: null,
    goal_id: null,
    merged_into_id: null,
    created_by_agent_id: 'a2',
    created_by_name: 'Iris',
    age_days: 1,
    created_at: T,
    updated_at: T,
    ...over,
  }
}

export function makeRadarSource(over: Partial<RadarSource> = {}): RadarSource {
  return {
    id: 's1',
    slug: 'hn',
    name: 'Hacker News',
    url: 'https://news.ycombinator.com',
    category: 'forum',
    access_method: 'json_api',
    adapter: 'hn_algolia',
    fetch_url: 'https://hn.algolia.com/api/v1/search?query={query}',
    search_template: null,
    query_mode: 'per_keyword',
    keep_unmatched: false,
    enabled: true,
    origin: 'seed',
    edited_by_owner: false,
    interval_seconds: 21_600,
    tos_note: null,
    rate_note: null,
    last_fetched_at: T,
    next_fetch_at: null,
    last_status: 'ok',
    last_error: null,
    robots_override: false,
    template_values: {},
    consecutive_failures: 0,
    disabled_reason: null,
    items_total: 41,
    created_at: T,
    updated_at: T,
    read_via: 'fetch',
    ...over,
  }
}

export function makeRadarKeyword(over: Partial<RadarKeyword> = {}): RadarKeyword {
  return { id: 'k1', term: 'acme', kind: 'product', origin: 'derived', weight: 3, enabled: true, created_at: T, updated_at: T, ...over }
}
