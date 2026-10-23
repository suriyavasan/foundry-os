import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse } from '@/test/factories'
import { ApiError, apiFetch, fetchAllPages, fieldErrorsFromDetails } from './client'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fn = vi.fn((url: string, init?: RequestInit) => Promise.resolve(impl(url, init)))
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('apiFetch', () => {
  it('returns parsed JSON and prefixes /api', async () => {
    const f = stubFetch(() => jsonResponse({ status: 'ok' }))
    await expect(apiFetch('/health')).resolves.toEqual({ status: 'ok' })
    expect(f.mock.calls[0]![0]).toBe('/api/health')
  })

  it('serialises body and query params, skipping empty values', async () => {
    const f = stubFetch(() => jsonResponse({}))
    await apiFetch('/companies/1/agents', { method: 'POST', body: { name: 'x' }, query: { limit: 5, cursor: null, status: '' } })
    const [url, init] = f.mock.calls[0]!
    expect(url).toBe('/api/companies/1/agents?limit=5')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe('{"name":"x"}')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('parses the error envelope into a typed ApiError', async () => {
    stubFetch(() => jsonResponse({ error: { code: 'not_found', message: 'Agent not found', details: null } }, 404))
    const err = await apiFetch('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    const e = err as ApiError
    expect(e.status).toBe(404)
    expect(e.code).toBe('not_found')
    expect(e.message).toBe('Agent not found')
    expect(e.isNotFound).toBe(true)
  })

  it('exposes 422 details as field errors', async () => {
    stubFetch(() =>
      jsonResponse(
        {
          error: {
            code: 'validation_error',
            message: 'Invalid request',
            details: [
              { loc: ['body', 'name'], msg: 'String should have at least 1 character' },
              { loc: ['body', 'founder', 'name'], msg: 'bad founder' },
              { loc: ['body', 'name'], msg: 'second message ignored' },
            ],
          },
        },
        422,
      ),
    )
    const e = (await apiFetch('/x').catch((x: unknown) => x)) as ApiError
    expect(e.code).toBe('validation_error')
    expect(e.fieldErrors).toEqual({ name: 'String should have at least 1 character', 'founder.name': 'bad founder' })
  })

  it('handles non-JSON error bodies', async () => {
    stubFetch(() => new Response('<html>boom</html>', { status: 502, statusText: 'Bad Gateway' }))
    const e = (await apiFetch('/x').catch((x: unknown) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(502)
    expect(e.code).toBe('internal_error')
    expect(e.message).toContain('502')
  })

  it('maps network failures to network_error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const e = (await apiFetch('/x').catch((x: unknown) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e.code).toBe('network_error')
    expect(e.status).toBe(0)
  })

  it('returns undefined for 204', async () => {
    stubFetch(() => new Response(null, { status: 204 }))
    await expect(apiFetch('/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})

describe('fieldErrorsFromDetails', () => {
  it('ignores malformed details', () => {
    expect(fieldErrorsFromDetails(null)).toEqual({})
    expect(fieldErrorsFromDetails('nope')).toEqual({})
    expect(fieldErrorsFromDetails([{ foo: 1 }, null])).toEqual({})
  })
})

describe('fetchAllPages', () => {
  it('follows next_cursor until exhausted', async () => {
    const f = stubFetch((url) =>
      url.includes('cursor=p2') ? jsonResponse({ items: [3], next_cursor: null }) : jsonResponse({ items: [1, 2], next_cursor: 'p2' }),
    )
    await expect(fetchAllPages<number>('/things')).resolves.toEqual([1, 2, 3])
    expect(f).toHaveBeenCalledTimes(2)
  })
})
