import { Plus } from 'lucide-react'
import { useMemo } from 'react'
import { isNotDeployed, useAllTasks } from '@/api'
import type { Task, TaskStatus } from '@/api/types'
import { Button, EmptyState, ErrorState, LoadingState, NotAvailable, Widget } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { PriorityBadge, TaskStatusBadge } from '@/features/shared/badges'
import { ACTIVE_WORK, TASK_STATUS_LABEL } from '@/lib/taskStatus'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'
import clsx from 'clsx'

const COUNT_ORDER: TaskStatus[] = ['in_progress', 'in_review', 'blocked', 'ready', 'backlog', 'done', 'failed']
const COUNT_TONE: Partial<Record<TaskStatus, string>> = { in_progress: 'text-accent-fg', blocked: 'text-warning', failed: 'text-danger', done: 'text-success' }
const PRIORITY_RANK = { urgent: 0, high: 1, normal: 2, low: 3 } as const

export function countByStatus(tasks: Task[]): Record<TaskStatus, number> {
  const out = { backlog: 0, ready: 0, in_progress: 0, in_review: 0, revision_requested: 0, blocked: 0, done: 0, failed: 0, cancelled: 0 } satisfies Record<TaskStatus, number>
  for (const t of tasks) out[t.status] = (out[t.status] ?? 0) + 1
  return out
}

export function WorkWidget({ className }: { className?: string }) {
  const { companyId, openPanel, create } = useCanvas()
  const tasks = useAllTasks(companyId)
  const { map } = useAgentMap(companyId)

  const counts = useMemo(() => countByStatus(tasks.data ?? []), [tasks.data])
  const active = useMemo(
    () =>
      (tasks.data ?? [])
        .filter((t) => ACTIVE_WORK.has(t.status))
        .sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || b.updated_at.localeCompare(a.updated_at)),
    [tasks.data],
  )
  const unavailable = isNotDeployed(tasks.error)

  return (
    <Widget
      title="Work"
      subtitle={tasks.data ? `${tasks.data.length} tasks` : undefined}
      onExpand={() => openPanel({ kind: 'tasks' })}
      expandLabel="Open all tasks"
      className={className}
      actions={
        !unavailable && (
          <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => create('task')} disabled={!tasks.data}>
            Task
          </Button>
        )
      }
    >
      {tasks.isPending ? (
        <LoadingState rows={5} label="Loading tasks" />
      ) : unavailable ? (
        <NotAvailable compact className="p-4" milestone="M2" description="Tasks, dependencies and runs come with the work engine." />
      ) : tasks.isError ? (
        <ErrorState compact error={tasks.error} onRetry={() => void tasks.refetch()} retrying={tasks.isFetching} />
      ) : tasks.data.length === 0 ? (
        <EmptyState compact title="No tasks yet" description="Tasks created by you or by the AI Founder appear here." action={<Button size="sm" variant="primary" onClick={() => create('task')}>Create task</Button>} />
      ) : (
        <>
          <ul className="grid grid-cols-4 gap-px border-b border-line bg-line">
            {COUNT_ORDER.map((s) => (
              <li key={s} className="bg-surface">
                <button
                  type="button"
                  onClick={() => openPanel({ kind: 'tasks' })}
                  className="block w-full px-3 py-2 text-left transition-colors hover:bg-hover"
                  aria-label={`${counts[s]} ${TASK_STATUS_LABEL[s].toLowerCase()}`}
                >
                  <span className="block truncate text-[11px] text-fg-3">{TASK_STATUS_LABEL[s]}</span>
                  <span className={clsx('block text-[17px] leading-6 font-semibold tabular-nums', counts[s] > 0 ? (COUNT_TONE[s] ?? 'text-fg') : 'text-fg-3')}>{counts[s]}</span>
                </button>
              </li>
            ))}
            <li className="bg-surface" aria-hidden />
          </ul>
          {active.length === 0 ? (
            <p className="px-3.5 py-4 text-[12.5px] text-fg-3">Nothing is in progress, blocked or in review right now.</p>
          ) : (
            <ul className="divide-y divide-line">
              {active.slice(0, 8).map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => openPanel({ kind: 'task', id: t.id })} className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-hover">
                    <AgentChip agentId={t.assignee_agent_id} agents={map} size="sm" hideName emptyLabel="–" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] text-fg">{t.title}</span>
                      <span className="block truncate text-[11.5px] text-fg-3">{t.assignee_agent_id ? (map.get(t.assignee_agent_id)?.name ?? 'Unknown agent') : 'Unassigned'}</span>
                    </span>
                    {(t.priority === 'urgent' || t.priority === 'high') && <PriorityBadge priority={t.priority} />}
                    <TaskStatusBadge status={t.status} />
                  </button>
                </li>
              ))}
              {active.length > 8 && (
                <li>
                  <button type="button" onClick={() => openPanel({ kind: 'tasks' })} className="w-full px-3.5 py-2 text-left text-[12px] text-accent-fg hover:underline">
                    + {active.length - 8} more
                  </button>
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </Widget>
  )
}
