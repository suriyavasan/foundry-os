import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeNote } from '@/test/factories'
import { qk } from './keys'
import { useCompanyStream, type EventSourceLike } from './stream'
import type { ClientNote } from './types'

class FakeSource implements EventSourceLike {
  static all: FakeSource[] = []
  readyState = 0
  onopen: ((e: Event) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  listeners = new Map<string, Array<(e: MessageEvent) => void>>()
  constructor(public url: string) {
    FakeSource.all.push(this)
  }
  addEventListener(type: string, l: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l])
  }
  close() {
    this.readyState = 2
  }
  open() {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }
  emit(type: string, data: unknown, id: number) {
    const e = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: String(id) })
    for (const l of this.listeners.get(type) ?? []) l(e)
  }
}

describe('useCompanyStream with Founder’s notes', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeSource.all = []
  })
  afterEach(() => vi.useRealTimers())

  it('refetches the notes list on note.created / note.triaged / note.replied (and the open thread)', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    renderHook(() => useCompanyStream('c1', { createSource: (u) => new FakeSource(u), flushMs: 10 }), { wrapper })
    qc.setQueryData<ClientNote[]>(qk.notes('c1'), [makeNote({ id: 'n1' })])
    qc.setQueryData(qk.note('c1', 'n1'), { ...makeNote({ id: 'n1' }), replies: [] })
    const src = FakeSource.all[0]!
    act(() => src.open())

    for (const [type, payload, id] of [
      ['note.created', { note_id: 'n1' }, 1],
      ['note.triaged', { note_id: 'n1', category: 'idea' }, 2],
      ['note.replied', { note_id: 'n1' }, 3],
    ] as const) {
      const spy = vi.spyOn(qc, 'invalidateQueries')
      act(() => {
        src.emit(type, payload, id)
        vi.advanceTimersByTime(20)
      })
      const hit = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey))
      expect(hit, type).toContain(JSON.stringify(qk.notesAll('c1')))
      spy.mockRestore()
    }
    expect(qc.getQueryState(qk.notes('c1'))?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.note('c1', 'n1'))?.isInvalidated).toBe(true)
  })
})
