import { Plus, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  ApiError,
  errorMessage,
  useAddDependency,
  useAllTasks,
  useArtifacts,
  useProjects,
  useRemoveDependency,
  useTask,
  useTaskAction,
  useTaskRuns,
  useTasks,
} from '@/api'
import type { Task, TaskAction } from '@/api/types'
import {
  Boundary,
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  ConfirmDialog,
  DefinitionList,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  FormError,
  IconButton,
  LoadingState,
  Select,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Textarea,
  useToast,
} from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { AgentChip } from '@/features/shared/AgentChip'
import { ArtifactStatusBadge, PriorityBadge, RunStatusBadge, TaskStatusBadge } from '@/features/shared/badges'
import { formatDuration } from '@/lib/dates'
import { formatDateTime, formatRelative } from '@/lib/format'
import { formatCost, formatUsd } from '@/lib/money'
import { allowedTaskActions, isTerminalTask, type TaskActionSpec } from '@/lib/taskStatus'
import { RunDetail } from './RunDetail'

const dash = <span className="text-fg-3">—</span>

export function TaskPanel({ taskId, runId }: { taskId: string; runId?: string }) {
  const { companyId, openPanel } = useCanvas()
  const taskQ = useTask(companyId, taskId)

  if (taskQ.isError && taskQ.error instanceof ApiError && taskQ.error.status === 404) {
    return (
      <PanelFrame title="Task not found">
        <EmptyState title="Task not found" description="This task does not exist in this company." />
      </PanelFrame>
    )
  }
  if (taskQ.isPending) {
    return (
      <PanelFrame title="Task">
        <Card>
          <LoadingState rows={6} label="Loading task" />
        </Card>
      </PanelFrame>
    )
  }
  if (taskQ.isError) {
    return (
      <PanelFrame title="Task">
        <Card>
          <ErrorState error={taskQ.error} onRetry={() => void taskQ.refetch()} retrying={taskQ.isFetching} />
        </Card>
      </PanelFrame>
    )
  }
  const task = taskQ.data

  if (runId) {
    return (
      <PanelFrame title={`Run · ${task.title}`} subtitle="Run detail" onBack={() => openPanel({ kind: 'task', id: task.id })}>
        <RunDetail companyId={companyId} runId={runId} />
      </PanelFrame>
    )
  }

  return (
    <PanelFrame title={task.title} subtitle={<TaskStatusBadge status={task.status} />} onBack={() => openPanel({ kind: 'tasks' })} actions={<TaskActions companyId={companyId} task={task} />}>
      <TaskBody companyId={companyId} task={task} />
    </PanelFrame>
  )
}

function TaskBody({ companyId, task }: { companyId: string; task: Task }) {
  const { openPanel } = useCanvas()
  const { map } = useAgentMap(companyId)
  const projects = useProjects(companyId)
  const project = projects.data?.find((p) => p.id === task.project_id)
  const parent = useAllTasks(companyId).data?.find((t) => t.id === task.parent_task_id)

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Brief" />
          <CardBody className="space-y-4">
            <TextBlock label="Description" text={task.description} empty="No description." />
            <TextBlock label="Acceptance criteria" text={task.acceptance_criteria} empty="None. The task is done as soon as the run succeeds." />
            {task.result_summary && <TextBlock label="Result" text={task.result_summary} empty="" />}
            {task.error && (
              <div role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
                <strong className="font-semibold">{task.error.code}: </strong>
                {task.error.message}
                {task.error.run_id && (
                  <button type="button" className="ml-2 underline" onClick={() => openPanel({ kind: 'task', id: task.id, run: task.error!.run_id! })}>
                    View run
                  </button>
                )}
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DefinitionList
              items={[
                { label: 'Priority', value: <PriorityBadge priority={task.priority} /> },
                { label: 'Kind', value: <span className="capitalize">{task.kind}</span> },
                { label: 'Assignee', value: <AgentChip agentId={task.assignee_agent_id} agents={map} size="sm" /> },
                { label: 'Reviewer', value: task.reviewer_agent_id ? <AgentChip agentId={task.reviewer_agent_id} agents={map} size="sm" /> : <span className="text-fg-3">Assignee&apos;s manager</span> },
                { label: 'Project', value: project?.name ?? dash },
                { label: 'Parent', value: parent ? <button type="button" className="text-left text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: parent.id })}>{parent.title}</button> : dash },
                { label: 'Budget', value: task.budget_micros !== null ? formatUsd(task.budget_micros) : <span className="text-fg-3">No cap</span> },
                { label: 'Attempts', value: task.attempt_count },
                { label: 'Revisions', value: task.revision_count },
                { label: 'Depth', value: task.delegation_depth },
                { label: 'Due', value: task.due_at ? formatDateTime(task.due_at) : dash },
                { label: 'Started', value: task.started_at ? formatDateTime(task.started_at) : dash },
                { label: 'Completed', value: task.completed_at ? formatDateTime(task.completed_at) : dash },
                { label: 'Created', value: formatDateTime(task.created_at) },
                ...(task.required_skills.length ? [{ label: 'Skills', value: <span className="flex flex-wrap gap-1">{task.required_skills.map((s) => <Chip key={s}>{s}</Chip>)}</span> }] : []),
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <Dependencies companyId={companyId} task={task} />
      <Subtasks companyId={companyId} task={task} />
      <Runs companyId={companyId} task={task} />
      <TaskArtifacts companyId={companyId} task={task} />
    </div>
  )
}

function TextBlock({ label, text, empty }: { label: string; text: string | null; empty: string }) {
  return (
    <div>
      <div className="mb-1 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">{label}</div>
      {text ? <p className="text-[13px] break-words whitespace-pre-wrap text-fg">{text}</p> : <p className="text-[13px] text-fg-3">{empty}</p>}
    </div>
  )
}

/* -------------------------------- actions -------------------------------- */

function TaskActions({ companyId, task }: { companyId: string; task: Task }) {
  const act = useTaskAction(companyId, task.id)
  const toast = useToast()
  const [dialog, setDialog] = useState<TaskActionSpec | null>(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState<TaskAction | null>(null)
  const actions = allowedTaskActions(task.status)

  async function run(spec: TaskActionSpec) {
    setError(null)
    setRunning(spec.action)
    try {
      await act.mutateAsync({ action: spec.action, reason: reason.trim() || undefined })
      toast.success(`${spec.label}: done`)
      setDialog(null)
      setReason('')
    } catch (err) {
      const msg = err instanceof ApiError && (err.status === 409 || err.status === 422) ? err.message : errorMessage(err)
      if (dialog) setError(msg)
      else toast.error(msg)
    } finally {
      setRunning(null)
    }
  }

  if (actions.length === 0) return null
  return (
    <>
      {actions.map((spec) => (
        <Button
          key={spec.action}
          size="sm"
          variant={spec.destructive ? 'danger' : 'secondary'}
          loading={running === spec.action && !dialog}
          disabled={running !== null}
          onClick={() => {
            if (spec.needsReason || spec.destructive) {
              setError(null)
              setReason('')
              setDialog(spec)
            } else void run(spec)
          }}
        >
          {spec.label}
        </Button>
      ))}
      {dialog &&
        (dialog.needsReason ? (
          <Dialog
            open
            onOpenChange={(o) => !o && running === null && setDialog(null)}
            size="sm"
            title={`${dialog.label} “${task.title}”?`}
            description="The task will not be dispatched while it is blocked."
            footer={
              <>
                <Button variant="ghost" onClick={() => setDialog(null)} disabled={running !== null}>
                  Cancel
                </Button>
                <Button variant="primary" loading={running === dialog.action} onClick={() => (reason.trim() ? void run(dialog) : setError('A reason is required.'))}>
                  {dialog.label}
                </Button>
              </>
            }
          >
            <div className="space-y-3">
              <Field label="Reason" required hint="Recorded in the audit log and shown to the assignee.">
                {({ id }) => <Textarea id={id} autoFocus rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}
              </Field>
              <FormError message={error} />
            </div>
          </Dialog>
        ) : (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && running === null && setDialog(null)}
            title={`${dialog.label}?`}
            confirmLabel={dialog.label}
            loading={running === dialog.action}
            error={error}
            onConfirm={() => void run(dialog)}
            description={
              task.status === 'in_progress'
                ? 'The task is running right now. Its run is asked to stop, the reserved budget is released, and the task becomes cancelled. This cannot be undone.'
                : 'The task becomes cancelled and will not run. This cannot be undone.'
            }
          />
        ))}
    </>
  )
}

/* ------------------------------ dependencies ------------------------------ */

function Dependencies({ companyId, task }: { companyId: string; task: Task }) {
  const { openPanel } = useCanvas()
  const all = useAllTasks(companyId)
  const add = useAddDependency(companyId, task.id)
  const remove = useRemoveDependency(companyId, task.id)
  const toast = useToast()
  const [choice, setChoice] = useState('')
  const [error, setError] = useState<string | null>(null)
  const byId = useMemo(() => new Map((all.data ?? []).map((t) => [t.id, t])), [all.data])
  const deps = task.depends_on
  const candidates = (all.data ?? []).filter((t) => t.id !== task.id && !(deps ?? []).includes(t.id) && t.status !== 'cancelled')
  const editable = !isTerminalTask(task.status)

  async function addDep() {
    if (!choice) return
    setError(null)
    try {
      await add.mutateAsync(choice)
      setChoice('')
      toast.success('Dependency added')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Card>
      <CardHeader title="Dependencies" description="This task starts only after these are done." />
      <CardBody className="space-y-3">
        {deps === undefined ? (
          <p className="text-[13px] text-fg-3">The API did not report this task&apos;s dependencies.</p>
        ) : deps.length === 0 ? (
          <p className="text-[13px] text-fg-3">No dependencies.</p>
        ) : (
          <ul className="space-y-1">
            {deps.map((id) => {
              const d = byId.get(id)
              return (
                <li key={id} className="flex items-center gap-2 rounded-md border border-line bg-raised py-1 pr-1 pl-2.5 text-[13px]">
                  {d ? (
                    <>
                      <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" onClick={() => openPanel({ kind: 'task', id })}>
                        {d.title}
                      </button>
                      <TaskStatusBadge status={d.status} />
                    </>
                  ) : (
                    <span className="min-w-0 flex-1 truncate text-fg-3">Task {id.slice(0, 8)}…</span>
                  )}
                  {editable && (
                    <IconButton
                      label={`Remove dependency ${d?.title ?? id}`}
                      className="size-6"
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(id, { onError: (e) => setError(errorMessage(e)) })}
                    >
                      <X className="size-3.5" aria-hidden />
                    </IconButton>
                  )}
                </li>
              )
            })}
          </ul>
        )}
        {editable && (
          <div className="flex gap-2">
            <Select aria-label="Add dependency" className="h-8 flex-1" value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="">Add a dependency…</option>
              {candidates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </Select>
            <Button size="sm" icon={<Plus className="size-3.5" />} disabled={!choice} loading={add.isPending} onClick={() => void addDep()}>
              Add
            </Button>
          </div>
        )}
        <FormError message={error} />
      </CardBody>
    </Card>
  )
}

/* -------------------------------- subtasks -------------------------------- */

function Subtasks({ companyId, task }: { companyId: string; task: Task }) {
  const { openPanel, create } = useCanvas()
  const q = useTasks(companyId, { parent_task_id: task.id })
  const { map } = useAgentMap(companyId)
  return (
    <Card>
      <CardHeader
        title="Subtasks"
        actions={
          !isTerminalTask(task.status) && (
            <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => create('task', { parent_task_id: task.id, project_id: task.project_id ?? undefined })}>
              Add subtask
            </Button>
          )
        }
      />
      <Boundary query={q} isEmpty={(d) => d.length === 0} empty={{ title: 'No subtasks', description: 'Managers and the AI Co-founder break work down into subtasks.' }} unavailableText="Tasks come with the work engine.">
        {(items) => (
          <ul className="divide-y divide-line">
            {items.map((t) => (
              <li key={t.id}>
                <button type="button" onClick={() => openPanel({ kind: 'task', id: t.id })} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-hover">
                  <span className="min-w-0 flex-1 truncate text-[13px]">{t.title}</span>
                  <AgentChip agentId={t.assignee_agent_id} agents={map} />
                  <TaskStatusBadge status={t.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
    </Card>
  )
}

/* ---------------------------------- runs ---------------------------------- */

function Runs({ companyId, task }: { companyId: string; task: Task }) {
  const { openPanel } = useCanvas()
  const q = useTaskRuns(companyId, task.id)
  const { map } = useAgentMap(companyId)
  return (
    <Card>
      <CardHeader title="Runs" description="Each attempt by an agent, newest first." />
      <Boundary query={q} compact={false} isEmpty={(d) => d.length === 0} empty={{ title: 'No runs yet', description: task.status === 'ready' ? 'This task is ready and will be dispatched shortly.' : 'A run is created when the task is dispatched to its assignee.' }} unavailableText="Runs come with the work engine.">
        {(runs) => (
          <Table>
            <THead>
              <TR>
                <TH>Attempt</TH>
                <TH>Status</TH>
                <TH>Agent</TH>
                <TH>Started</TH>
                <TH>Duration</TH>
                <TH className="text-right">Cost</TH>
              </TR>
            </THead>
            <TBody>
              {[...runs].sort((a, b) => b.attempt_number - a.attempt_number).map((r) => (
                <TR key={r.id} interactive>
                  <TD>
                    <button type="button" className="font-medium hover:underline" onClick={() => openPanel({ kind: 'task', id: task.id, run: r.id })}>
                      #{r.attempt_number}
                    </button>
                  </TD>
                  <TD>
                    <RunStatusBadge status={r.status} />
                  </TD>
                  <TD>
                    <AgentChip agentId={r.agent_id} agents={map} />
                  </TD>
                  <TD className="text-fg-2" title={formatDateTime(r.started_at)}>
                    {r.started_at ? formatRelative(r.started_at) : '—'}
                  </TD>
                  <TD className="tabular-nums text-fg-2">{r.started_at && r.finished_at ? formatDuration(new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) : '—'}</TD>
                  <TD className="text-right tabular-nums text-fg-2">{formatCost(r.cost_micros, r.cost_kind)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
    </Card>
  )
}

/* -------------------------------- artifacts -------------------------------- */

function TaskArtifacts({ companyId, task }: { companyId: string; task: Task }) {
  const { openPanel } = useCanvas()
  const q = useArtifacts(companyId, { task_id: task.id })
  return (
    <Card>
      <CardHeader title="Artifacts" />
      <Boundary query={q} isEmpty={(d) => d.items.length === 0} empty={{ title: 'No artifacts', description: 'Files and documents produced for this task appear here.' }} unavailableText="Artifacts come with the work engine.">
        {(page) => (
          <ul className="divide-y divide-line">
            {page.items.map((a) => (
              <li key={a.id}>
                <button type="button" onClick={() => openPanel({ kind: 'artifact', id: a.id })} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-hover">
                  <span className="min-w-0 flex-1 truncate text-[13px]">{a.title}</span>
                  <Chip>{a.kind}</Chip>
                  <span className="text-[12px] text-fg-3">v{a.current_version}</span>
                  <ArtifactStatusBadge status={a.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Boundary>
    </Card>
  )
}
