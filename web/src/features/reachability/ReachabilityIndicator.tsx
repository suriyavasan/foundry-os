import clsx from 'clsx'
import { Globe } from 'lucide-react'
import { useReachability } from '@/api'
import { Tooltip } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { isMissingEndpoint } from '@/lib/auth'
import { globeModel, type GlobeTone } from '@/lib/reachability'
import { CopyButton } from './shared'

const ICON: Record<GlobeTone, string> = { neutral: 'text-fg-3', info: 'text-info', success: 'text-success', warning: 'text-warning' }

/**
 * Top bar: a globe saying how far the app is reachable (local only, local network, public), the active address on
 * hover, and a copy button for it. Not drawn while loading or when reachability is not built yet: nothing honest to show.
 */
export function ReachabilityIndicator() {
  const { openPanel } = useCanvas()
  const q = useReachability()
  if (q.isPending || (q.isError && isMissingEndpoint(q.error))) return null
  const g = globeModel(q.data ?? null)

  return (
    <div className="flex items-center" data-testid="reachability-indicator" data-mode={g.mode ?? 'unknown'}>
      <Tooltip content={g.tooltip}>
        <button
          type="button"
          onClick={() => openPanel({ kind: 'reachability' })}
          aria-label={`Reachability: ${g.label}${g.url ? `, ${g.url}` : ''}. Open details`}
          className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[12.5px] text-fg-2 transition-colors hover:bg-hover hover:text-fg"
        >
          <Globe className={clsx('size-4', ICON[g.tone])} aria-hidden />
          <span className="hidden xl:inline">{g.label}</span>
        </button>
      </Tooltip>
      {g.url && <CopyButton value={g.url} label="Copy active address" />}
    </div>
  )
}
