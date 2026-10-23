import type { ApiErrorCode, Page, ValidationDetail } from './types'

export const API_BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: ApiErrorCode | (string & {})
  readonly details: unknown

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  /** Validation problems keyed by field path (e.g. `name`, `founder.name`). */
  get fieldErrors(): Record<string, string> {
    return fieldErrorsFromDetails(this.details)
  }
}

const LOC_PREFIXES = new Set(['body', 'query', 'path'])

/** Convert a 422 `details` list of `{loc, msg}` into `{ 'a.b': msg }`. First message per field wins. */
export function fieldErrorsFromDetails(details: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!Array.isArray(details)) return out
  for (const d of details as Partial<ValidationDetail>[]) {
    if (!d || typeof d.msg !== 'string') continue
    const loc = Array.isArray(d.loc) ? d.loc.filter((p, i) => !(i === 0 && LOC_PREFIXES.has(String(p)))) : []
    const key = loc.join('.')
    if (!(key in out)) out[key] = d.msg
  }
  return out
}

export function errorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error && err.message) return err.message
  return fallback
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = path.startsWith('/api') ? path : `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    params.set(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

function isEnvelope(x: unknown): x is { error: { code?: unknown; message?: unknown; details?: unknown } } {
  return typeof x === 'object' && x !== null && 'error' in x && typeof (x as { error: unknown }).error === 'object'
}

/** Fetch JSON from the Company OS API. Non-2xx responses throw a typed `ApiError`. */
export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal } = opts
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res: Response
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'network_error', 'Could not reach the Company OS API. Is the backend running?')
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  let parsed: unknown = undefined
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = undefined
    }
  }

  if (!res.ok) {
    if (isEnvelope(parsed)) {
      const e = parsed.error
      throw new ApiError(
        res.status,
        typeof e.code === 'string' ? e.code : 'internal_error',
        typeof e.message === 'string' ? e.message : `Request failed (${res.status})`,
        e.details ?? null,
      )
    }
    throw new ApiError(
      res.status,
      res.status >= 500 ? 'internal_error' : 'bad_request',
      `Request failed (${res.status} ${res.statusText || ''})`.trim(),
    )
  }

  if (parsed === undefined && text) {
    throw new ApiError(res.status, 'invalid_response', 'The server returned a response that was not valid JSON.')
  }
  return parsed as T
}

/** Follow `next_cursor` until exhausted (bounded) and return every item. */
export async function fetchAllPages<T>(
  path: string,
  query: RequestOptions['query'] = {},
  signal?: AbortSignal,
  maxPages = 25,
): Promise<T[]> {
  const items: T[] = []
  let cursor: string | null = null
  for (let i = 0; i < maxPages; i++) {
    const page: Page<T> = await apiFetch<Page<T>>(path, { query: { ...query, limit: 200, cursor }, signal })
    items.push(...page.items)
    if (!page.next_cursor) break
    cursor = page.next_cursor
  }
  return items
}
