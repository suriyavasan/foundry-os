import { ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useCancelRun, useRun, useRunEvents, errorMessage } from '@/api'
import type { RunEvent, TaskRun } from '@/api/types'
import { Button, Card, CardBody, CardHeader, Chip, ConfirmDialog, CostKindBadge, DefinitionList, EmptyState, ErrorState, LoadingState, useToast } from '@/components/ui'
import { useAgentMap, useTick } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { RunStatusBadge } from '@/features/shared/badges'
import { formatDuration } from '@/lib/dates'
import { formatDateTime, formatTokens } from '@/lib/format'
import { formatCost } from '@/lib/money'
import { describeGraphStep, describeRunEvent, isGraphStep } from '@/lib/runEvents'
import { isRunActive } from '@/lib/taskStatus'

const time = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

/** One run: status, usage with cost provenance, and a live-tailing event log. */
export function RunDetail({ companyId, runId }: { companyId: string; runId: string }) {
  const run = useRun(companyId, runId)
  const active = run.data ? isRunActive(run.data.status) : false
  const now = useTick(active ? 1000 : 60_000)
  const events = useRunEvents(companyId, runId, active)
  const { map } = useAgentMap(companyId)

  if (run.isPending) return <Card><LoadingState rows={5} label="Loading run" /></Card>
  if (run.isError) return <Card><ErrorState error={run.error} onRetry={() => void run.refetch()} retrying={run.isFetching} /></Card>
  const r = run.data

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              Run #{r.attempt_number}
              <RunStatusBadge status={r.status} />
              {r.cancel_requested && active && <Chip>cancel requested</Chip>}
            </span>
          }
          actions={active ? <CancelRun companyId={companyId} runId={r.id} /> : undefined}
        />
        <CardBody>
          <DefinitionList
            items={[
              { label: 'Agent', value: <AgentChip agentId={r.agent_id} agents={map} size="sm" /> },
              { label: 'Runtime', value: <Chip>{r.runtime}</Chip> },
              { label: 'Queued', value: formatDateTime(r.queued_at) },
              { label: 'Started', value: formatDateTime(r.started_at) },
              { label: 'Finished', value: r.finished_at ? formatDateTime(r.finished_at) : active ? 'Running…' : '—' },
              { label: 'Duration', value: r.started_at ? formatDuration((r.finished_at ? new Date(r.finished_at).getTime() : now) - new Date(r.started_at).getTime()) : '—' },
              { label: 'Tokens in / out', value: `${formatTokens(r.input_tokens)} / ${formatTokens(r.output_tokens)}` },
              {
                label: 'Cost',
                value: (
                  <span className="inline-flex items-center gap-2">
                    {formatCost(r.cost_micros, r.cost_kind)}
                    <CostKindBadge kind={r.cost_kind} />
                  </span>
                ),
              },
              ...(r.exit_reason ? [{ label: 'Exit reason', value: r.exit_reason }] : []),
            ]}
          />
          {r.error && (
            <div role="alert" className="mt-3 rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
              {r.error.code && <strong className="font-semibold">{r.error.code}: </strong>}
              {r.error.message ?? 'The run failed.'}
            </div>
          )}
        </CardBody>
      </Card>

      <EventLog events={events.data ?? []} loading={events.isPending} error={events.isError ? events.error : null} onRetry={() => void events.refetch()} live={active} run={r} />
    </div>
  )
}

function CancelRun({ companyId, runId }: { companyId: string; runId: string }) {
  const cancel = useCancelRun(companyId)
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <>
      <Button size="sm" variant="danger" onClick={() => { setError(null); setOpen(true) }}>
        Cancel run
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Cancel this run?"
        description="The agent is asked to stop at the next safe point. Work already done is kept, the budget it reserved is released, and the task may be retried according to its attempt limit."
        confirmLabel="Cancel run"
        loading={cancel.isPending}
        error={error}
        onConfirm={async () => {
          try {
            await cancel.mutateAsync(runId)
            toast.success('Cancel requested')
            setOpen(false)
          } catch (e) {
            setError(errorMessage(e))
          }
        }}
      />
    </>
  )
}

function EventLog({ events, loading, error, onRetry, live, run }: { events: RunEvent[]; loading: boolean; error: unknown; onRetry: () => void; live: boolean; run: TaskRun }) {
  const box = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [events.length, follow])

  return (
    <Card>
      <CardHeader
        title="Event log"
        description={live ? 'Live. New events appear as the agent works.' : `${events.length} event${events.length === 1 ? '' : 's'}`}
        actions={
          live && (
            <Button size="sm" variant={follow ? 'secondary' : 'ghost'} aria-pressed={follow} icon={<ChevronDown className="size-3.5" />} onClick={() => setFollow((f) => !f)}>
              Follow
            </Button>
          )
        }
      />
      {loading ? (
        <LoadingState rows={4} label="Loading events" />
      ) : error ? (
        <ErrorState compact error={error} onRetry={onRetry} />
      ) : events.length === 0 ? (
        <EmptyState compact title={run.status === 'queued' ? 'Waiting to start' : 'No events recorded'} description="Events appear here as the runtime reports them." />
      ) : (
        <div ref={box} onScroll={(e) => { const el = e.currentTarget; setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24) }} className="max-h-[420px] overflow-auto" role="log" aria-live={live ? 'polite' : 'off'} aria-label="Run events">
          <ol className="divide-y divide-line font-mono text-[12px]">
            {events.map((e) => (
              <li key={e.seq} className="px-3 py-1.5">
                <div className="flex items-start gap-2">
                  <span className="w-8 shrink-0 text-right text-fg-3 tabular-nums">{e.seq}</span>
                  <time dateTime={e.occurred_at} className="shrink-0 text-fg-3">
                    {time.format(new Date(e.occurred_at))}
                  </time>
                  {isGraphStep(e) ? (
                    <span data-testid="graph-step" className="min-w-0 flex-1 break-words text-fg-2">
                      <span aria-hidden className="mr-1.5 text-fg-3">
                        ›
                      </span>
                      {describeGraphStep(e)}
                    </span>
                  ) : (
                    <>
                      <Chip className="shrink-0">{e.type}</Chip>
                      <span className="min-w-0 flex-1 break-words whitespace-pre-wrap text-fg">{describeRunEvent(e)}</span>
                    </>
                  )}
                </div>
                {Object.keys(e.payload ?? {}).length > 0 && (
                  <details className="mt-1 ml-10">
                    <summary className="cursor-pointer text-[11.5px] text-fg-3 hover:text-fg-2">payload</summary>
                    <pre className="mt-1 max-h-48 overflow-auto rounded-sm bg-raised p-2 text-[11.5px] text-fg-2">{JSON.stringify(e.payload, null, 2)}</pre>
                  </details>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  )
}
