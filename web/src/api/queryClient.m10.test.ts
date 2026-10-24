import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { qk } from './keys'
import { createQueryClient, isAiSetupRequired } from './queryClient'

describe('ai_setup_required', () => {
  it('is recognised only as the 409 with that code', () => {
    expect(isAiSetupRequired(new ApiError(409, 'ai_setup_required', 'Connect an AI first'))).toBe(true)
    expect(isAiSetupRequired(new ApiError(409, 'conflict', 'x'))).toBe(false)
    expect(isAiSetupRequired(new ApiError(403, 'ai_setup_required', 'x'))).toBe(false)
    expect(isAiSetupRequired(new Error('x'))).toBe(false)
  })

  it('a refused request re-reads the AI setup of every company, so the gate takes over', async () => {
    const qc = createQueryClient()
    qc.setQueryData(qk.aiSetup('c1'), { ready: true })
    qc.setQueryData(qk.notes('c1'), [])
    const spy = vi.spyOn(qc, 'invalidateQueries')
    await qc
      .fetchQuery({ queryKey: ['company', 'c1', 'probe'], queryFn: () => Promise.reject(new ApiError(409, 'ai_setup_required', 'Connect an AI first')), retry: false })
      .catch(() => undefined)
    const predicates = spy.mock.calls.map((c) => c[0] as { predicate?: (q: { queryKey: readonly unknown[] }) => boolean })
    const hit = predicates.find((p) => p.predicate)
    expect(hit).toBeTruthy()
    expect(hit!.predicate!({ queryKey: qk.aiSetup('c1') })).toBe(true)
    expect(hit!.predicate!({ queryKey: qk.aiSetup('other') })).toBe(true)
    expect(hit!.predicate!({ queryKey: qk.notes('c1') })).toBe(false)
  })
})
