import { useMemo } from 'react'
import { useRunEvents } from '@/api'
import { useStream } from '@/api/stream'
import { describeRunEvent } from '@/lib/runEvents'

const SKIP = new Set(['usage', 'cost', 'heartbeat', 'token', 'tokens'])
const LINES = 3

export interface ProgressLine {
  key: string
  text: string
}

/**
 * What the AI Co-founder is doing right now while it works on an exchange: the newest lines of its run (read from the run's
 * events, which the stream keeps fresh), or, until the run is known, the newest activity of that agent from the stream.
 * Nothing is invented: with no lines yet the list is empty.
 */
export function useProgressLines(companyId: string, runId: string | null, agentId: string | null, since: string): ProgressLine[] {
  const events = useRunEvents(companyId, runId ?? undefined, true)
  const stream = useStream()
  return useMemo(() => {
    if (runId && events.data && events.data.length > 0) {
      return events.data
        .filter((e) => !SKIP.has(e.type))
        .map((e) => ({ key: `r${e.seq}`, text: describeRunEvent(e) }))
        .filter((l) => l.text.trim() !== '')
        .slice(-LINES)
    }
    return stream.feed
      .filter((f) => agentId && f.agentId === agentId && f.at >= since && (f.kind === 'agent' || f.kind === 'run' || f.kind === 'decision'))
      .slice(0, LINES)
      .reverse()
      .map((f) => ({ key: f.key, text: f.text }))
  }, [runId, events.data, stream.feed, agentId, since])
}

/** The newest few lines, oldest first. */
export function ProgressLines({ lines }: { lines: ProgressLine[] }) {
  return (
    <ul aria-label="Live progress" className="mt-1.5 space-y-0.5">
      {lines.length === 0 ? (
        <li className="text-[12px] text-fg-3">Starting up…</li>
      ) : (
        lines.map((l) => (
          <li key={l.key} className="cos-feed-item truncate text-[12px] text-fg-3">
            {l.text}
          </li>
        ))
      )}
    </ul>
  )
}
