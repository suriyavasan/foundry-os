import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { createContext, use, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  KNOWN_EVENT_TYPES,
  dedupeEffects,
  effectsForEvent,
  feedItemForEvent,
  parseCompanyEvent,
  patchAgentLive,
  patchOneAgentLive,
  type CacheEffect,
  type FeedItem,
} from '@/lib/streamEvents'
import { API_BASE } from './client'
import { qk } from './keys'
import type { Agent, CompanyEvent } from './types'

/**
 * - connecting:   first attempt in flight
 * - live:         SSE open; the cache is updated as events arrive
 * - reconnecting: dropped; retrying (resumes with Last-Event-ID / ?after=)
 * - unavailable:  the endpoint is missing or keeps failing; the UI polls every 10 s instead
 */
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'unavailable'

export interface StreamState {
  status: StreamStatus
  /** Newest-first feed built from stream events (the REST audit log seeds it elsewhere). */
  feed: FeedItem[]
  lastEventAt: number | null
}

/** The slice of EventSource the hook uses; injectable for tests. */
export interface EventSourceLike {
  readyState: number
  onopen: ((e: Event) => void) | null
  onerror: ((e: Event) => void) | null
  onmessage: ((e: MessageEvent) => void) | null
  addEventListener(type: string, listener: (e: MessageEvent) => void): void
  close(): void
}

export interface StreamOptions {
  createSource?: (url: string) => EventSourceLike
  pollMs?: number
  /** Consecutive failures (without an open) before falling back to polling. */
  failuresBeforePolling?: number
  flushMs?: number
}

const CLOSED = 2
const MAX_FEED = 100

const hasEventSource = () => typeof EventSource !== 'undefined'
const defaultCreate = (url: string): EventSourceLike => new EventSource(url) as unknown as EventSourceLike

/** Apply a batch of effects to the TanStack cache. */
export function applyEffects(qc: QueryClient, cid: string, effects: CacheEffect[]) {
  for (const e of dedupeEffects(effects)) {
    if (e.kind === 'invalidate') {
      void qc.invalidateQueries({ queryKey: e.key })
    } else {
      qc.setQueriesData({ queryKey: qk.agents(cid) }, (old: unknown) => {
        if (Array.isArray(old)) return patchAgentLive(old as Agent[], e.agentId, e.live)
        if (old && typeof old === 'object' && 'id' in old) return patchOneAgentLive(old as Agent, e.agentId, e.live)
        return old
      })
    }
  }
}

function invalidateLive(qc: QueryClient, cid: string) {
  for (const key of [
    qk.agents(cid),
    qk.tasksAll(cid),
    qk.runsAll(cid),
    qk.approvalsAll(cid),
    qk.artifactsAll(cid),
    qk.usageAll(cid),
    qk.activityAll(cid),
    qk.overview(cid),
    qk.schedules(cid),
    qk.skillsAll(cid),
  ]) {
    void qc.invalidateQueries({ queryKey: key })
  }
}

/** State starts fresh per company: mount it with `key={companyId}` (StreamProvider callers do). */
export function useCompanyStream(cid: string | undefined, options: StreamOptions = {}): StreamState {
  const qc = useQueryClient()
  const [status, setStatus] = useState<StreamStatus>(() => (options.createSource || hasEventSource() ? 'connecting' : 'unavailable'))
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [lastEventAt, setLastEventAt] = useState<number | null>(null)
  // Options are read through a ref so a new object identity each render never reconnects.
  const optsRef = useRef(options)
  useEffect(() => {
    optsRef.current = options
  })

  useEffect(() => {
    if (!cid) return
    const { createSource = defaultCreate, pollMs = 10_000, failuresBeforePolling = 2, flushMs = 250 } = optsRef.current

    let source: EventSourceLike | null = null
    let disposed = false
    let lastId = 0
    let failures = 0
    let wasLive = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let pollTimer: ReturnType<typeof setInterval> | undefined
    let flushTimer: ReturnType<typeof setTimeout> | undefined
    let pending: CacheEffect[] = []

    const flush = () => {
      flushTimer = undefined
      const batch = pending
      pending = []
      if (batch.length) applyEffects(qc, cid, batch)
    }

    const ingest = (ev: CompanyEvent) => {
      if (ev.id && ev.id <= lastId) return // replay of something already applied
      if (ev.id) lastId = ev.id
      pending.push(...effectsForEvent(cid, ev))
      if (!flushTimer) flushTimer = setTimeout(flush, flushMs)
      const item = feedItemForEvent(ev)
      if (item) setFeed((f) => (f.some((x) => x.key === item.key) ? f : [item, ...f].slice(0, MAX_FEED)))
      setLastEventAt(Date.now())
    }

    const startPolling = () => {
      if (pollTimer) return
      pollTimer = setInterval(() => invalidateLive(qc, cid), pollMs)
    }
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = undefined
    }

    const noteFailure = () => {
      failures++
      if (failures >= failuresBeforePolling) {
        setStatus('unavailable')
        startPolling()
      } else {
        setStatus('reconnecting')
      }
    }

    const connect = () => {
      if (disposed) return
      const url = `${API_BASE}/companies/${cid}/stream${lastId ? `?after=${lastId}` : ''}`
      const es = createSource(url)
      source = es

      es.onopen = () => {
        if (source !== es) return
        // After an outage, events may have been missed (or pruned): resync once.
        if (failures > 0 || wasLive) invalidateLive(qc, cid)
        failures = 0
        wasLive = true
        stopPolling()
        setStatus('live')
      }
      es.onerror = () => {
        if (disposed || source !== es) return
        noteFailure()
        if (es.readyState === CLOSED) {
          // A non-2xx response (e.g. 404 before the backend is upgraded) closes the EventSource for good.
          es.close()
          const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures, 5)) * (0.75 + Math.random() * 0.5)
          reconnectTimer = setTimeout(connect, delay)
        }
        // otherwise the browser is already retrying and will send Last-Event-ID itself
      }
      const onMessage = (named: string | null) => (e: MessageEvent) => {
        const ev = parseCompanyEvent(named, typeof e.data === 'string' ? e.data : '', e.lastEventId)
        if (ev) ingest(ev)
      }
      es.onmessage = onMessage(null)
      for (const t of KNOWN_EVENT_TYPES) es.addEventListener(t, onMessage(t))
    }

    if (optsRef.current.createSource || hasEventSource()) {
      connect()
    } else {
      // No EventSource in this environment: poll instead (status already starts as 'unavailable').
      startPolling()
    }

    return () => {
      disposed = true
      source?.close()
      source = null
      clearTimeout(reconnectTimer)
      clearTimeout(flushTimer)
      stopPolling()
    }
  }, [cid, qc])

  return useMemo(() => ({ status, feed, lastEventAt }), [status, feed, lastEventAt])
}

/* ------------------------------ shared via context ------------------------------ */

const StreamContext = createContext<StreamState>({ status: 'connecting', feed: [], lastEventAt: null })

export function StreamProvider({ companyId, children }: { companyId: string; children: ReactNode }) {
  const state = useCompanyStream(companyId)
  return <StreamContext value={state}>{children}</StreamContext>
}

export function useStream(): StreamState {
  return use(StreamContext)
}

export const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: 'Connecting',
  live: 'Live',
  reconnecting: 'Reconnecting',
  unavailable: 'Live updates unavailable',
}
