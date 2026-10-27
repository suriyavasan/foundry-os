/**
 * URL state for the canvas drawer: `?panel=agent:<id>`, `?panel=tasks`, ...
 * Pure parse/serialize so deep links and the back button work.
 */

export const SIMPLE_PANELS = [
  'org',
  'tasks',
  'activity',
  'artifacts',
  'approvals',
  'finance',
  'settings',
  'integrations',
  'goals',
  'routines',
  'skills',
  'notes',
  'memory',
  'cofounder',
  'guardrails',
  'account',
  'team',
  'my-work',
  'reachability',
  'ai',
  'reflections',
  'standards',
  'command-center',
  'repos',
] as const
export type SimplePanel = (typeof SIMPLE_PANELS)[number]

export const ID_PANELS = ['agent', 'task', 'artifact', 'skill', 'note', 'reflection', 'repo'] as const
export type IdPanel = (typeof ID_PANELS)[number]

export type Panel =
  | { kind: SimplePanel }
  | { kind: 'agent'; id: string }
  | { kind: 'task'; id: string; run?: string }
  | { kind: 'artifact'; id: string }
  | { kind: 'skill'; id: string }
  | { kind: 'note'; id: string }
  | { kind: 'reflection'; id: string }
  | { kind: 'repo'; id: string }

export const ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export const PANEL_PARAM = 'panel'
export const RUN_PARAM = 'run'

function toParams(search: string | URLSearchParams): URLSearchParams {
  return search instanceof URLSearchParams ? search : new URLSearchParams(search)
}

export function parsePanel(search: string | URLSearchParams): Panel | null {
  const params = toParams(search)
  const raw = params.get(PANEL_PARAM)
  if (!raw) return null
  const colon = raw.indexOf(':')
  if (colon === -1) {
    return (SIMPLE_PANELS as readonly string[]).includes(raw) ? ({ kind: raw } as Panel) : null
  }
  const kind = raw.slice(0, colon)
  const id = raw.slice(colon + 1)
  if (!(ID_PANELS as readonly string[]).includes(kind) || !ID_RE.test(id)) return null
  if (kind === 'task') {
    const run = params.get(RUN_PARAM)
    return run && ID_RE.test(run) ? { kind: 'task', id, run } : { kind: 'task', id }
  }
  return { kind: kind as 'agent' | 'artifact' | 'skill' | 'note' | 'reflection' | 'repo', id }
}

/** Value of the `panel` query parameter. */
export function serializePanel(p: Panel): string {
  return 'id' in p ? `${p.kind}:${p.id}` : p.kind
}

/**
 * New query string (with leading `?`, or empty) that shows `panel` (or closes the drawer when null),
 * keeping unrelated parameters.
 */
export function withPanel(search: string | URLSearchParams, panel: Panel | null): string {
  const params = new URLSearchParams(toParams(search))
  params.delete(PANEL_PARAM)
  params.delete(RUN_PARAM)
  if (panel) {
    params.set(PANEL_PARAM, serializePanel(panel))
    if (panel.kind === 'task' && panel.run) params.set(RUN_PARAM, panel.run)
  }
  // ':' is legal in a query string; keep the URL readable (?panel=agent:<id>).
  const qs = params.toString().replace(/%3A/g, ':')
  return qs ? `?${qs}` : ''
}

export function samePanel(a: Panel | null, b: Panel | null): boolean {
  if (!a || !b) return a === b
  return serializePanel(a) === serializePanel(b) && (a.kind !== 'task' || b.kind !== 'task' || a.run === b.run)
}

export const PANEL_TITLE: Record<SimplePanel, string> = {
  org: 'Organization',
  tasks: 'Tasks',
  activity: 'Activity',
  artifacts: 'Artifacts',
  approvals: 'Approvals',
  finance: 'Finance',
  settings: 'Settings',
  integrations: 'Integrations',
  goals: 'Goals & plan',
  routines: 'Routines',
  skills: 'Skills',
  notes: 'Notes',
  memory: 'Memory',
  cofounder: 'AI Co-founder',
  guardrails: 'Guardrails',
  account: 'Account',
  team: 'Team',
  'my-work': 'My work',
  reachability: 'Reachability',
  ai: 'AI settings',
  reflections: 'Reflections',
  standards: 'Standards',
  'command-center': 'Command Center',
  repos: 'Repositories',
}

const LEGACY_AREAS: Record<string, SimplePanel | null> = {
  'command-center': null,
  organization: 'org',
  projects: 'goals',
  tasks: 'tasks',
  artifacts: 'artifacts',
  activity: 'activity',
  finance: 'finance',
  approvals: 'approvals',
  integrations: 'integrations',
  settings: 'settings',
  skills: 'skills',
}

/**
 * Old sidebar URLs (`/c/:id/<area>[/agents/:agentId]`) -> the panel that replaces them.
 * Returns `undefined` for an unknown area (render 404) and `null` for "the canvas, no panel".
 */
export function legacyPanelFor(rest: string): Panel | null | undefined {
  const segs = rest.split('/').filter(Boolean)
  const area = segs[0]
  if (!area) return null
  if (!(area in LEGACY_AREAS)) return undefined
  if (area === 'organization' && segs[1] === 'agents' && segs[2] && ID_RE.test(segs[2])) {
    return { kind: 'agent', id: segs[2] }
  }
  const simple = LEGACY_AREAS[area]
  return simple ? { kind: simple } : null
}
