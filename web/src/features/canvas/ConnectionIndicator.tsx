import clsx from 'clsx'
import { STREAM_LABEL, useStream, type StreamStatus } from '@/api/stream'
import { Tooltip } from '@/components/ui'

const DOT: Record<StreamStatus, string> = {
  connecting: 'bg-neutral',
  live: 'bg-success',
  reconnecting: 'bg-warning',
  unavailable: 'bg-warning',
}
const HELP: Record<StreamStatus, string> = {
  connecting: 'Connecting to the live event stream.',
  live: 'Receiving updates the moment they happen.',
  reconnecting: 'The connection dropped. Reconnecting and resuming where it left off.',
  unavailable: 'The live event stream is not available. The canvas refreshes every 10 seconds instead.',
}

/** Small connection-status indicator for the top bar. */
export function ConnectionIndicator() {
  const { status } = useStream()
  return (
    <Tooltip content={HELP[status]}>
      <span role="status" aria-live="polite" className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] text-fg-2">
        <span aria-hidden data-live={status === 'live'} className={clsx('cos-live-dot inline-block size-1.5 rounded-full', DOT[status])} />
        <span className="hidden lg:inline">{STREAM_LABEL[status]}</span>
        <span className="sr-only lg:hidden">{STREAM_LABEL[status]}</span>
        {status === 'unavailable' && <span className="hidden text-fg-3 xl:inline">· polling 10s</span>}
      </span>
    </Tooltip>
  )
}
