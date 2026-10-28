import type { ClaudeLoginStatus } from '@/api/types'
import { normalizeClaudeLogin } from './aiSetup'

/**
 * M17 "One AI stack": types, defensive readers and plain-language labels for `/ai-stack`.
 * The server decides routing; the UI only shows and edits the owner's choices.
 */

export type AiUseFor = 'everything' | 'heavy' | 'everyday'
export type AiConnState = 'ready' | 'needs_test' | 'test_failed' | 'needs_sign_in' | 'not_installed' | 'paused' | 'disabled'
export type AiGroup = 'plan' | 'api' | 'local'
export type AiStackBlocking = 'no_ai' | 'no_tested_ai' | 'no_heavy_ai' | 'no_everyday_ai'
export type AiOptionId = 'claude_plan' | 'anthropic_api' | 'openai_api' | 'openrouter' | 'openai_compatible' | 'ollama' | 'cursor_plan' | 'cursor_api' | 'codex_api' | 'opencode' | 'antigravity_api'
const OPTION_IDS: AiOptionId[] = ['claude_plan', 'anthropic_api', 'openai_api', 'openrouter', 'openai_compatible', 'ollama', 'cursor_plan', 'cursor_api', 'codex_api', 'opencode', 'antigravity_api']
export type AiAuthMode = 'plan' | 'api_key' | 'provider'

export interface AiLastTest {
  ok: boolean
  detail: string | null
  tested_at: string | null
  model: string | null
}

export interface AiConnectionStatus {
  state: AiConnState
  message: string | null
  last_test: AiLastTest | null
  signed_in: boolean | null
  account: string | null
  paused_until: string | null
}

export interface AiConnection {
  id: string
  position: number
  rank_label: string
  label: string
  engine: 'api' | 'cli'
  option: AiOptionId
  provider_kind: string | null
  model_provider_id: string | null
  model: string | null
  cli_id: string | null
  auth_mode: AiAuthMode | null
  credential_id: string | null
  cli_provider_id: string | null
  use_for: AiUseFor
  share: number
  enabled: boolean
  status: AiConnectionStatus
  runs_24h: number
  max_concurrency: number | null
  home_dir: string | null
  created_at: string | null
}

export interface AiOption {
  option: AiOptionId
  label: string
  group: AiGroup
  engine: 'api' | 'cli'
  cli_id: string | null
  provider_kind: string | null
  available: boolean
  reason: string | null
  needs_install: boolean
  install: { available: boolean; command: string | null } | null
  warning: string | null
}

export interface AiSummaryEntry {
  connection_id: string
  label: string
  percent: number
  backup: boolean
}

export interface AiStackData {
  items: AiConnection[]
  ready: boolean
  blocking_reason: AiStackBlocking | string | null
  summary: { heavy: AiSummaryEntry[]; everyday: AiSummaryEntry[] }
  options: AiOption[]
}

export interface AddAiBody {
  option: AiOptionId
  label?: string
  use_for?: AiUseFor
  share?: number
  model?: string | null
  model_provider_id?: string | null
  credential_id?: string | null
  cli_provider_id?: string | null
}

export interface PatchAiBody {
  label?: string
  use_for?: AiUseFor
  share?: number
  enabled?: boolean
  model?: string | null
}

/* ----------------------------------- labels ----------------------------------- */

export const USE_FOR_LABEL: Record<AiUseFor, string> = { everything: 'Everything', heavy: 'Heavy lifting', everyday: 'Everyday work' }
export const USE_FOR_ORDER: AiUseFor[] = ['everything', 'heavy', 'everyday']
export const SHARE_LABEL: Record<number, string> = { 4: 'Most', 3: 'More', 2: 'Normal', 1: 'Less', 0: 'Backup only' }
export const SHARE_ORDER = [4, 3, 2, 1, 0]

export const STATE_LABEL: Record<AiConnState, string> = {
  ready: 'Ready',
  needs_test: 'Needs a test',
  test_failed: 'Test failed',
  needs_sign_in: 'Needs sign-in',
  not_installed: 'Not installed',
  paused: 'Paused',
  disabled: 'Off',
}
export const STATE_TONE: Record<AiConnState, 'success' | 'warning' | 'danger' | 'neutral'> = {
  ready: 'success',
  needs_test: 'warning',
  test_failed: 'danger',
  needs_sign_in: 'warning',
  not_installed: 'warning',
  paused: 'warning',
  disabled: 'neutral',
}

const RANKS = ['Primary', 'Secondary', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh', 'Eighth', 'Ninth', 'Tenth']
export const rankLabel = (index: number): string => RANKS[index] ?? `#${index + 1}`

export const AI_BLOCKING_TEXT: Record<AiStackBlocking, string> = {
  no_ai: 'No AI yet. Add one to start.',
  no_tested_ai: 'Test your AI (or finish signing in). Nothing can run until one passes its test.',
  no_heavy_ai: 'Nothing is set up for heavy lifting yet. Set an AI to "Everything" or "Heavy lifting", and test it.',
  no_everyday_ai: 'Nothing is set up for everyday work yet. Set an AI to "Everything" or "Everyday work", and test it.',
}

export function stackBlockingText(reason: string | null | undefined): string {
  return (reason && (AI_BLOCKING_TEXT as Record<string, string>)[reason]) || 'Your AI setup is not ready yet.'
}

export const isPlanOption = (c: Pick<AiConnection, 'engine' | 'auth_mode'>): boolean => c.engine === 'cli' && c.auth_mode === 'plan'

/** "Heavy lifting: Claude plan 67%, Anthropic API 33%." */
export function summarySentence(title: string, entries: AiSummaryEntry[]): string {
  if (entries.length === 0) return `${title}: no AI covers this yet.`
  const parts = entries.map((e) => (e.backup || e.percent <= 0 ? `${e.label} (backup)` : `${e.label} ${Math.round(e.percent)}%`))
  return `${title}: ${parts.join(', ')}.`
}

export const terminalLoginCommand = (label: string): string => `foundryos ai login ${/\s/.test(label) ? `"${label}"` : label}`

/* ---------------------------------- normalizers ---------------------------------- */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const STATES = Object.keys(STATE_LABEL) as AiConnState[]

function normLastTest(raw: unknown): AiLastTest | null {
  const o = obj(raw)
  if (!o || typeof o.ok !== 'boolean') return null
  return { ok: o.ok, detail: str(o.detail) ?? str(o.message), tested_at: str(o.tested_at), model: str(o.model) }
}

export function normalizeAiConnection(raw: unknown): AiConnection | null {
  const o = obj(raw)
  const option = OPTION_IDS.find((x) => x === o?.option)
  if (!o || typeof o.id !== 'string' || !option) return null
  const s = obj(o.status) ?? {}
  const state = (STATES as string[]).includes(s.state as string) ? (s.state as AiConnState) : 'needs_test'
  const position = typeof o.position === 'number' ? o.position : 0
  const useFor = o.use_for === 'heavy' || o.use_for === 'everyday' ? o.use_for : 'everything'
  const share = typeof o.share === 'number' && o.share >= 0 && o.share <= 4 ? Math.round(o.share) : 2
  return {
    id: o.id,
    position,
    rank_label: str(o.rank_label) ?? rankLabel(position),
    label: str(o.label) ?? option,
    engine: o.engine === 'cli' ? 'cli' : 'api',
    option,
    provider_kind: str(o.provider_kind),
    model_provider_id: str(o.model_provider_id),
    model: str(o.model),
    cli_id: str(o.cli_id),
    auth_mode: o.auth_mode === 'plan' || o.auth_mode === 'api_key' || o.auth_mode === 'provider' ? o.auth_mode : null,
    credential_id: str(o.credential_id),
    cli_provider_id: str(o.cli_provider_id),
    use_for: useFor,
    share,
    enabled: o.enabled !== false,
    status: {
      state,
      message: str(s.message),
      last_test: normLastTest(s.last_test),
      signed_in: typeof s.signed_in === 'boolean' ? s.signed_in : null,
      account: str(s.account),
      paused_until: str(s.paused_until),
    },
    runs_24h: typeof o.runs_24h === 'number' ? o.runs_24h : 0,
    max_concurrency: typeof o.max_concurrency === 'number' ? o.max_concurrency : null,
    home_dir: str(o.home_dir),
    created_at: str(o.created_at),
  }
}

function normOption(raw: unknown): AiOption | null {
  const o = obj(raw)
  const option = OPTION_IDS.find((x) => x === o?.option)
  if (!o || !option) return null
  const inst = obj(o.install)
  return {
    option,
    label: str(o.label) ?? option,
    group: o.group === 'plan' || o.group === 'local' ? o.group : 'api',
    engine: o.engine === 'cli' ? 'cli' : 'api',
    cli_id: str(o.cli_id),
    provider_kind: str(o.provider_kind),
    available: o.available !== false,
    reason: str(o.reason),
    needs_install: o.needs_install === true,
    install: inst ? { available: inst.available === true, command: str(inst.command) } : null,
    warning: str(o.warning),
  }
}

function normSummary(raw: unknown): AiSummaryEntry[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((r) => {
    const o = obj(r)
    if (!o || typeof o.connection_id !== 'string') return []
    return [{ connection_id: o.connection_id, label: str(o.label) ?? 'AI', percent: typeof o.percent === 'number' ? o.percent : 0, backup: o.backup === true }]
  })
}

export function normalizeAiStack(raw: unknown): AiStackData | null {
  const o = obj(raw)
  if (!o || !Array.isArray(o.items)) return null
  const items = o.items.map(normalizeAiConnection).filter((c): c is AiConnection => c !== null)
  const sum = obj(o.summary) ?? {}
  return {
    items: items.sort((a, b) => a.position - b.position),
    ready: o.ready === true,
    blocking_reason: str(o.blocking_reason),
    summary: { heavy: normSummary(sum.heavy), everyday: normSummary(sum.everyday) },
    options: (Array.isArray(o.options) ? o.options : []).map(normOption).filter((x): x is AiOption => x !== null),
  }
}

export const normalizeAiLogin = (raw: unknown): ClaudeLoginStatus | null => normalizeClaudeLogin(raw)
