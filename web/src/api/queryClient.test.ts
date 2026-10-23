import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { qk } from './keys'
import { createQueryClient, isUnauthenticated, shouldRetry } from './queryClient'

const err = (status: number) => new ApiError(status, 'x', 'x')

describe('shouldRetry', () => {
  it('never retries client errors, including 401', () => {
    for (const s of [400, 401, 403, 404, 409, 422]) expect(shouldRetry(0, err(s))).toBe(false)
  })
  it('retries transient failures twice', () => {
    expect(shouldRetry(0, err(502))).toBe(true)
    expect(shouldRetry(1, new Error('network'))).toBe(true)
    expect(shouldRetry(2, err(500))).toBe(false)
  })
})

describe('createQueryClient', () => {
  it('is configured with that retry rule', () => {
    expect(createQueryClient().getDefaultOptions().queries?.retry).toBe(shouldRetry)
  })

  it('re-checks /me when any other query is rejected with 401, so the login gate takes over', async () => {
    const qc = createQueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    await qc.fetchQuery({ queryKey: ['company', 'c1', 'tasks'], queryFn: () => Promise.reject(err(401)), retry: false }).catch(() => undefined)
    expect(spy).toHaveBeenCalledWith({ queryKey: qk.me })
  })

  it('does not loop when /me itself is the 401, and ignores other errors', async () => {
    const qc = createQueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    await qc.fetchQuery({ queryKey: qk.me, queryFn: () => Promise.reject(err(401)), retry: false }).catch(() => undefined)
    await qc.fetchQuery({ queryKey: ['x'], queryFn: () => Promise.reject(err(500)), retry: false }).catch(() => undefined)
    expect(spy).not.toHaveBeenCalled()
  })

  it('recognises 401s', () => {
    expect(isUnauthenticated(err(401))).toBe(true)
    expect(isUnauthenticated(err(403))).toBe(false)
    expect(isUnauthenticated(new Error('x'))).toBe(false)
  })
})
