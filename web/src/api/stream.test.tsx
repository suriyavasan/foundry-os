import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeAgent } from '@/test/factories'
import { qk } from './keys'
import { useCompanyStream, type EventSourceLike } from './stream'
import type { Agent } from './types'

class FakeSource implements EventSourceLike {
  static all: FakeSource[] = []
  readyState = 0
  onopen: ((e: Event) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  listeners = new Map<string, Array<(e: MessageEvent) => void>>()
  closed = false
  constructor(public url: string) {
    FakeSource.all.push(this)
  }
  addEventListener(type: string, l: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l])
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
  open() {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }
  fail(closed: boolean) {
    this.readyState = closed ? 2 : 0
    this.onerror?.(new Event('error'))
  }
  emit(type: string, data: unknown, id: number) {
    const e = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: String(id) })
    for (const l of this.listeners.get(type) ?? []) l(e)
  }
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  const hook = renderHook(
    () =>
      useCompanyStream('c1', {
        createSource: (url) => new FakeSource(url),
        pollMs: 10_000,
        flushMs: 10,
      }),
    { wrapper },
  )
  return { qc, ...hook }
}

describe('useCompanyStream', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeSource.all = []
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('connects to the company stream and reports live once open', () => {
    const { result } = setup()
    expect(result.current.status).toBe('connecting')
    expect(FakeSource.all[0]!.url).toBe('/api/companies/c1/stream')
    act(() => FakeSource.all[0]!.open())
    expect(result.current.status).toBe('live')
  })

  it('patches the cached agent list from agent.activity without refetching', () => {
    const { qc, result } = setup()
    const agent = makeAgent({ id: 'a1' })
    qc.setQueryData(qk.agentList('c1', {}), [agent])
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => FakeSource.all[0]!.open())
    spy.mockClear()
    act(() => {
      FakeSource.all[0]!.emit('agent.activity', { agent_id: 'a1', state: 'working', summary: 'Drafting', current_task: { id: 't1', title: 'Spec' } }, 5)
      vi.advanceTimersByTime(20)
    })
    const list = qc.getQueryData<Agent[]>(qk.agentList('c1', {}))!
    expect(list[0]!.live).toMatchObject({ state: 'working', summary: 'Drafting' })
    expect(spy).not.toHaveBeenCalled()
    expect(result.current.feed[0]?.text).toBe('Drafting')
  })

  it('invalidates task queries on task.updated, once for a burst', () => {
    const { qc } = setup()
    act(() => FakeSource.all[0]!.open())
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => {
      FakeSource.all[0]!.emit('task.updated', { status: 'done' }, 1)
      FakeSource.all[0]!.emit('task.updated', { status: 'done' }, 2)
      vi.advanceTimersByTime(20)
    })
    const taskCalls = spy.mock.calls.filter((c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(qk.tasksAll('c1')))
    expect(taskCalls).toHaveLength(1)
  })

  it('ignores events it has already applied (replay after reconnect)', () => {
    const { result } = setup()
    act(() => FakeSource.all[0]!.open())
    act(() => {
      FakeSource.all[0]!.emit('task.created', { title: 'A' }, 3)
      FakeSource.all[0]!.emit('task.created', { title: 'A' }, 3)
    })
    expect(result.current.feed).toHaveLength(1)
  })

  it('degrades to polling every 10 s when the endpoint keeps failing (e.g. 404)', () => {
    const { qc, result } = setup()
    act(() => FakeSource.all[0]!.fail(true))
    expect(result.current.status).toBe('reconnecting')
    // backoff then a second attempt, which also fails
    act(() => vi.advanceTimersByTime(5000))
    expect(FakeSource.all.length).toBeGreaterThanOrEqual(2)
    act(() => FakeSource.all[1]!.fail(true))
    expect(result.current.status).toBe('unavailable')
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => vi.advanceTimersByTime(10_000))
    expect(spy.mock.calls.some((c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(qk.tasksAll('c1')))).toBe(true)
  })

  it('resumes with ?after=<last id> after a reconnect and stops polling once live again', () => {
    const { qc, result } = setup()
    act(() => FakeSource.all[0]!.open())
    act(() => FakeSource.all[0]!.emit('task.created', { title: 'A' }, 41))
    act(() => FakeSource.all[0]!.fail(true))
    act(() => vi.advanceTimersByTime(5000))
    const second = FakeSource.all[1]!
    expect(second.url).toBe('/api/companies/c1/stream?after=41')
    act(() => second.open())
    expect(result.current.status).toBe('live')
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => vi.advanceTimersByTime(30_000))
    expect(spy).not.toHaveBeenCalled() // no polling while live
  })

  it('closes the connection on unmount', () => {
    const { unmount } = setup()
    const src = FakeSource.all[0]!
    unmount()
    expect(src.closed).toBe(true)
  })
})
