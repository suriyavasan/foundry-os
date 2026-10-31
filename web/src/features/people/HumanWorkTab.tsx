import { CheckCircle2, HandHelping, UserRoundX } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, errorMessage, isNotDeployed, useClaimHumanWork, useDeclineHumanWork, useHumanWork, useMyMember } from '@/api'
import type { HumanWorkItem } from '@/api/types'
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorState, Field, FormError, LoadingState, NotAvailable, Segmented, Textarea, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useCompanyRole, useTick } from '@/features/canvas/hooks'
import { ActDialog } from '@/features/mywork/MyWorkPanel'
import { formatDateTime, formatRelative } from '@/lib/format'
import { HUMAN_WORK_VIEWS, canDeclineHumanWork, canTakeHumanWork, categoryLabel, dueText, filterHumanWork, holderText, sortHumanWork, type HumanWorkView } from '@/lib/humanWork'

type View = HumanWorkView | 'done'

/**
 * Tasks an AI employee asked a person to do. Open ones come overdue first; anyone but a viewer can take one from the pool ("I'll do it"),
 * the person who has it completes it with a comment and files, and an owner or admin can decline it with a reason the employee reads.
 * The title and instructions were written by an AI employee: plain text.
 */
export function HumanWorkTab({ companyId }: { companyId: string }) {
  const role = useCompanyRole(companyId)
  const mine = useMyMember(companyId)
  const [view, setView] = useState<View>('all')
  const open = useHumanWork(companyId, { status: 'open' })
  const done = useHumanWork(companyId, { status: 'done', enabled: view === 'done' })
  const q = view === 'done' ? done : open
  const now = useTick(60_000)
  const items = useMemo(() => {
    const data = q.data ?? []
    return view === 'done' ? data : sortHumanWork(filterHumanWork(data, view, mine.memberId))
  }, [q.data, view, mine.memberId])

  let body
  if (q.isPending) body = <LoadingState rows={4} label="Loading human work" />
  else if (q.isError) {
    body = isNotDeployed(q.error) ? (
      <NotAvailable milestone="M22" title="Human work is not built yet" description="Requests from AI employees for something only a person can do arrive with M22." />
    ) : (
      <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
    )
  } else if (items.length === 0) {
    body = (
      <EmptyState
        title={view === 'done' ? 'Nothing finished yet' : view === 'mine' ? 'Nothing is assigned to you' : view === 'pool' ? 'The pool is empty' : 'No AI employee is waiting for a person'}
        description={view === 'done' ? 'Human work that people completed appears here.' : 'When an AI employee needs a signature, a call or an errand, it appears here and you are notified.'}
      />
    )
  } else {
    body = (
      <ul aria-label="Human work" className="divide-y divide-line">
        {items.map((i) => (
          <HumanWorkRow key={i.id} item={i} companyId={companyId} role={role} memberId={mine.memberId} now={now} finished={view === 'done'} />
        ))}
      </ul>
    )
  }

  return (
    <Card aria-label="Human work">
      <CardHeader
        title="Human work"
        description="Things an AI employee cannot do and asked a person for."
        actions={
          <Segmented
            label="Show"
            value={view}
            onChange={setView}
            options={[...HUMAN_WORK_VIEWS, { value: 'done' as const, label: 'Done' }]}
          />
        }
      />
      {body}
    </Card>
  )
}

function HumanWorkRow({ item: i, companyId, role, memberId, now, finished }: { item: HumanWorkItem; companyId: string; role: string | undefined; memberId: string | null; now: number; finished: boolean }) {
  const { openPanel } = useCanvas()
  const claim = useClaimHumanWork(companyId)
  const toast = useToast()
  const [acting, setActing] = useState(false)
  const [declining, setDeclining] = useState(false)
  const due = finished ? null : dueText(i.due_at, now)
  const isMine = !!memberId && i.assignee_ref === `member:${memberId}`
  const canComplete = !finished && !i.pool && (isMine || canDeclineHumanWork(role))
  const takeError = claim.error instanceof ApiError && claim.error.status === 409

  return (
    <li className="space-y-2 px-4 py-3" data-testid="human-work-item" data-overdue={i.overdue ? 'true' : 'false'}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <button type="button" onClick={() => openPanel({ kind: 'task', id: i.id })} className="block max-w-full text-left text-[13px] font-medium break-words text-fg hover:underline">
            {i.title}
          </button>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-3">
            <Badge tone="accent">{categoryLabel(i.category)}</Badge>
            {due && <Badge tone={due.tone}>{due.text}</Badge>}
            {!finished && <span>{holderText(i)}</span>}
            {i.requested_by_agent_name && <span>Asked by {i.requested_by_agent_name}</span>}
            <time dateTime={i.created_at}>{formatRelative(i.created_at)}</time>
            {i.blocking_task_ids.length > 0 && !finished && <span className="text-warning">Holding up {i.blocking_task_ids.length === 1 ? 'a task' : `${i.blocking_task_ids.length} tasks`}</span>}
          </div>
        </div>
        {!finished && (
          <div className="flex flex-wrap gap-1.5">
            {i.pool && canTakeHumanWork(role) && (
              <Button
                size="sm"
                variant="primary"
                icon={<HandHelping className="size-3.5" />}
                loading={claim.isPending}
                onClick={() =>
                  claim.mutate(i.id, {
                    onSuccess: () => toast.success('It is yours now'),
                    onError: (err) => toast.error(err instanceof ApiError && err.status === 409 ? 'Somebody already took it' : errorMessage(err, 'Could not take it')),
                  })
                }
              >
                I&apos;ll do it
              </Button>
            )}
            {canComplete && (
              <Button size="sm" variant="primary" icon={<CheckCircle2 className="size-3.5" />} aria-label={`Complete ${i.title}`} onClick={() => setActing(true)}>
                Complete
              </Button>
            )}
            {canDeclineHumanWork(role) && (
              <Button size="sm" variant="danger" icon={<UserRoundX className="size-3.5" />} aria-label={`Decline ${i.title}`} onClick={() => setDeclining(true)}>
                Decline
              </Button>
            )}
          </div>
        )}
      </div>
      {i.instructions && (
        <p className="max-w-prose text-[12.5px] whitespace-pre-wrap text-fg-2" data-testid="human-instructions">
          {i.instructions}
        </p>
      )}
      {finished && (
        <p className="text-[12px] text-fg-3">
          Done {i.completed_at ? formatDateTime(i.completed_at) : ''}
          {i.assignee_name ? ` by ${i.assignee_name}` : ''}
          {i.result_summary ? `: ${i.result_summary}` : ''}
        </p>
      )}
      {takeError && !claim.isPending && (
        <p role="alert" className="text-[12px] text-danger">
          Somebody already took this one.
        </p>
      )}
      {acting && <ActDialog companyId={companyId} task={{ id: i.id, title: i.title }} action="complete" onClose={() => setActing(false)} />}
      {declining && <DeclineDialog companyId={companyId} item={i} onClose={() => setDeclining(false)} />}
    </li>
  )
}

function DeclineDialog({ companyId, item, onClose }: { companyId: string; item: HumanWorkItem; onClose: () => void }) {
  const decline = useDeclineHumanWork(companyId)
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!reason.trim()) {
      setError('Say why, so the AI employee can find another way.')
      return
    }
    setError(null)
    try {
      await decline.mutateAsync({ id: item.id, reason: reason.trim() })
      toast.success('Declined. The AI employee was told why.')
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'That did not go through'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !decline.isPending && onClose()}
      size="sm"
      title="Decline this request"
      description={item.title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={decline.isPending}>
            Cancel
          </Button>
          <Button variant="danger" loading={decline.isPending} onClick={() => void submit()}>
            Decline
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Reason" required hint="The AI employee reads this and its task goes back to the queue.">
          {({ id, invalid, describedBy }) => <Textarea id={id} autoFocus rows={3} maxLength={500} value={reason} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}
