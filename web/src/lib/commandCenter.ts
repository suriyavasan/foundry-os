import type { CommandExchange, CommandOutput, CommandOutputKind, CommandStatus } from '@/api/types'
import { actionChips, type ActionChip, type RefTitles } from './notes'
import { parsePanel, type Panel } from './panels'

/**
 * Command Center (docs/specs/m10 §5): pure helpers for parsing what was typed, the slash-command autocomplete, the
 * history, and what an exchange should look like in each status. Every input must visibly get an output, so every status
 * (including a missing output) maps to something to show.
 */

/* ------------------------------------ commands ------------------------------------ */

export interface SlashCommandDef {
  name: string
  /** What to type after the name, for the autocomplete (`<title> [@agent]`). Empty when the command takes nothing. */
  args: string
  description: string
  /** Changes company state (as the human, with the human's permissions). Shown so nobody is surprised. */
  changes: boolean
}

/** The deterministic commands of spec §5. The server executes them; this list drives help and autocomplete. */
export const SLASH_COMMANDS: SlashCommandDef[] = [
  { name: 'help', args: '', description: 'List every command', changes: false },
  { name: 'status', args: '', description: 'Company status, spend and what is running', changes: false },
  { name: 'goals', args: '', description: 'Active goals and their progress', changes: false },
  { name: 'agents', args: '', description: 'Everyone on the team and what they are doing', changes: false },
  { name: 'tasks', args: '[status]', description: 'Tasks, optionally only one status', changes: false },
  { name: 'spend', args: '[day|month]', description: 'What the company has spent', changes: false },
  { name: 'approvals', args: '', description: 'Approvals waiting for a decision', changes: false },
  { name: 'standards', args: '', description: 'The company standards agents follow', changes: false },
  { name: 'pause', args: '', description: 'Pause the company: no new work is dispatched', changes: true },
  { name: 'resume', args: '', description: 'Resume a paused company', changes: true },
  { name: 'goal', args: '<text>', description: 'Create a goal', changes: true },
  { name: 'task', args: '<title> [@agent]', description: 'Create a task, optionally assigned to an agent', changes: true },
  { name: 'hire', args: '<template> [reason]', description: 'Ask HR to hire someone from a role template', changes: true },
  { name: 'approve', args: '<id>', description: 'Approve a pending approval', changes: true },
  { name: 'reject', args: '<id> [note]', description: 'Reject a pending approval', changes: true },
]

const COMMAND_NAMES = new Set(SLASH_COMMANDS.map((c) => c.name))

export type ParsedInput =
  | { kind: 'empty' }
  | { kind: 'text'; text: string }
  | { kind: 'command'; name: string; args: string; known: boolean }

/** `/tasks blocked` -> command; anything else non-empty is free text for the AI Co-founder. */
export function parseInput(raw: string): ParsedInput {
  const text = raw.trim()
  if (!text) return { kind: 'empty' }
  const m = /^\/([A-Za-z][\w-]*)(?:\s+([\s\S]*))?$/.exec(text)
  if (!m) return { kind: 'text', text }
  const name = m[1]!.toLowerCase()
  return { kind: 'command', name, args: (m[2] ?? '').trim(), known: COMMAND_NAMES.has(name) }
}

export function usageOf(def: SlashCommandDef): string {
  return `/${def.name}${def.args ? ` ${def.args}` : ''}`
}

/**
 * Autocomplete: while a `/name` is being typed (no space yet), the commands that start with it, best first. Typing a
 * space or a non-slash character closes it.
 */
export function matchCommands(input: string, limit = 8): SlashCommandDef[] {
  const m = /^\/([A-Za-z-]*)$/.exec(input)
  if (!m) return []
  const q = m[1]!.toLowerCase()
  const starts = SLASH_COMMANDS.filter((c) => c.name.startsWith(q))
  if (starts.length > 0 || !q) return starts.slice(0, limit)
  // Nothing starts with it: fall back to a looser match so a half-remembered name still finds something.
  return SLASH_COMMANDS.filter((c) => c.name.includes(q) || c.description.toLowerCase().includes(q)).slice(0, limit)
}

/** What accepting a suggestion puts in the box: the name, plus a space when the command takes arguments. */
export function applySuggestion(def: SlashCommandDef): string {
  return `/${def.name}${def.args ? ' ' : ''}`
}

/** Whether Enter should accept the highlighted suggestion instead of sending (the typed text is not yet that command). */
export function enterAcceptsSuggestion(input: string, suggestion: SlashCommandDef | undefined): boolean {
  if (!suggestion) return false
  return input.trim().toLowerCase() !== `/${suggestion.name}`
}

/** Plain Enter sends a single-line command; free text keeps ⌘/Ctrl+Enter so a thought can have several lines. */
export function enterSends(input: string): boolean {
  return parseInput(input).kind === 'command' && !input.includes('\n')
}

/* ------------------------------------- history ------------------------------------- */

export const HISTORY_MAX = 50

/** Oldest first. Empty input and an immediate repeat are not stored. */
export function pushHistory(items: string[], input: string, max = HISTORY_MAX): string[] {
  const t = input.trim()
  if (!t || items[items.length - 1] === t) return items
  return [...items, t].slice(-max)
}

export interface HistoryCursor {
  /** Index into the history, or null while the person is on their own draft. */
  index: number | null
  /** What was in the box before they started browsing, restored when they come back down. */
  draft: string
}

export const HISTORY_START: HistoryCursor = { index: null, draft: '' }

/** ↑ goes to older entries, ↓ to newer and finally back to the draft. */
export function stepHistory(items: string[], cursor: HistoryCursor, dir: 'up' | 'down', current: string): { cursor: HistoryCursor; value: string } {
  if (items.length === 0) return { cursor, value: current }
  if (dir === 'up') {
    if (cursor.index === null) return { cursor: { index: items.length - 1, draft: current }, value: items[items.length - 1]! }
    const i = Math.max(0, cursor.index - 1)
    return { cursor: { ...cursor, index: i }, value: items[i]! }
  }
  if (cursor.index === null) return { cursor, value: current }
  if (cursor.index >= items.length - 1) return { cursor: HISTORY_START, value: cursor.draft }
  const i = cursor.index + 1
  return { cursor: { ...cursor, index: i }, value: items[i]! }
}

/** ↑/↓ browse history only from the first/last line, so moving inside a multi-line thought still works. */
export function historyKeyApplies(dir: 'up' | 'down', value: string, caret: number): boolean {
  if (value === '') return true
  return dir === 'up' ? !value.slice(0, caret).includes('\n') : !value.slice(caret).includes('\n')
}

/* ------------------------------------- exchanges ------------------------------------ */

export const ACTIVE_STATUSES: CommandStatus[] = ['pending', 'processing']
export const isActiveExchange = (e: Pick<CommandExchange, 'status'>): boolean => ACTIVE_STATUSES.includes(e.status)

/** The server writes an explanation and retries once after 15 minutes without an answer (spec §5). */
export const STALE_AFTER_MS = 15 * 60 * 1000

export const STATUS_LABEL: Record<CommandStatus, string> = {
  done: 'Done',
  pending: 'Waiting',
  processing: 'Working',
  answered: 'Answered',
  failed: 'Failed',
}

export type ExchangePhase = 'output' | 'waiting' | 'working' | 'answered' | 'failed'

export interface ExchangeView {
  phase: ExchangePhase
  /** Still unanswered after the time the system itself waits. The card says so and offers a retry. */
  stale: boolean
  /** Whether a manual retry is offered. */
  canRetry: boolean
  /** Replacement text when the server has no output yet (or none at all). */
  placeholder: string | null
}

export function exchangeView(ex: Pick<CommandExchange, 'status' | 'output' | 'created_at' | 'input_kind'>, now: number = Date.now()): ExchangeView {
  const age = now - new Date(ex.created_at).getTime()
  const stale = isActiveExchange(ex) && Number.isFinite(age) && age > STALE_AFTER_MS
  switch (ex.status) {
    case 'done':
      return { phase: 'output', stale: false, canRetry: false, placeholder: ex.output ? null : 'The command ran but returned nothing to show.' }
    case 'pending':
      return { phase: 'waiting', stale, canRetry: stale, placeholder: 'Sent. The AI Co-founder will pick this up in a few seconds.' }
    case 'processing':
      return { phase: 'working', stale, canRetry: stale, placeholder: 'The AI Co-founder is working on it.' }
    case 'answered':
      return { phase: 'answered', stale: false, canRetry: false, placeholder: ex.output ? null : 'The AI Co-founder handled this but wrote no reply. Open the note to see what changed.' }
    case 'failed':
      return {
        phase: 'failed',
        stale: false,
        canRetry: true,
        placeholder: ex.output ? null : 'This did not get an answer, and the system could not say why. Try again; if it keeps failing, check the AI Co-founder panel.',
      }
  }
}

/** The tone of the status chip. */
export function statusTone(status: CommandStatus): 'success' | 'info' | 'accent' | 'danger' | 'neutral' {
  return status === 'done' || status === 'answered' ? 'success' : status === 'processing' ? 'accent' : status === 'pending' ? 'info' : 'danger'
}

/** Action chips of an exchange: the records the AI Co-founder created or changed while answering. */
export function exchangeChips(ex: Pick<CommandExchange, 'actions' | 'note_id'>, titles: RefTitles = {}): ActionChip[] {
  return actionChips((ex.actions ?? []).map((a) => ({ type: a.type, ref_id: a.ref_id ?? '', label: a.label ?? undefined })), titles)
}

/* --------------------------------------- output ------------------------------------- */

export interface NormalizedOutput {
  kind: CommandOutputKind
  title: string | null
  text: string | null
  columns: string[]
  rows: string[][]
  links: Array<{ label: string; panel: Panel }>
}

const cell = (v: unknown): string => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** Rows may be arrays, objects keyed by column, or plain strings (lists). Cells become strings; nothing is dropped silently. */
export function normalizeCommandOutput(out: CommandOutput | null | undefined): NormalizedOutput | null {
  if (!out) return null
  const kind: CommandOutputKind = out.kind === 'table' || out.kind === 'list' || out.kind === 'error' ? out.kind : 'text'
  const rawRows = Array.isArray(out.rows) ? out.rows : []
  let columns = Array.isArray(out.columns) ? out.columns.map(cell) : []
  const rows = rawRows.map((r) => {
    if (Array.isArray(r)) return r.map(cell)
    if (r && typeof r === 'object') {
      if (columns.length === 0) columns = Object.keys(r)
      return columns.map((c) => cell((r as Record<string, unknown>)[c]))
    }
    return [cell(r)]
  })
  const links: NormalizedOutput['links'] = []
  for (const l of out.links ?? []) {
    const panel = typeof l?.panel === 'string' ? panelFromString(l.panel) : null
    if (panel && l.label) links.push({ label: l.label, panel })
  }
  return { kind, title: out.title?.trim() || null, text: out.text?.trim() || null, columns, rows, links }
}

/** `task:abc` / `tasks` as written in a command link -> a Panel, or null for something the UI cannot open. */
export function panelFromString(s: string): Panel | null {
  const clean = s.replace(/^\?/, '').replace(/^panel=/, '')
  return parsePanel(new URLSearchParams({ panel: clean }))
}

/* ----------------------------------- local exchanges ---------------------------------- */

/**
 * An exchange that exists only in this browser: the request could not be delivered (network, 409 `ai_setup_required`).
 * It is shown like any failed exchange, with the reason, so even a refused input visibly gets an output.
 */
export interface LocalExchange extends CommandExchange {
  local: true
  error: string
}

export function localFailure(input: string, message: string, now: Date = new Date()): LocalExchange {
  const ts = now.toISOString()
  const kind = parseInput(input).kind === 'command' ? 'command' : 'note'
  return {
    id: `local-${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
    input,
    input_kind: kind,
    created_at: ts,
    updated_at: ts,
    status: 'failed',
    output: { kind: 'error', title: 'Not sent', text: message },
    note_id: null,
    run_id: null,
    actions: [],
    local: true,
    error: message,
  }
}

export const isLocalExchange = (e: CommandExchange): e is LocalExchange => (e as Partial<LocalExchange>).local === true

/** The shell's optimistic row while the request is in flight. */
export function optimisticExchange(input: string, now: Date = new Date()): CommandExchange {
  const ts = now.toISOString()
  const isCommand = parseInput(input).kind === 'command'
  return {
    id: `pending-${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
    input,
    input_kind: isCommand ? 'command' : 'note',
    created_at: ts,
    updated_at: ts,
    status: isCommand ? 'processing' : 'pending',
    output: null,
    note_id: null,
    run_id: null,
    actions: [],
  }
}

export const isOptimisticExchange = (e: Pick<CommandExchange, 'id'>): boolean => e.id.startsWith('pending-')

/** Commands that change company state: the records they touch are re-read when one completes. */
export function commandChangesState(input: string): boolean {
  const p = parseInput(input)
  return p.kind === 'command' && (SLASH_COMMANDS.find((c) => c.name === p.name)?.changes ?? false)
}
