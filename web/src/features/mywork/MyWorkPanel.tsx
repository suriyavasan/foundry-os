import { CheckCircle2, OctagonAlert, Paperclip } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { errorMessage, isNotDeployed, useAllTasks, useApprovals, useHumanTaskAction, useMyMember } from '@/api'
import type { Task } from '@/api/types'
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorState, Field, FormError, LoadingState, NotAvailable, Textarea, useToast } from '@/components/ui'
import { ApprovalCard } from '@/features/approvals/ApprovalCard'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { PriorityBadge, TaskStatusBadge } from '@/features/shared/badges'
import { TaskCheckout } from '@/features/wallet/TaskCheckout'
import { formatDateTime } from '@/lib/format'
import { splitMyWork, validateAttachment } from '@/lib/myWork'
import { canManageTeam } from '@/lib/people'

export function MyWorkPanel() {
  const { companyId, openPanel } = useCanvas()
  const mine = useMyMember(companyId)
  const tasks = useAllTasks(companyId)
  const approvals = useApprovals(companyId, { status: 'pending' })
  const { map: agents } = useAgentMap(companyId)
  const role = useCompanyRole(companyId)
  const [acting, setActing] = useState<{ task: Task; action: 'complete' | 'block' } | null>(null)

  const work = useMemo(
    () => (mine.memberId ? splitMyWork(tasks.data ?? [], approvals.data ?? [], mine.memberId, canManageTeam(role)) : null),
    [mine.memberId, tasks.data, approvals.data, role],
  )

  let body
  if (mine.loading || tasks.isPending) body = <Card><LoadingState rows={5} label="Loading your work" /></Card>
  else if (!mine.available) {
    body = (
      <Card>
        <NotAvailable milestone="M7" title="Team accounts are not built yet" description="Work assigned to a person arrives with accounts. Until then, tasks are assigned to AI agents only." />
      </Card>
    )
  } else if (!work) {
    body = <Card><EmptyState title="You are not a member of this company" description="Ask an owner to invite you." /></Card>
  } else if (tasks.isError && !isNotDeployed(tasks.error)) {
    body = <Card><ErrorState error={tasks.error} onRetry={() => void tasks.refetch()} retrying={tasks.isFetching} /></Card>
  } else if (work.tasks.length + work.reviews.length + work.approvals.length === 0) {
    body = <Card><EmptyState title="Nothing waiting on you" description="Tasks, reviews and approvals assigned to you appear here, and you are notified when one arrives." /></Card>
  } else {
    body = (
      <div className="space-y-4">
        <TaskSection companyId={companyId} title="Assigned to me" description="Work for you to do." tasks={work.tasks} onOpen={(id) => openPanel({ kind: 'task', id })} onAct={(task, action) => setActing({ task, action })} />
        <TaskSection companyId={companyId} title="Reviews requested" description="Someone is waiting for your verdict." tasks={work.reviews} onOpen={(id) => openPanel({ kind: 'task', id })} onAct={(task, action) => setActing({ task, action })} />
        <Card>
          <CardHeader title="Approvals for me" description="Decisions only a person can make." />
          {approvals.isError ? (
            <ErrorState compact error={approvals.error} onRetry={() => void approvals.refetch()} retrying={approvals.isFetching} />
          ) : work.approvals.length === 0 ? (
            <p className="px-4 py-4 text-[12.5px] text-fg-3">No approvals are waiting for you.</p>
          ) : (
            <div className="divide-y divide-line">
              {work.approvals.map((a) => (
                <ApprovalCard key={a.id} approval={a} agents={agents} companyId={companyId} />
              ))}
            </div>
          )}
        </Card>
      </div>
    )
  }

  return (
    <PanelFrame title="My work" subtitle="Tasks, reviews and approvals that are waiting on you">
      {body}
      {acting && <ActDialog companyId={companyId} task={acting.task} action={acting.action} onClose={() => setActing(null)} />}
    </PanelFrame>
  )
}

function TaskSection({ companyId, title, description, tasks, onOpen, onAct }: { companyId: string; title: string; description: string; tasks: Task[]; onOpen: (id: string) => void; onAct: (t: Task, a: 'complete' | 'block') => void }) {
  return (
    <Card aria-label={title}>
      <CardHeader title={title} description={description} actions={<Badge tone="neutral">{tasks.length}</Badge>} />
      {tasks.length === 0 ? (
        <p className="px-4 py-4 text-[12.5px] text-fg-3">Nothing here.</p>
      ) : (
        <ul className="divide-y divide-line">
          {tasks.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
              <div className="min-w-0 flex-1">
                <button type="button" onClick={() => onOpen(t.id)} className="block max-w-full truncate text-left text-[13px] font-medium text-fg hover:underline">
                  {t.title}
                </button>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-fg-3">
                  <TaskStatusBadge status={t.status} />
                  <PriorityBadge priority={t.priority} />
                  {t.due_at && <span>Due {formatDateTime(t.due_at)}</span>}
                </div>
              </div>
              <div className="flex gap-1.5">
                <Button size="sm" icon={<OctagonAlert className="size-3.5" />} aria-label={`Block ${t.title}`} onClick={() => onAct(t, 'block')}>
                  Block
                </Button>
                <Button size="sm" variant="primary" icon={<CheckCircle2 className="size-3.5" />} aria-label={`Complete ${t.title}`} onClick={() => onAct(t, 'complete')}>
                  Complete
                </Button>
              </div>
              <div className="w-full empty:hidden">
                <TaskCheckout companyId={companyId} taskId={t.id} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ActDialog({ companyId, task, action, onClose }: { companyId: string; task: Task; action: 'complete' | 'block'; onClose: () => void }) {
  const act = useHumanTaskAction(companyId)
  const toast = useToast()
  const [comment, setComment] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [errors, setErrors] = useState<{ comment?: string; file?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  const complete = action === 'complete'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const next: typeof errors = {}
    if (!complete && !comment.trim()) next.comment = 'Say why it is blocked, so the right person can unblock it'
    const fileError = complete ? validateAttachment(file) : null
    if (fileError) next.file = fileError
    setErrors(next)
    if (Object.keys(next).length) return
    try {
      await act.mutateAsync(complete ? { task, action: 'complete', comment, file } : { task, action: 'block', comment: comment.trim() })
      toast.success(complete ? 'Task completed' : 'Task blocked')
      onClose()
    } catch (err) {
      setFormError(isNotDeployed(err) ? `This backend cannot ${complete ? 'complete' : 'block'} tasks for people yet.` : errorMessage(err, 'That did not go through'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !act.isPending && onClose()}
      size="sm"
      title={complete ? 'Complete task' : 'Block task'}
      description={task.title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={act.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="act-task-form" loading={act.isPending}>
            {complete ? 'Mark complete' : 'Block task'}
          </Button>
        </>
      }
    >
      <form id="act-task-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label={complete ? 'Comment' : 'Reason'} required={!complete} error={errors.comment} hint={complete ? 'Optional. What was done, for whoever reads the history.' : 'Recorded in the audit log and shown to the people who can unblock it.'}>
          {({ id, invalid, describedBy }) => <Textarea id={id} autoFocus rows={3} value={comment} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setComment(e.target.value)} />}
        </Field>
        {complete && (
          <Field label="Attach a file" error={errors.file} hint="Optional. Saved as an artifact of this task (up to 25 MB).">
            {({ id, describedBy }) => (
              <div className="flex items-center gap-2">
                <Paperclip className="size-3.5 text-fg-3" aria-hidden />
                <input id={id} type="file" aria-describedby={describedBy} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="min-w-0 flex-1 text-[12.5px] text-fg-2 file:mr-2 file:rounded-md file:border file:border-line-strong file:bg-raised file:px-2 file:py-1 file:text-[12.5px] file:text-fg" />
              </div>
            )}
          </Field>
        )}
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
