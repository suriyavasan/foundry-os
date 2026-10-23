import { describe, expect, it } from 'vitest'
import { splitRuntimeDetail } from './runtimes'

describe('splitRuntimeDetail', () => {
  it('pulls sandbox sentences out of the free-text detail', () => {
    const r = splitRuntimeDetail('Runtime python found. Docker daemon is not reachable; sandbox backend: none (restricted local mode). Version 0.1.')
    expect(r.sandbox).toEqual(['Docker daemon is not reachable;', 'sandbox backend: none (restricted local mode).'])
    expect(r.rest).toEqual(['Runtime python found.', 'Version 0.1.'])
  })
  it('handles empty detail', () => {
    expect(splitRuntimeDetail(null)).toEqual({ sandbox: [], rest: [] })
    expect(splitRuntimeDetail('')).toEqual({ sandbox: [], rest: [] })
  })
})
