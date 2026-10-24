import type {
  AiBlockingReason,
  AiSetupStatus,
  AiSetupUpdate,
  CliAuthMode,
  CliInstallLog,
  CliLastTest,
  CliRuntimeStatus,
  ProviderCheckName,
  ProviderKind,
  ProviderTest,
  ProviderTestCheck,
  WorkMode,
  WorkRuntime,
} from '@/api/types'
import type { Tone } from '@/components/ui/Badge'
import { emptyProviderForm, type ProviderFormValues } from './providers'

/**
 * M10 AI setup (docs/specs/m10 §1-§3): pure helpers. The gate rule, the check-to-loader mapping, the work-mode rules and the
 * CLI tile vocabulary live here so they are tested without a DOM. Every backend response is read defensively: the M10
 * backend is built in parallel and unknown fields are ignored, never invented.
 */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)

/* ------------------------------------ the gate ----------------------------------- */

/** The context window an AI setup needs (spec §1). Shown in copy; the server decides. */
export const MIN_CONTEXT_TOKENS = 32_768

export const BLOCKING_REASON_TEXT: Record<AiBlockingReason, string> = {
  no_tested_provider: 'Connect an AI provider and run its test. Nothing can run without a working one.',
  provider_test_failed: 'The last test of your AI provider did not pass. Fix the connection and test it again.',
  no_default_model: 'Choose the model your AI Co-founder should use to manage the company.',
}

export function blockingReasonText(reason: AiBlockingReason | string | null | undefined): string {
  if (!reason) return BLOCKING_REASON_TEXT.no_tested_provider
  return (BLOCKING_REASON_TEXT as Record<string, string>)[reason] ?? 'Your AI setup is not ready yet.'
}

export type AiGateState = 'loading' | 'open' | 'blocked'

/**
 * Whether the canvas may be shown. Only an explicit `ready: false` blocks. A backend that predates M10 (404), an error
 * or a malformed answer never blocks: the server enforces readiness itself (409 `ai_setup_required`) and the UI must not
 * lock a person out because it could not read the status.
 */
export function aiGateState(q: { isPending: boolean; data?: AiSetupStatus | null }): AiGateState {
  if (q.isPending) return 'loading'
  return q.data && q.data.ready === false ? 'blocked' : 'open'
}

/** Normalise `GET /ai-setup`. Returns null when the body is not an AI-setup object (treated as "not available"). */
export function normalizeAiSetup(raw: unknown): AiSetupStatus | null {
  const o = obj(raw)
  if (!o || typeof o.ready !== 'boolean') return null
  const m = obj(o.management) ?? {}
  const mode = o.work_mode === 'hybrid' ? 'hybrid' : 'api'
  return {
    ready: o.ready,
    blocking_reason: (str(o.blocking_reason) as AiBlockingReason | null) ?? null,
    management: {
      provider_id: str(m.provider_id),
      provider_name: str(m.provider_name),
      kind: str(m.kind),
      model: str(m.model),
      last_test: m.last_test ? normalizeProviderTest(m.last_test) : null,
    },
    work_mode: mode,
    work_runtime: (str(o.work_runtime) as WorkRuntime | null) ?? null,
    clis: Array.isArray(o.clis) ? o.clis.map(normalizeCli).filter((c): c is CliRuntimeStatus => c !== null) : [],
  }
}

/* ------------------------------- provider test checks ----------------------------- */

export const CHECK_ORDER: ProviderCheckName[] = ['reachable', 'auth', 'tool_calling', 'context', 'model_exists']

export const CHECK_LABEL: Record<ProviderCheckName, string> = {
  reachable: 'Reachable',
  auth: 'Authenticated',
  tool_calling: 'Tool calling',
  context: 'Context window',
  model_exists: 'Model available',
}

export function checkLabel(name: string): string {
  return (CHECK_LABEL as Record<string, string>)[name] ?? name.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

/** Normalise `POST /model-providers/{id}/test` (the M10 shape; older shapes yield an empty `checks`). */
export function normalizeProviderTest(raw: unknown): ProviderTest {
  const o = obj(raw) ?? {}
  const checks: ProviderTestCheck[] = []
  if (Array.isArray(o.checks)) {
    for (const c of o.checks) {
      const co = obj(c)
      const name = co && str(co.name)
      if (!co || !name) continue
      checks.push({ name, ok: co.ok === true, detail: str(co.detail) ?? str(co.message) })
    }
  }
  // `status`/`checked_at` are the pre-M10 names of the same facts; tolerated so an older test result still reads.
  const status = str(o.status)
  const ok = bool(o.ok) ?? (status ? status === 'ok' && checks.every((c) => c.ok) : checks.length > 0 && checks.every((c) => c.ok))
  return {
    ok,
    checks,
    served_context: num(o.served_context) ?? num(o.context_length_served),
    latency_ms: num(o.latency_ms),
    tested_at: str(o.tested_at) ?? str(o.checked_at),
  }
}

export type LoaderStepState = 'pending' | 'running' | 'passed' | 'failed' | 'unchecked'

export interface LoaderStep {
  name: string
  label: string
  state: LoaderStepState
  detail: string | null
}

/**
 * The multi-step loader's rows, driven only by what the server reported.
 *  - request in flight: nothing is known yet, so the first row spins and the rest wait (no timers, no guessing)
 *  - result in: each reported check is its real pass/fail with the server's detail; a check the server did not run is
 *    "not checked"; the last row ("Ready") passes only when the overall test passed.
 * The order is fixed (reachable, auth, tool calling, context, model) plus any extra check the server reports.
 */
export function loaderSteps(test: ProviderTest | null, running: boolean): LoaderStep[] {
  const names: string[] = [...CHECK_ORDER]
  for (const c of test?.checks ?? []) if (!names.includes(c.name)) names.push(c.name)
  const steps: LoaderStep[] = names.map((name, i) => {
    const base = { name, label: checkLabel(name), detail: null as string | null }
    if (running) return { ...base, state: i === 0 ? 'running' : 'pending' }
    if (!test) return { ...base, state: 'pending' }
    const hit = test.checks.find((c) => c.name === name)
    if (!hit) return { ...base, state: 'unchecked' }
    return { ...base, state: hit.ok ? 'passed' : 'failed', detail: hit.detail }
  })
  const ready: LoaderStep = {
    name: 'ready',
    label: 'Ready',
    state: running ? 'pending' : !test ? 'pending' : test.ok ? 'passed' : 'failed',
    detail: null,
  }
  return [...steps, ready]
}

/** The first failing check, for a one-line "what to fix". */
export function firstFailure(test: ProviderTest | null): ProviderTestCheck | null {
  return test?.checks.find((c) => !c.ok) ?? null
}

/** A passing test with fewer checks than the five expected is not trusted as "ready" by the UI. */
export function testPassed(test: ProviderTest | null): boolean {
  return !!test && test.ok && test.checks.every((c) => c.ok)
}

/* ---------------------------------- provider cards --------------------------------- */

export type ProviderCardId = 'openrouter' | 'anthropic' | 'openai' | 'openai_compatible' | 'ollama'

export interface ProviderCardDef {
  id: ProviderCardId
  kind: ProviderKind
  label: string
  blurb: string
  needsBaseUrl: boolean
  needsKey: boolean
  keyLabel: string
  /** Clickable examples, never pre-filled: the model-exists check tells the truth. */
  modelSuggestions: string[]
  baseUrlHint: string
}

export const PROVIDER_CARDS: ProviderCardDef[] = [
  {
    id: 'openrouter',
    kind: 'openrouter',
    label: 'OpenRouter',
    blurb: 'One key, hundreds of models from every lab.',
    needsBaseUrl: false,
    needsKey: true,
    keyLabel: 'OpenRouter API key',
    modelSuggestions: ['anthropic/claude-sonnet-4.5', 'openai/gpt-4.1'],
    baseUrlHint: '',
  },
  {
    id: 'anthropic',
    kind: 'anthropic',
    label: 'Anthropic',
    blurb: 'Claude models, straight from Anthropic.',
    needsBaseUrl: false,
    needsKey: true,
    keyLabel: 'Anthropic API key',
    modelSuggestions: ['claude-sonnet-4-5'],
    baseUrlHint: '',
  },
  {
    id: 'openai',
    kind: 'openai',
    label: 'OpenAI',
    blurb: 'GPT models, straight from OpenAI.',
    needsBaseUrl: false,
    needsKey: true,
    keyLabel: 'OpenAI API key',
    modelSuggestions: ['gpt-4.1'],
    baseUrlHint: '',
  },
  {
    id: 'openai_compatible',
    kind: 'openai_compatible',
    label: 'OpenAI-compatible',
    blurb: 'Any compatible API: Groq, Together, DeepSeek, a gateway or your own server.',
    needsBaseUrl: true,
    needsKey: false,
    keyLabel: 'API key (if the server needs one)',
    modelSuggestions: [],
    baseUrlHint: 'https://host.example/v1',
  },
]

export const OLLAMA_CARD: ProviderCardDef = {
  id: 'ollama',
  kind: 'ollama',
  label: 'Local Ollama',
  blurb: 'Runs on this computer: free and private.',
  needsBaseUrl: true,
  needsKey: false,
  keyLabel: '',
  modelSuggestions: [],
  baseUrlHint: 'http://127.0.0.1:11434',
}

export function cardFor(id: ProviderCardId): ProviderCardDef {
  return id === 'ollama' ? OLLAMA_CARD : PROVIDER_CARDS.find((c) => c.id === id)!
}

/** The provider form (lib/providers) for a card and what the person typed. The secret stays in the form values only. */
export function providerFormFor(card: ProviderCardDef, v: { baseUrl: string; model: string; secret: string; contextLength?: string }): ProviderFormValues {
  return emptyProviderForm({
    name: card.id === 'ollama' ? `Ollama · ${v.model.trim() || 'local'}` : card.label,
    kind: card.kind,
    base_url: card.needsBaseUrl ? v.baseUrl : '',
    default_model: v.model,
    secret: card.needsKey || card.kind === 'openai_compatible' ? v.secret : '',
    context_length: v.contextLength ?? '',
    credential_name: `${card.id}-key`,
  })
}

/** Whether a "Test" can be started with what has been entered (no network, no secret echo). */
export function canTestProvider(card: ProviderCardDef, v: { baseUrl: string; model: string; secret: string; hasCredential: boolean }): boolean {
  if (!v.model.trim()) return false
  if (card.needsBaseUrl && !v.baseUrl.trim()) return false
  if (card.needsKey && !v.secret.trim() && !v.hasCredential) return false
  return true
}

/** Normalise `GET /model-providers/{id}/models` into pickable options. Strings or objects, in `items`, `models`, `data` or bare. */
export interface ModelOption {
  id: string
  context_length: number | null
}
export function normalizeModelList(raw: unknown): ModelOption[] {
  const o = obj(raw)
  const list = Array.isArray(raw) ? raw : Array.isArray(o?.items) ? o.items : Array.isArray(o?.models) ? o.models : Array.isArray(o?.data) ? o.data : []
  const out: ModelOption[] = []
  const seen = new Set<string>()
  for (const m of list) {
    const mo = obj(m)
    const id = typeof m === 'string' ? m : mo ? (str(mo.id) ?? str(mo.name) ?? str(mo.model)) : null
    if (!id || seen.has(id)) continue
    seen.add(id)
    out.push({ id, context_length: mo ? (num(mo.context_length) ?? num(mo.context_window)) : null })
  }
  return out
}

/** Filter the picker; empty query = everything. Case-insensitive substring, prefix matches first. */
export function filterModels(models: ModelOption[], query: string, limit = 50): ModelOption[] {
  const q = query.trim().toLowerCase()
  if (!q) return models.slice(0, limit)
  const starts: ModelOption[] = []
  const contains: ModelOption[] = []
  for (const m of models) {
    const id = m.id.toLowerCase()
    if (id.startsWith(q)) starts.push(m)
    else if (id.includes(q)) contains.push(m)
  }
  return [...starts, ...contains].slice(0, limit)
}

/* ------------------------------------ work modes ----------------------------------- */

export const WORK_MODE_LABEL: Record<WorkMode, string> = { api: 'API only', hybrid: 'API + coding CLIs' }
export const WORK_MODE_BLURB: Record<WorkMode, string> = {
  api: 'Every employee works through your API provider. Simplest, and the strongest isolation.',
  hybrid: 'Your API runs the company (AI Co-founder, HR, leads, reviews). Employees do hands-on work through a coding CLI such as Claude Code, Codex or OpenCode.',
}

export const COMPANY_AGENT_WORK_RUNTIME: WorkRuntime = 'company_agent'

export function runtimeIdOf(cli: Pick<CliRuntimeStatus, 'id'>): WorkRuntime {
  return (cli.id.startsWith('cli:') ? cli.id : `cli:${cli.id}`) as WorkRuntime
}

/** A CLI can do company work only once it is installed and its own test passed. */
export function cliReady(cli: CliRuntimeStatus): boolean {
  return cli.installed && cli.auth.last_test?.ok === true
}

export function readyClis(clis: CliRuntimeStatus[]): CliRuntimeStatus[] {
  return clis.filter(cliReady)
}

/** Hybrid needs a work runtime that tested OK. API only always passes. */
export function workModeProblem(mode: WorkMode, runtime: WorkRuntime | null, clis: CliRuntimeStatus[]): string | null {
  if (mode === 'api') return null
  if (readyClis(clis).length === 0) return 'Install a coding CLI and run its test first. Only a CLI that tested OK can do the work.'
  if (!runtime || runtime === 'company_agent') return 'Choose which CLI should do the work.'
  const cli = clis.find((c) => runtimeIdOf(c) === runtime)
  if (!cli || !cliReady(cli)) return 'The chosen CLI has not tested OK. Run its test or pick another one.'
  return null
}

/** The `PUT /ai-setup` body for a choice of mode and runtime, or null when no management model is known yet. */
export function aiSetupUpdate(ai: Pick<AiSetupStatus, 'management'>, mode: WorkMode, runtime: WorkRuntime | null): AiSetupUpdate | null {
  const { provider_id, model } = ai.management
  if (!provider_id || !model) return null
  return {
    management_provider_id: provider_id,
    management_model: model,
    work_mode: mode,
    work_runtime: mode === 'api' ? COMPANY_AGENT_WORK_RUNTIME : runtime,
  }
}

/* -------------------------------------- CLI tiles ---------------------------------- */

export const CLI_AUTH_LABEL: Record<CliAuthMode, string> = { api_key: 'API key (recommended)', own_login: 'My own login' }
export const CLI_AUTH_BLURB: Record<CliAuthMode, string> = {
  api_key: 'Strongest isolation: each employee gets its own private home folder and the key is handed only to that run.',
  own_login: 'Uses the login already on this computer.',
}
export const OWN_LOGIN_WARNING =
  'Weaker isolation: the tool needs your real home folder to find your login, so it runs with access to what that folder holds. Check the tool’s own terms before using a personal or subscription login for automated work. Company OS never reads or copies the tool’s login files.'

export function sandboxSummary(s: CliRuntimeStatus['sandbox']): { label: string; tone: Tone; help: string } {
  if (!s.available || s.kind === 'none') {
    return { label: 'No sandbox', tone: 'warning', help: s.detail ?? 'No sandbox is available, so shell access is turned off for this tool.' }
  }
  return {
    label: s.kind === 'docker' ? 'Docker sandbox' : 'OS sandbox',
    tone: 'success',
    help: s.detail ?? (s.kind === 'docker' ? 'Shell commands run in a Docker container.' : 'Shell commands run in an operating-system sandbox.'),
  }
}

export interface CliStatusLine {
  label: string
  tone: Tone
}

export function cliStatusLine(cli: CliRuntimeStatus): CliStatusLine {
  if (!cli.installed) return { label: 'Not installed', tone: 'neutral' }
  const version = cli.version ? ` ${cli.version}` : ''
  if (cli.auth.last_test?.ok) return { label: `Ready${version}`, tone: 'success' }
  if (cli.auth.last_test && !cli.auth.last_test.ok) return { label: `Test failed${version}`, tone: 'danger' }
  return { label: `Installed${version}`, tone: 'info' }
}

/** Defensive read of one `CliRuntimeStatus`. */
export function normalizeCli(raw: unknown): CliRuntimeStatus | null {
  const o = obj(raw)
  const id = o && str(o.id)
  if (!o || !id) return null
  const a = obj(o.auth) ?? {}
  const s = obj(o.sandbox) ?? {}
  const inst = obj(o.install) ?? {}
  const modes = Array.isArray(o.auth_modes) ? (o.auth_modes.filter((m) => m === 'api_key' || m === 'own_login') as CliAuthMode[]) : []
  const kind = s.kind === 'os' || s.kind === 'docker' ? s.kind : 'none'
  return {
    id,
    display_name: str(o.display_name) ?? id,
    installed: o.installed === true,
    binary_path: str(o.binary_path),
    version: str(o.version),
    auth_modes: modes,
    auth: {
      mode: a.mode === 'api_key' || a.mode === 'own_login' ? a.mode : null,
      configured: a.configured === true,
      credential_id: str(a.credential_id),
      last_test: normalizeCliTest(a.last_test),
      model_provider_id: str(a.model_provider_id),
      model: str(a.model),
    },
    sandbox: { available: s.available === true, kind, detail: str(s.detail) },
    install: { available: inst.available === true, command: str(inst.command), requires: Array.isArray(inst.requires) ? inst.requires.filter((r): r is string => typeof r === 'string') : [] },
    login_hint: str(o.login_hint),
    warnings: Array.isArray(o.warnings) ? o.warnings.filter((w): w is string => typeof w === 'string' && w !== '') : [],
  }
}

/** The result of a CLI test: `{ok, detail, tested_at}` itself, or a status object carrying it as `auth.last_test`. */
export function normalizeCliTest(raw: unknown): CliLastTest | null {
  const o = obj(raw)
  if (!o) return null
  const nested = obj(obj(o.auth)?.last_test)
  const t = typeof o.ok === 'boolean' ? o : nested
  if (!t || typeof t.ok !== 'boolean') return null
  return { ok: t.ok, detail: str(t.detail) ?? str(t.message), tested_at: str(t.tested_at) }
}

/** Defensive read of `GET /cli-runtimes/{id}/install-log`: `{state|status, lines|log|text, exit_code, done}`. */
export function normalizeInstallLog(raw: unknown): CliInstallLog {
  const o = obj(raw) ?? {}
  const rawState = str(o.state) ?? str(o.status)
  const exit = num(o.exit_code)
  let state: CliInstallLog['state'] = 'idle'
  if (rawState === 'running' || rawState === 'succeeded' || rawState === 'failed' || rawState === 'idle') state = rawState
  else if (rawState === 'none') state = 'idle'
  else if (rawState === 'success' || rawState === 'done' || rawState === 'completed') state = 'succeeded'
  else if (rawState === 'error') state = 'failed'
  else if (o.done === true || o.finished === true) state = exit && exit !== 0 ? 'failed' : 'succeeded'
  const lines = Array.isArray(o.lines) ? o.lines.map(String) : typeof o.log === 'string' ? o.log.split('\n') : typeof o.text === 'string' ? o.text.split('\n') : []
  return { state, lines: lines.filter((l, i, a) => l !== '' || i < a.length - 1), exit_code: exit }
}

/** The command the person is asked to confirm: exactly what the server will run, never reworded. */
export function installCommandOf(cli: Pick<CliRuntimeStatus, 'install'>): string | null {
  return cli.install.available && cli.install.command ? cli.install.command : null
}
