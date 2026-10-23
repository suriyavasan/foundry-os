import { Plus } from 'lucide-react'
import { useMemo } from 'react'
import { isNotDeployed, useAllTasks, useCompany, useGoals, useProjects } from '@/api'
import type { Goal, Project, Task } from '@/api/types'
import { Button, EmptyState, ErrorState, LoadingState, NotAvailable, ProgressBar, SectionLabel, Widget } from '@/components/ui'
import { GoalStatusBadge, ProjectStatusBadge } from '@/features/shared/badges'
import { PriorityBadge } from '@/features/shared/badges'
import { progressOf } from '@/lib/taskStatus'
import { useCanvas } from '../CanvasContext'

const GOAL_ORDER: Record<Goal['status'], number> = { active: 0, proposed: 1, achieved: 2, abandoned: 3 }

export function projectProgress(projectId: string, tasks: Task[] | undefined) {
  return progressOf((tasks ?? []).filter((t) => t.project_id === projectId).map((t) => t.status))
}

export function GoalsWidget({ className }: { className?: string }) {
  const { companyId, openPanel, create } = useCanvas()
  const company = useCompany(companyId)
  const goals = useGoals(companyId)
  const projects = useProjects(companyId)
  const tasks = useAllTasks(companyId)

  const sorted = useMemo(
    () => [...(goals.data ?? [])].sort((a, b) => GOAL_ORDER[a.status] - GOAL_ORDER[b.status] || a.created_at.localeCompare(b.created_at)),
    [goals.data],
  )
  const byGoal = useMemo(() => {
    const m = new Map<string | null, Project[]>()
    for (const p of projects.data ?? []) {
      const list = m.get(p.goal_id) ?? []
      list.push(p)
      m.set(p.goal_id, list)
    }
    return m
  }, [projects.data])

  const unavailable = isNotDeployed(goals.error)
  const strategic = company.data?.strategic_goal

  return (
    <Widget
      title="Goals & plan"
      subtitle={goals.data ? `${goals.data.filter((g) => g.status === 'active').length} active` : undefined}
      onExpand={() => openPanel({ kind: 'goals' })}
      expandLabel="Open goals and plan"
      className={className}
      actions={
        !unavailable && (
          <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => create('goal')} disabled={!goals.data}>
            Goal
          </Button>
        )
      }
    >
      {strategic && (
        <div className="border-b border-line bg-raised px-3.5 py-2">
          <SectionLabel>Strategic goal</SectionLabel>
          <p className="mt-0.5 line-clamp-2 text-[12.5px] text-fg">{strategic}</p>
        </div>
      )}
      {goals.isPending ? (
        <LoadingState rows={4} label="Loading goals" />
      ) : unavailable ? (
        <NotAvailable compact className="p-4" milestone="M2" description="Structured goals and projects come with the work engine." />
      ) : goals.isError ? (
        <ErrorState compact error={goals.error} onRetry={() => void goals.refetch()} retrying={goals.isFetching} />
      ) : sorted.length === 0 && (projects.data?.length ?? 0) === 0 ? (
        <EmptyState compact title="No goals yet" description="Goals break the strategic goal into outcomes the company can plan against." action={<Button size="sm" variant="primary" onClick={() => create('goal')}>Create goal</Button>} />
      ) : (
        <ul className="divide-y divide-line">
          {sorted.map((g) => (
            <li key={g.id} className="px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg">{g.title}</span>
                <PriorityBadge priority={g.priority} />
                <GoalStatusBadge status={g.status} />
              </div>
              <ProjectList projects={byGoal.get(g.id) ?? []} tasks={tasks.data} tasksPending={tasks.isPending} onOpen={() => openPanel({ kind: 'goals' })} />
            </li>
          ))}
          {(byGoal.get(null)?.length ?? 0) > 0 && (
            <li className="px-3.5 py-2.5">
              <div className="text-[12.5px] text-fg-3">Projects without a goal</div>
              <ProjectList projects={byGoal.get(null)!} tasks={tasks.data} tasksPending={tasks.isPending} onOpen={() => openPanel({ kind: 'goals' })} />
            </li>
          )}
        </ul>
      )}
    </Widget>
  )
}

function ProjectList({ projects, tasks, tasksPending, onOpen }: { projects: Project[]; tasks: Task[] | undefined; tasksPending: boolean; onOpen: () => void }) {
  if (projects.length === 0) return <p className="mt-1 text-[12px] text-fg-3">No projects yet.</p>
  return (
    <ul className="mt-1.5 space-y-1.5">
      {projects.map((p) => {
        const { done, total } = projectProgress(p.id, tasks)
        return (
          <li key={p.id}>
            <button type="button" onClick={onOpen} className="group w-full rounded-sm text-left">
              <div className="flex items-center justify-between gap-2 text-[12px]">
                <span className="min-w-0 truncate text-fg-2 group-hover:text-fg">{p.name}</span>
                <span className="flex shrink-0 items-center gap-2 text-fg-3">
                  {p.status !== 'active' && <ProjectStatusBadge status={p.status} />}
                  <span className="tabular-nums">{tasksPending ? '…' : total === 0 ? 'no tasks' : `${done}/${total}`}</span>
                </span>
              </div>
              <ProgressBar className="mt-1" value={done} max={total} tone={total > 0 && done === total ? 'success' : 'accent'} label={`${p.name} progress`} />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
