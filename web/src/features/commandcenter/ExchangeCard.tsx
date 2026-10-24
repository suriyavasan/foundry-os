import { RotateCw } from 'lucide-react'
import type { CommandExchange } from '@/api/types'
import { Avatar } from '@/components/avatar'
import { Badge, Button } from '@/components/ui'
import { ActionChips } from '@/features/notes/NoteBadges'
import { exchangeChips, exchangeView, isLocalExchange, isOptimisticExchange, statusTone, STATUS_LABEL } from '@/lib/commandCenter'
import { formatRelative } from '@/lib/format'
import type { Panel } from '@/lib/panels'
import type { RefTitles } from '@/lib/notes'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { OutputView } from './OutputView'
import { ProgressLines } from './ProgressLines'

const WORKING = { token: '--live-working', dashed: false, pulse: true, dimmed: false }
const IDLE = { token: '--live-idle', dashed: false, pulse: false, dimmed: false }

function CofounderMark({ working }: { working: boolean }) {
  return <Avatar family="founder" size="sm" ring={working ? WORKING : IDLE} />
}

/**
 * One exchange: the input line, then what came back. Every status has something to show: structured output for a command,
 * "waiting", a live "working" state, the reply with the records it changed, or an explanation with a retry.
 */
export function ExchangeCard({
  exchange: ex,
  companyId,
  cofounderId,
  titles,
  now,
  onOpenPanel,
  onRetry,
  retrying,
}: {
  exchange: CommandExchange
  companyId: string
  cofounderId: string | null
  titles?: RefTitles
  now: number
  onOpenPanel: (p: Panel) => void
  onRetry: (e: CommandExchange) => void
  retrying: boolean
}) {
  const v = exchangeView(ex, now)
  const chips = exchangeChips(ex, titles)
  const optimistic = isOptimisticExchange(ex)
  return (
    <li data-testid="exchange" data-status={ex.status} data-kind={ex.input_kind} className="space-y-2 px-3.5 py-3">
      <div className="flex items-start gap-2">
        <span aria-hidden className="mt-px font-mono text-[13px] leading-5 font-semibold text-accent-fg select-none">
          {ex.input_kind === 'command' ? '❯' : '›'}
        </span>
        <p className="min-w-0 flex-1 font-mono text-[12.5px] leading-5 break-words whitespace-pre-wrap text-fg line-clamp-4">{ex.input}</p>
        <div className="flex shrink-0 items-center gap-1.5">
          <Badge tone={statusTone(ex.status)}>{STATUS_LABEL[ex.status]}</Badge>
        </div>
      </div>

      <div className="pl-5" aria-live={ex.status === 'processing' || ex.status === 'pending' ? 'polite' : undefined}>
        {v.phase === 'output' && ex.output && <OutputView output={ex.output} onOpenPanel={onOpenPanel} />}
        {v.phase === 'output' && !ex.output && <p className="text-[12.5px] text-fg-3">{v.placeholder}</p>}

        {v.phase === 'waiting' && (
          <div role="status" className="flex items-center gap-2 text-[12.5px] text-fg-3">
            <span aria-hidden className="inline-flex gap-0.5">
              <span className="cos-live-dot size-1 rounded-full bg-fg-3" data-live="true" />
              <span className="cos-live-dot size-1 rounded-full bg-fg-3" data-live="true" style={{ animationDelay: '0.3s' }} />
              <span className="cos-live-dot size-1 rounded-full bg-fg-3" data-live="true" style={{ animationDelay: '0.6s' }} />
            </span>
            {optimistic ? 'Sending…' : v.placeholder}
          </div>
        )}

        {v.phase === 'working' && (
          <div role="status" className="flex items-start gap-2.5">
            <CofounderMark working />
            <div className="min-w-0 flex-1">
              <p className="text-[12.5px] font-medium text-fg">{optimistic ? 'Running…' : `${AI_COFOUNDER_LABEL} is working on it`}</p>
              {!optimistic && <ProgressLines companyId={companyId} runId={ex.run_id} agentId={cofounderId} since={ex.created_at} />}
            </div>
          </div>
        )}

        {v.phase === 'answered' && (
          <div className="flex items-start gap-2.5">
            <CofounderMark working={false} />
            <div className="min-w-0 flex-1 space-y-2">
              <p className="text-[11.5px] font-medium text-fg-3">{AI_COFOUNDER_LABEL}</p>
              {ex.output ? <OutputView output={ex.output} onOpenPanel={onOpenPanel} /> : <p className="text-[12.5px] text-fg-3">{v.placeholder}</p>}
              <ActionChips chips={chips} onOpen={onOpenPanel} label="Changed while answering" />
              {ex.note_id && (
                <button type="button" onClick={() => onOpenPanel({ kind: 'note', id: ex.note_id! })} className="text-[12px] text-accent-fg hover:underline">
                  Open the note
                </button>
              )}
            </div>
          </div>
        )}

        {v.phase === 'failed' && (
          <div className="space-y-2">
            {ex.output ? <OutputView output={{ ...ex.output, kind: 'error' }} onOpenPanel={onOpenPanel} /> : <p role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-fg">{v.placeholder}</p>}
            {v.canRetry && (
              <Button size="sm" icon={<RotateCw className="size-3.5" />} loading={retrying} onClick={() => onRetry(ex)}>
                Retry
              </Button>
            )}
            {isLocalExchange(ex) && <p className="text-[11.5px] text-fg-3">This did not reach the server, so nothing was run.</p>}
          </div>
        )}

        {v.stale && (
          <div className="mt-2 space-y-2">
            <p role="alert" className="text-[12.5px] text-warning">
              This is taking longer than usual. The system will retry on its own; you can retry now.
            </p>
            <Button size="sm" icon={<RotateCw className="size-3.5" />} loading={retrying} onClick={() => onRetry(ex)}>
              Retry now
            </Button>
          </div>
        )}
      </div>
      <p className="pl-5 text-[11.5px] text-fg-3" title={ex.created_at}>
        {formatRelative(ex.created_at, now)}
      </p>
    </li>
  )
}
