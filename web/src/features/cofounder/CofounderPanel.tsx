import { Zap } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, useCofounderSummary, useWakeCofounder } from '@/api'
import type { CofounderSummary } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { Badge, Boundary, Button, Card, CardBody, CardHeader, EmptyState, Tooltip, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { AgentChip, CreatorChip } from '@/features/shared/AgentChip'
import { RunStatusBadge } from '@/features/shared/badges'
import { formatDateTime, formatRelative } from '@/lib/format'
import { SEVERITY_LABEL, SEVERITY_TONE, alertPanel, sortAlerts } from '@/lib/alerts'
import { buildPlanTree, wakeResultText } from '@/lib/cofounder'
import { refTypeLabel } from '@/lib/refs'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { PlanTree } from './PlanTree'

/** `?panel=cofounder`: what the AI Co-founder is working toward, why, and what is in its way. */
export function CofounderPanel() {
  const { companyId } = useCanvas()
  const q = useCofounderSummary(companyId)
  const wake = useWakeCofounder(companyId)
  const toast = useToast()
  const { agents } = useAgentMap(companyId)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const agentId = q.data?.agent_id
  const agent = (agentId ? agents.find((a) => a.id === agentId) : undefined) ?? findCofounder(agents)
  const pending = wake.isPending || !!q.data?.wake_pending
  const noAgent = q.isSuccess && !q.data.agent_id && !agent

  function onWake() {
    setResult(null)
    wake.mutate(undefined, {
      onSuccess: (r) => {
        const text = wakeResultText(r)
        setResult({ ok: true, text })
        toast.success(text)
      },
      onError: (e) => {
        setResult({ ok: false, text: errorMessage(e) })
        toast.error(errorMessage(e))
      },
    })
  }

  return (
    <PanelFrame
      title={AI_COFOUNDER_LABEL}
      subtitle="Goals, plan, decisions and blockers"
      leading={agent ? <AgentAvatar agent={agent} size="md" /> : <Avatar family="founder" size="md" />}
      actions={
        <Tooltip content={noAgent ? `There is no active ${AI_COFOUNDER_LABEL} to wake.` : pending ? 'A planning run is already waiting.' : 'Start a planning run now instead of waiting for the next trigger.'}>
          <Button size="sm" variant="primary" icon={<Zap className="size-3.5" />} loading={wake.isPending} disabled={!q.isSuccess || pending || noAgent} onClick={onWake}>
            {q.data?.wake_pending && !wake.isPending ? 'Wake pending' : 'Wake now'}
          </Button>
        </Tooltip>
      }
    >
      <div className="space-y-4">
        {result && (
          <p role="status" className={'rounded-md border px-3 py-2 text-[13px] ' + (result.ok ? 'border-success/40 bg-success-subtle text-fg' : 'border-danger/40 bg-danger-subtle text-danger')}>
            {result.text}
          </p>
        )}
        <Boundary
          query={q}
          compact={false}
          rows={8}
          milestone="M5"
          unavailableTitle={`The ${AI_COFOUNDER_LABEL} view is not built yet`}
          unavailableText="Its plan, decisions and blockers arrive with the autonomous operating model."
        >
          {(s) => <SummaryBody s={s} />}
        </Boundary>
      </div>
    </PanelFrame>
  )
}

function SummaryBody({ s }: { s: CofounderSummary }) {
  const { companyId, openPanel } = useCanvas()
  const { map } = useAgentMap(companyId)
  const tree = useMemo(() => buildPlanTree(s), [s])
  const alerts = useMemo(() => sortAlerts(s.alerts ?? []), [s.alerts])
  const agent = s.agent_id ? map.get(s.agent_id) : undefined

  return (
    <>
      <Card>
        <CardHeader
          title="Right now"
          description={agent ? `${agent.name}${agent.live?.summary ? ` · ${agent.live.summary}` : ''}` : undefined}
          actions={
            s.unprocessed_notes > 0 ? (
              <button type="button" onClick={() => openPanel({ kind: 'notes' })} className="text-[12.5px] text-accent-fg hover:underline">
                {s.unprocessed_notes} note{s.unprocessed_notes === 1 ? '' : 's'} waiting to be read
              </button>
            ) : undefined
          }
        />
        <CardBody>
          {s.plan?.summary ? (
            <>
              <p className="text-[13.5px] whitespace-pre-wrap text-fg">{s.plan.summary}</p>
              {s.plan.updated_at && <p className="mt-1.5 text-[12px] text-fg-3">Updated {formatRelative(s.plan.updated_at)}</p>}
            </>
          ) : (
            <p className="text-[13px] text-fg-3">The {AI_COFOUNDER_LABEL} has not written a plan summary yet.</p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Active plan" description="Goals, the projects that deliver them, and what is still open" />
        {tree.length === 0 ? <EmptyState title="No goals yet" description={`Write a note, or set a strategic goal. The ${AI_COFOUNDER_LABEL} turns direction into goals and projects.`} /> : <PlanTree nodes={tree} agents={map} onOpen={openPanel} />}
      </Card>

      <Card>
        <CardHeader title="Recent decisions" description="Calls made, with the reason" />
        {(s.decisions ?? []).length === 0 ? (
          <EmptyState compact title="No decisions recorded yet" />
        ) : (
          <ul className="divide-y divide-line">
            {s.decisions.map((d) => (
              <li key={d.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-[13px] font-medium text-fg">{d.title}</h3>
                  <time dateTime={d.created_at} title={formatDateTime(d.created_at)} className="shrink-0 text-[12px] text-fg-3">
                    {formatRelative(d.created_at)}
                  </time>
                </div>
                {d.rationale ? <p className="mt-1 text-[12.5px] whitespace-pre-wrap text-fg-2">{d.rationale}</p> : <p className="mt-1 text-[12.5px] text-fg-3">No rationale was recorded.</p>}
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[12px] text-fg-3">
                  <CreatorChip type={d.made_by_type} id={d.made_by_id} agents={map} />
                  {d.related_task_id && (
                    <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: d.related_task_id! })}>
                      Related task
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Blockers" description="Work that cannot move" />
          {(s.blockers ?? []).length === 0 ? (
            <EmptyState compact title="Nothing is blocked" />
          ) : (
            <ul className="divide-y divide-line">
              {s.blockers.map((b) => (
                <li key={b.task_id} className="px-4 py-2.5">
                  <button type="button" onClick={() => openPanel({ kind: 'task', id: b.task_id })} className="text-left text-[13px] font-medium text-fg hover:underline">
                    {b.title}
                  </button>
                  {b.reason && <p className="mt-0.5 text-[12.5px] text-fg-2">{b.reason}</p>}
                  <div className="mt-1 flex items-center gap-2 text-[12px] text-fg-3">
                    <AgentChip agentId={b.agent_id} agents={map} emptyLabel="Unassigned" />
                    {b.since && <span>blocked {formatRelative(b.since)}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Open alerts" />
          {alerts.length === 0 ? (
            <EmptyState compact title="No open alerts" />
          ) : (
            <ul className="divide-y divide-line">
              {alerts.map((a) => {
                const panel = alertPanel(a)
                return (
                  <li key={a.id} className="flex items-start gap-2 px-4 py-2.5">
                    <Badge tone={SEVERITY_TONE[a.severity]}>{SEVERITY_LABEL[a.severity] ?? a.severity}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] text-fg">{a.title}</p>
                      {a.body && <p className="text-[12px] text-fg-3">{a.body}</p>}
                    </div>
                    {panel && (
                      <button type="button" className="shrink-0 text-[12.5px] text-accent-fg hover:underline" onClick={() => openPanel(panel)}>
                        {a.related_type ? refTypeLabel(a.related_type) : 'Open'}
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title="Last run" />
        <CardBody>
          {s.last_run ? (
            <div className="flex flex-wrap items-center gap-3 text-[13px]">
              <RunStatusBadge status={s.last_run.status} />
              <span className="text-fg-2">
                {s.last_run.finished_at ? `Finished ${formatRelative(s.last_run.finished_at)}` : s.last_run.started_at ? `Started ${formatRelative(s.last_run.started_at)}` : 'Not started yet'}
              </span>
              {s.last_run.summary && <span className="min-w-0 basis-full text-[12.5px] text-fg-3">{s.last_run.summary}</span>}
              <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: s.last_run!.task_id, run: s.last_run!.run_id })}>
                Open run detail
              </button>
            </div>
          ) : (
            <p className="text-[13px] text-fg-3">The {AI_COFOUNDER_LABEL} has not run yet.</p>
          )}
        </CardBody>
      </Card>
    </>
  )
}
