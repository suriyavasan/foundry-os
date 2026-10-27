import type { Agent, Task } from '@/api/types'
import { PANEL_TITLE, SIMPLE_PANELS, type Panel } from './panels'
import { AI_COFOUNDER_LABEL } from './terms'

export type CommandGroup = 'Actions' | 'Panels' | 'Agents' | 'Tasks'

export type CommandAction =
  | { type: 'panel'; panel: Panel }
  | { type: 'create'; what: 'task' | 'goal' | 'project' | 'hire' }
  | { type: 'company'; do: 'pause' | 'resume' }
  | { type: 'theme' }
  | { type: 'focus-command-center' }
  | { type: 'wake-cofounder' }
  | { type: 'emergency-stop' }
  | { type: 'copy-public-url' }
  | { type: 'connect-account' }

export interface PaletteCommand {
  id: string
  group: CommandGroup
  label: string
  /** Secondary text (role, status...). Also searched. */
  hint?: string
  keywords?: string[]
  action: CommandAction
}

const GROUP_ORDER: CommandGroup[] = ['Actions', 'Panels', 'Agents', 'Tasks']

/** How many rows each group may show when the query is empty (keeps the palette scannable). */
const EMPTY_QUERY_CAP: Record<CommandGroup, number> = { Actions: 10, Panels: 16, Agents: 6, Tasks: 5 }

const tokens = (s: string) => s.toLowerCase().split(/[\s/:_-]+/).filter(Boolean)

/** Score one token against a command; 0 means no match. */
function scoreToken(token: string, cmd: PaletteCommand): number {
  const label = cmd.label.toLowerCase()
  if (label.startsWith(token)) return 6
  if (label.split(/[\s/:_-]+/).some((w) => w.startsWith(token))) return 4
  if (label.includes(token)) return 3
  const extra = [cmd.hint ?? '', ...(cmd.keywords ?? [])].join(' ').toLowerCase()
  if (extra.split(/[\s/:_-]+/).some((w) => w.startsWith(token))) return 2
  if (extra.includes(token)) return 1
  return 0
}

/** Every query token must match; ranking favours label prefixes, then word starts, then substrings. */
export function filterCommands(commands: PaletteCommand[], query: string): PaletteCommand[] {
  const q = tokens(query)
  const indexed = commands.map((c, i) => ({ c, i }))

  if (q.length === 0) {
    const counts: Partial<Record<CommandGroup, number>> = {}
    return indexed
      .sort((a, b) => GROUP_ORDER.indexOf(a.c.group) - GROUP_ORDER.indexOf(b.c.group) || a.i - b.i)
      .filter(({ c }) => {
        counts[c.group] = (counts[c.group] ?? 0) + 1
        return counts[c.group]! <= EMPTY_QUERY_CAP[c.group]
      })
      .map(({ c }) => c)
  }

  const scored: Array<{ c: PaletteCommand; i: number; score: number }> = []
  for (const { c, i } of indexed) {
    let total = 0
    let ok = true
    for (const t of q) {
      const s = scoreToken(t, c)
      if (s === 0) {
        ok = false
        break
      }
      total += s
    }
    if (ok) scored.push({ c, i, score: total })
  }
  return scored
    .sort((a, b) => b.score - a.score || GROUP_ORDER.indexOf(a.c.group) - GROUP_ORDER.indexOf(b.c.group) || a.i - b.i)
    .slice(0, 40)
    .map((s) => s.c)
}

export interface CommandContext {
  agents: Agent[]
  tasks: Task[]
  companyStatus: 'active' | 'paused'
  /** false when the work engine is not deployed: task/goal creation is hidden, not faked. */
  workEngine: boolean
  /** false when the M5 notes / AI Co-founder endpoints are not deployed: their actions are hidden, not faked. */
  autonomy?: boolean
  /** false when the M10 Command Center is not deployed: its action is hidden, not faked. */
  commandCenter?: boolean
  /** false when the M6 system endpoints are not deployed: the emergency stop is hidden, not faked. */
  guardrails?: boolean
  /** false when the M8 reachability endpoints are not deployed: its actions are hidden, not faked. */
  reachability?: boolean
  /** false when the M14 repository endpoints are not deployed: the panel entry is hidden, not faked. */
  repositories?: boolean
}

export function buildCommands(ctx: CommandContext): PaletteCommand[] {
  const out: PaletteCommand[] = []
  out.push({ id: 'act:hire', group: 'Actions', label: 'Propose a hire', keywords: ['new agent', 'employee', 'add'], action: { type: 'create', what: 'hire' } })
  if (ctx.workEngine) {
    out.push(
      { id: 'act:task', group: 'Actions', label: 'Create task', keywords: ['new', 'add', 'todo'], action: { type: 'create', what: 'task' } },
      { id: 'act:goal', group: 'Actions', label: 'Create goal', keywords: ['new', 'add', 'objective'], action: { type: 'create', what: 'goal' } },
      { id: 'act:project', group: 'Actions', label: 'Create project', keywords: ['new', 'add'], action: { type: 'create', what: 'project' } },
    )
  }
  if (ctx.commandCenter !== false) {
    out.push({
      id: 'act:focus-command-center',
      group: 'Actions',
      label: 'Focus command center',
      hint: `Commands and messages for the ${AI_COFOUNDER_LABEL}`,
      keywords: ['note', 'write', 'type', 'tell', 'message', 'thought', 'idea', 'slash', 'founder'],
      action: { type: 'focus-command-center' },
    })
  }
  if (ctx.autonomy !== false) {
    out.push(
      { id: 'act:wake', group: 'Actions', label: `Wake ${AI_COFOUNDER_LABEL}`, hint: 'Start a planning run', keywords: ['run', 'plan', 'now', 'founder'], action: { type: 'wake-cofounder' } },
    )
  }
  out.push(
    ctx.companyStatus === 'active'
      ? { id: 'act:pause', group: 'Actions', label: 'Pause company', hint: 'Kill switch', keywords: ['stop', 'halt', 'kill'], action: { type: 'company', do: 'pause' } }
      : { id: 'act:resume', group: 'Actions', label: 'Resume company', hint: 'Restart dispatch', keywords: ['start', 'unpause'], action: { type: 'company', do: 'resume' } },
    ...(ctx.guardrails !== false
      ? [{ id: 'act:emergency-stop', group: 'Actions' as const, label: 'Emergency stop\u2026', hint: 'Halts every company', keywords: ['kill', 'halt', 'panic', 'stop all', 'guardrails'], action: { type: 'emergency-stop' as const } }]
      : []),
    ...(ctx.reachability !== false
      ? [
          { id: 'act:copy-public-url', group: 'Actions' as const, label: 'Copy public URL', hint: 'Reachability', keywords: ['link', 'share', 'tunnel', 'domain', 'address'], action: { type: 'copy-public-url' as const } },
          { id: 'act:connect-account', group: 'Actions' as const, label: 'Connect an account\u2026', hint: 'Cloudflare, GitHub, Vercel, email', keywords: ['provider', 'token', 'cloudflare', 'github', 'vercel', 'agentmail', 'resend', 'integration'], action: { type: 'connect-account' as const } },
        ]
      : []),
    { id: 'act:theme', group: 'Actions', label: 'Toggle theme', hint: 'Dark / light', keywords: ['dark', 'light', 'appearance'], action: { type: 'theme' } },
  )
  for (const kind of SIMPLE_PANELS) {
    if (kind === 'repos' && ctx.repositories === false) continue
    out.push({ id: `panel:${kind}`, group: 'Panels', label: `Open ${PANEL_TITLE[kind]}`, keywords: [kind, 'go', 'show'], action: { type: 'panel', panel: { kind } } })
  }
  for (const a of ctx.agents) {
    if (a.status === 'retired' || a.status === 'rejected') continue
    out.push({ id: `agent:${a.id}`, group: 'Agents', label: a.name, hint: a.role_title, keywords: [a.status, a.kind], action: { type: 'panel', panel: { kind: 'agent', id: a.id } } })
  }
  for (const t of ctx.tasks) {
    if (t.status === 'done' || t.status === 'cancelled') continue
    out.push({ id: `task:${t.id}`, group: 'Tasks', label: t.title, hint: t.status.replace(/_/g, ' '), keywords: [t.priority], action: { type: 'panel', panel: { kind: 'task', id: t.id } } })
  }
  return out
}
