import type {
  CreateCredentialRequest,
  CreateModelProviderRequest,
  DetectResult,
  DetectedModel,
  ModelProvider,
  ProviderKind,
  ProviderTestResult,
} from '@/api/types'
import { formatUsd, usdToMicros } from './money'

/**
 * Model providers (M3b). Pure helpers: response normalisation (the endpoints were specified in prose
 * only, so every field is read defensively) and the "add provider" form rules.
 *
 * Secrets: the API key is write-only. It lives in the form state until submit, goes only into
 * `toCreateCredentialBody`, and is never part of the provider body, a response type or a message.
 */

export const PROVIDER_KINDS: readonly ProviderKind[] = ['ollama', 'openrouter', 'anthropic', 'openai', 'openai_compatible']

export const PROVIDER_KIND_LABEL: Record<ProviderKind, string> = {
  ollama: 'Ollama (local)',
  openrouter: 'OpenRouter',
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  openai_compatible: 'OpenAI-compatible endpoint',
}

export function providerKindLabel(kind: string): string {
  return (PROVIDER_KIND_LABEL as Record<string, string>)[kind] ?? kind
}

/** Placeholder hints only: nothing is pre-filled, so a wrong default can never be submitted unseen. */
export const BASE_URL_HINT: Record<ProviderKind, string> = {
  ollama: 'http://127.0.0.1:11434',
  openrouter: 'https://openrouter.ai/api/v1',
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
  openai_compatible: 'https://host.example/v1',
}

const needsBaseUrl = (kind: ProviderKind) => kind === 'ollama' || kind === 'openai_compatible'
export const needsCredential = (kind: ProviderKind) => kind === 'openrouter' || kind === 'anthropic' || kind === 'openai'
export const allowsCredential = (kind: ProviderKind) => kind !== 'ollama'

export function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname.replace(/^\[|\]$/g, '')
    return host === 'localhost' || host === '::1' || /^127\./.test(host)
  } catch {
    return false
  }
}

/** Whether calls to this provider run on this machine (no provider charge). Derived, never typed in. */
export function deriveIsLocal(kind: ProviderKind, baseUrl: string): boolean {
  return kind === 'ollama' || (kind === 'openai_compatible' && isLoopbackUrl(baseUrl))
}

/* ------------------------------- display -------------------------------- */

export function isFreeProvider(p: Pick<ModelProvider, 'is_local' | 'input_price_per_mtok_micros' | 'output_price_per_mtok_micros'>): boolean {
  return !p.is_local && p.input_price_per_mtok_micros === 0 && p.output_price_per_mtok_micros === 0
}

/** "$0.15 in / $0.60 out per 1M tokens", or an honest "Unpriced" (null is unknown, not zero). */
export function describePrices(p: Pick<ModelProvider, 'input_price_per_mtok_micros' | 'output_price_per_mtok_micros'>): string {
  const { input_price_per_mtok_micros: i, output_price_per_mtok_micros: o } = p
  if (i == null && o == null) return 'Unpriced'
  return `${i == null ? 'unknown' : formatUsd(i)} in / ${o == null ? 'unknown' : formatUsd(o)} out per 1M`
}

/* ------------------------------- the form -------------------------------- */

export interface ProviderFormValues {
  name: string
  kind: ProviderKind
  base_url: string
  default_model: string
  context_length: string
  /** USD per 1M input / output tokens, as typed. */
  input_price: string
  output_price: string
  credential_name: string
  /** Write-only. */
  secret: string
}

export type ProviderFormErrors = Partial<Record<keyof ProviderFormValues, string>>

export function emptyProviderForm(partial: Partial<ProviderFormValues> = {}): ProviderFormValues {
  return {
    name: '',
    kind: 'ollama',
    base_url: '',
    default_model: '',
    context_length: '',
    input_price: '',
    output_price: '',
    credential_name: '',
    secret: '',
    ...partial,
  }
}

const PRICE_ERR = 'Enter a USD amount per 1M tokens like 0.15 (up to 6 decimals)'

function parsePrice(input: string): { value: number | null; error?: string } {
  if (input.trim() === '') return { value: null }
  const micros = usdToMicros(input)
  return micros === null ? { value: null, error: PRICE_ERR } : { value: micros }
}

/** Positive integer or blank. */
export function parseContextLength(input: string): { value: number | null; error?: string } {
  const t = input.trim().replace(/[,_\s]/g, '')
  if (t === '') return { value: null }
  if (!/^\d+$/.test(t) || Number(t) < 1 || !Number.isSafeInteger(Number(t))) return { value: null, error: 'Enter a whole number of tokens, e.g. 32768' }
  return { value: Number(t) }
}

export function validateProviderForm(v: ProviderFormValues, opts: { hasCredential?: boolean } = {}): ProviderFormErrors {
  const e: ProviderFormErrors = {}
  const name = v.name.trim()
  if (!name) e.name = 'Name is required'
  else if (name.length > 80) e.name = 'At most 80 characters'
  const url = v.base_url.trim()
  if (!url) {
    if (needsBaseUrl(v.kind)) e.base_url = 'Base URL is required for this kind'
  } else {
    try {
      const u = new URL(url)
      if (u.protocol !== 'http:' && u.protocol !== 'https:') e.base_url = 'Use an http:// or https:// URL'
      else if (u.username || u.password) e.base_url = 'Do not put credentials in the URL; use the API key field'
    } catch {
      e.base_url = 'Enter a full URL like http://127.0.0.1:11434'
    }
  }
  if (v.default_model.length > 160) e.default_model = 'At most 160 characters'
  const ctx = parseContextLength(v.context_length)
  if (ctx.error) e.context_length = ctx.error
  const inp = parsePrice(v.input_price)
  if (inp.error) e.input_price = inp.error
  const out = parsePrice(v.output_price)
  if (out.error) e.output_price = out.error
  if (needsCredential(v.kind) && !v.secret.trim() && !opts.hasCredential) e.secret = 'An API key is required for this provider'
  if (v.secret && /\s/.test(v.secret.trim())) e.secret = 'An API key has no spaces or line breaks; check what you pasted'
  return e
}

/** The provider body. Deliberately has no secret: it references the credential by id. */
export function toCreateProviderBody(v: ProviderFormValues, credentialId: string | null): CreateModelProviderRequest {
  const url = v.base_url.trim()
  const model = v.default_model.trim()
  return {
    name: v.name.trim(),
    kind: v.kind,
    base_url: url || null,
    credential_id: credentialId,
    default_model: model || null,
    context_length: parseContextLength(v.context_length).value,
    is_local: deriveIsLocal(v.kind, url),
    enabled: true,
    input_price_per_mtok_micros: parsePrice(v.input_price).value,
    output_price_per_mtok_micros: parsePrice(v.output_price).value,
  }
}

/** The only place the secret is read. Returns null when the form carries no secret. */
export function toCreateCredentialBody(v: ProviderFormValues): CreateCredentialRequest | null {
  const value = v.secret.trim()
  if (!allowsCredential(v.kind) || !value) return null
  const name = v.credential_name.trim() || `${v.name.trim() || v.kind}-key`
  return { name, kind: 'api_key', value }
}

/** Defensive: never let an error message echo the secret back into the UI. */
export function redactSecret(text: string, secret: string): string {
  const s = secret.trim()
  return s.length >= 4 ? text.split(s).join('[redacted]') : text
}

/** Pre-fill the form from a model found on the local Ollama. */
export function formFromDetected(baseUrl: string | null, model: DetectedModel): ProviderFormValues {
  return emptyProviderForm({
    name: `Ollama · ${model.name}`,
    kind: 'ollama',
    base_url: baseUrl ?? '',
    default_model: model.name,
    context_length: model.context_length ? String(model.context_length) : '',
  })
}

/* ---------------------------- response normalising ---------------------------- */

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)

function normalizeModel(m: unknown): DetectedModel | null {
  if (typeof m === 'string') return m ? { name: m, size_bytes: null, context_length: null } : null
  const o = obj(m)
  if (!o) return null
  const name = str(o.name) ?? str(o.model) ?? str(o.id)
  if (!name) return null
  return { name, size_bytes: num(o.size_bytes) ?? num(o.size), context_length: num(o.context_length) ?? num(o.context_window) }
}

/** `GET /model-providers/detect`. Accepts `{models|items: [...]}` of strings or objects. */
export function normalizeDetect(raw: unknown): DetectResult {
  const o = obj(raw) ?? {}
  const list = Array.isArray(o.models) ? o.models : Array.isArray(o.items) ? o.items : Array.isArray(raw) ? raw : []
  const models = list.map(normalizeModel).filter((m): m is DetectedModel => m !== null)
  const reachable = bool(o.reachable) ?? bool(o.available) ?? bool(o.ok) ?? models.length > 0
  return { reachable, base_url: str(o.base_url) ?? str(o.url), models, message: str(o.message) ?? str(o.error) ?? str(o.detail) }
}

/** `POST /model-providers/{id}/test`. Server-written warnings (e.g. served context) are passed through verbatim. */
export function normalizeTestResult(raw: unknown): ProviderTestResult {
  const o = obj(raw) ?? {}
  const status = str(o.status)
  const ok = bool(o.ok) ?? bool(o.success) ?? bool(o.reachable) ?? (status ? status === 'ok' || status === 'success' : false)
  const warnings: string[] = []
  if (Array.isArray(o.warnings)) for (const w of o.warnings) if (str(w)) warnings.push(w as string)
  for (const key of ['warning', 'context_warning', 'served_context_warning']) if (str(o[key])) warnings.push(o[key] as string)
  return {
    ok,
    message: str(o.message) ?? str(o.error) ?? str(o.detail) ?? (status && status !== 'ok' ? status : null),
    warnings: [...new Set(warnings)],
    latency_ms: num(o.latency_ms),
    model: str(o.model) ?? str(o.model_name),
  }
}
