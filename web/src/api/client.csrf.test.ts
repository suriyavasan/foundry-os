import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse } from '@/test/factories'
import { ApiError, apiFetch, csrfTokenFor, isCsrfFailure, readCookie } from './client'

const setCookie = (v: string) => {
  document.cookie = v
}
const clearCookies = () => {
  for (const name of ['cos_csrf', 'cos_owner', 'other']) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
}
const csrf403 = () => jsonResponse({ error: { code: 'csrf_failed', message: 'CSRF token missing or invalid', details: null } }, 403)

beforeEach(clearCookies)
afterEach(() => {
  vi.unstubAllGlobals()
  clearCookies()
})

type Call = { url: string; method: string; headers: Record<string, string> }
function stub(handler: (call: Call, n: number) => Response) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const call = { url, method: init?.method ?? 'GET', headers: (init?.headers ?? {}) as Record<string, string> }
      calls.push(call)
      return handler(call, calls.length)
    }),
  )
  return calls
}

describe('readCookie', () => {
  it('finds a cookie among others and decodes it', () => {
    expect(readCookie('cos_csrf', 'a=1; cos_csrf=ab%2Fcd; b=2')).toBe('ab/cd')
    expect(readCookie('cos_csrf', 'a=1')).toBeNull()
    expect(readCookie('cos_csrf', 'xcos_csrf=1')).toBeNull()
    expect(readCookie('cos_csrf', 'cos_csrf=')).toBeNull()
  })
})

describe('csrfTokenFor', () => {
  it('is only for state-changing methods and only when the cookie exists', () => {
    expect(csrfTokenFor('POST', 'cos_csrf=t')).toBe('t')
    for (const m of ['PATCH', 'PUT', 'DELETE', 'post']) expect(csrfTokenFor(m, 'cos_csrf=t')).toBe('t')
    for (const m of ['GET', 'HEAD', 'OPTIONS']) expect(csrfTokenFor(m, 'cos_csrf=t')).toBeNull()
    expect(csrfTokenFor('POST', 'cos_owner=secret')).toBeNull()
  })
})

describe('CSRF header injection', () => {
  it('sends X-CSRF-Token on state-changing requests when cookie auth is in use', async () => {
    setCookie('cos_csrf=tok123')
    const calls = stub(() => jsonResponse({}))
    await apiFetch('/x', { method: 'POST', body: {} })
    await apiFetch('/x', { method: 'PATCH', body: {} })
    await apiFetch('/x', { method: 'DELETE' })
    expect(calls.map((c) => c.headers['X-CSRF-Token'])).toEqual(['tok123', 'tok123', 'tok123'])
  })

  it('does not send it on reads', async () => {
    setCookie('cos_csrf=tok123')
    const calls = stub(() => jsonResponse({}))
    await apiFetch('/x')
    expect(calls[0]!.headers['X-CSRF-Token']).toBeUndefined()
  })

  it('does not send it without the cookie (legacy owner-token gate, bearer tokens)', async () => {
    setCookie('cos_owner=abc')
    const calls = stub(() => jsonResponse({}))
    await apiFetch('/x', { method: 'POST', body: {} })
    expect(calls[0]!.headers['X-CSRF-Token']).toBeUndefined()
  })

  it('leaves multipart bodies to the browser (no JSON content type) but still carries the token', async () => {
    setCookie('cos_csrf=tok123')
    const calls = stub(() => jsonResponse({}))
    const form = new FormData()
    form.append('file', new Blob(['x']), 'x.txt')
    await apiFetch('/artifacts', { method: 'POST', body: form })
    expect(calls[0]!.headers['Content-Type']).toBeUndefined()
    expect(calls[0]!.headers['X-CSRF-Token']).toBe('tok123')
  })
})

describe('CSRF 403 recovery', () => {
  it('refreshes the session once and retries once with the new token', async () => {
    setCookie('cos_csrf=stale')
    const calls = stub((call, n) => {
      if (call.url === '/api/auth/session') {
        setCookie('cos_csrf=fresh')
        return jsonResponse({ state: 'authenticated' })
      }
      return n === 1 ? csrf403() : jsonResponse({ ok: true })
    })
    await expect(apiFetch('/things', { method: 'POST', body: { a: 1 } })).resolves.toEqual({ ok: true })
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['POST /api/things', 'GET /api/auth/session', 'POST /api/things'])
    expect(calls[0]!.headers['X-CSRF-Token']).toBe('stale')
    expect(calls[2]!.headers['X-CSRF-Token']).toBe('fresh')
  })

  it('retries only once: a second CSRF refusal is an error', async () => {
    setCookie('cos_csrf=stale')
    const calls = stub((call) => (call.url === '/api/auth/session' ? jsonResponse({}) : csrf403()))
    const err = (await apiFetch('/things', { method: 'POST', body: {} }).catch((e: unknown) => e)) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(isCsrfFailure(err)).toBe(true)
    expect(calls.filter((c) => c.url === '/api/things')).toHaveLength(2)
    expect(calls.filter((c) => c.url === '/api/auth/session')).toHaveLength(1)
  })

  it('does not retry an ordinary 403 (permission), nor a CSRF-looking one on a read', async () => {
    setCookie('cos_csrf=tok')
    const calls = stub(() => jsonResponse({ error: { code: 'forbidden', message: 'Only owners can do that', details: null } }, 403))
    await expect(apiFetch('/things', { method: 'POST', body: {} })).rejects.toMatchObject({ status: 403, code: 'forbidden' })
    expect(calls).toHaveLength(1)
    const reads = stub(() => csrf403())
    await expect(apiFetch('/things')).rejects.toBeInstanceOf(ApiError)
    expect(reads).toHaveLength(1)
  })

  it('also recognises a CSRF failure reported in details.reason or the message', () => {
    expect(isCsrfFailure(new ApiError(403, 'forbidden', 'x', { reason: 'csrf_token_mismatch' }))).toBe(true)
    expect(isCsrfFailure(new ApiError(403, 'forbidden', 'Invalid CSRF token'))).toBe(true)
    expect(isCsrfFailure(new ApiError(401, 'csrf_failed', 'x'))).toBe(false)
  })

  it('still fails cleanly when the cookie was missing entirely and the refresh brings none', async () => {
    const calls = stub((call) => (call.url === '/api/auth/session' ? jsonResponse({}) : csrf403()))
    await expect(apiFetch('/things', { method: 'POST', body: {} })).rejects.toBeInstanceOf(ApiError)
    expect(calls.filter((c) => c.url === '/api/things')).toHaveLength(2)
  })
})
