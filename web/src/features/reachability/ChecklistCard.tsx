import { Check, CircleDashed, TriangleAlert } from 'lucide-react'
import type { ConnectedAccount, EmailIdentity, ReachabilityStatus } from '@/api/types'
import { Badge, Button, Card, CardHeader } from '@/components/ui'
import type { Tone } from '@/components/ui/Badge'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { CHECKLIST_STATUS_LABEL, buildChecklist, checklistProgress, type ChecklistStatus, type ChecklistStep } from '@/lib/reachability'

const TONE: Record<ChecklistStatus, Tone> = { done: 'success', todo: 'neutral', attention: 'warning', unknown: 'neutral' }

function StatusIcon({ status }: { status: ChecklistStatus }) {
  if (status === 'done') return <Check className="size-3.5 text-success" aria-hidden />
  if (status === 'attention') return <TriangleAlert className="size-3.5 text-warning" aria-hidden />
  return <CircleDashed className="size-3.5 text-fg-3" aria-hidden />
}

/**
 * "Your one-time steps": what only you can do (connect accounts, set a password), each with what the AI Co-founder
 * does on its own afterwards. Status is read from the real accounts, never ticked by hand.
 */
export function ChecklistCard({ status, accounts, email, canManage }: { status: ReachabilityStatus | null; accounts: ConnectedAccount[] | null; email?: EmailIdentity | null; canManage: boolean }) {
  const { openConnectAccount, openPanel } = useCanvas()
  const steps = buildChecklist({ reachability: status, accounts, email })
  const progress = checklistProgress(steps)

  function act(s: ChecklistStep) {
    if (s.action.kind === 'connect') openConnectAccount(s.action.provider)
    else openPanel({ kind: s.action.panel })
  }

  return (
    <Card aria-label="Your one-time steps">
      <CardHeader
        title="Your one-time steps"
        description="You connect each service once. Company OS never creates accounts for you; the AI Co-founder takes it from there."
        actions={
          <Badge tone={progress.complete ? 'success' : 'neutral'}>
            {progress.done} of {progress.total} done
          </Badge>
        }
      />
      <ol className="divide-y divide-line">
        {steps.map((s) => (
          <li key={s.id} data-step={s.id} data-status={s.status} className="flex items-start gap-3 px-4 py-3">
            <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full border border-line bg-raised">
              <StatusIcon status={s.status} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-[13px] font-medium text-fg">{s.title}</h3>
                {s.optional && <Badge tone="neutral">Optional</Badge>}
                <Badge tone={TONE[s.status]}>{CHECKLIST_STATUS_LABEL[s.status]}</Badge>
              </div>
              <p className="mt-0.5 text-[12.5px] text-fg-2">{s.description}</p>
              <p className="mt-1 text-[12px] text-fg-3">
                <span className="font-medium text-fg-2">Then the AI Co-founder:</span> {s.after}
              </p>
            </div>
            {s.status !== 'done' && s.status !== 'unknown' && canManage && (
              <Button size="sm" variant={s.status === 'attention' ? 'secondary' : 'primary'} onClick={() => act(s)}>
                {s.action.kind === 'connect' ? (s.status === 'attention' ? 'Reconnect' : 'Connect') : 'Open Account'}
              </Button>
            )}
          </li>
        ))}
      </ol>
    </Card>
  )
}
