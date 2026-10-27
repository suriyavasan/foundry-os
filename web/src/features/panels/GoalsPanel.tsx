import { GitBranch, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, isNotDeployed, useAllTasks, useCompany, useGoals, useProjects, useRepoAvailability, useRepositories, useUpdateGoal } from '@/api'
import type { Goal, GoalStatus, Project } from '@/api/types'
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, LoadingState, NotAvailable, ProgressBar, Select, useToast } from '@/components/ui'
import { projectProgress } from '@/features/canvas/widgets/GoalsWidget'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useAgentMap, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { ConnectRepoDialog } from '@/features/repos/ConnectRepoDialog'
import { RepoStatusBadge } from '@/features/repos/RepoBadges'
import { AgentChip } from '@/features/shared/AgentChip'
import { PriorityBadge, ProjectStatusBadge } from '@/features/shared/badges'
import { formatUsd } from '@/lib/money'

const GOAL_STATUSES: GoalStatus[] = ['proposed', 'active', 'achieved', 'abandoned']

export function GoalsPanel() {
  const { companyId, create, openPanel } = useCanvas()
  const company = useCompany(companyId)
  const goals = useGoals(companyId)
  const projects = useProjects(companyId)
  const tasks = useAllTasks(companyId)
  const { map } = useAgentMap(companyId)
  const update = useUpdateGoal(companyId)
  const toast = useToast()
  // M14: a project's repository, or the way to connect one. Hidden entirely on a backend without repositories or without git.
  const repoAvailability = useRepoAvailability(companyId)
  const repos = useRepositories(companyId)
  const canConnect = canEditPolicy(useCompanyRole(companyId)) && repoAvailability.data?.available === true
  const repoByProject = useMemo(() => new Map((repos.data ?? []).filter((r) => r.status !== 'disconnected').map((r) => [r.project_id, r])), [repos.data])
  const [connectFor, setConnectFor] = useState<string | null>(null)

  const byGoal = useMemo(() => {
    const m = new Map<string | null, Project[]>()
    for (const p of projects.data ?? []) m.set(p.goal_id, [...(m.get(p.goal_id) ?? []), p])
    return m
  }, [projects.data])
  const unavailable = isNotDeployed(goals.error)

  async function setStatus(g: Goal, status: GoalStatus) {
    try {
      await update.mutateAsync({ id: g.id, status })
      toast.success(`${g.title}: ${status}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const renderProjects = (list: Project[]) =>
    list.length === 0 ? (
      <p className="text-[12.5px] text-fg-3">No projects yet.</p>
    ) : (
      <ul className="space-y-2">
        {list.map((p) => {
          const { done, total } = projectProgress(p.id, tasks.data)
          return (
            <li key={p.id} className="rounded-md border border-line bg-raised px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg">{p.name}</span>
                <ProjectStatusBadge status={p.status} />
                {p.budget_micros !== null && <span className="text-[12px] text-fg-3">Budget {formatUsd(p.budget_micros)}</span>}
                <AgentChip agentId={p.lead_agent_id} agents={map} emptyLabel="No lead" />
                {repoByProject.has(p.id) ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-sm border border-line px-1.5 py-0.5 text-[12px] text-fg-2 hover:bg-hover hover:text-fg"
                    aria-label={`Open repository ${repoByProject.get(p.id)!.full_name}`}
                    onClick={() => openPanel({ kind: 'repo', id: repoByProject.get(p.id)!.id })}
                  >
                    <GitBranch className="size-3" aria-hidden />
                    {repoByProject.get(p.id)!.full_name}
                    <RepoStatusBadge status={repoByProject.get(p.id)!.status} />
                  </button>
                ) : canConnect && repos.isSuccess ? (
                  <button type="button" className="text-[12px] text-accent-fg hover:underline" onClick={() => setConnectFor(p.id)}>
                    Connect GitHub
                  </button>
                ) : null}
              </div>
              {p.description && <p className="mt-1 text-[12.5px] text-fg-2">{p.description}</p>}
              <div className="mt-2 flex items-center gap-3">
                <ProgressBar className="flex-1" value={done} max={total} tone={total > 0 && done === total ? 'success' : 'accent'} label={`${p.name} progress`} />
                <span className="text-[12px] text-fg-3 tabular-nums">{tasks.isPending ? '…' : total === 0 ? 'no tasks' : `${done}/${total} tasks done`}</span>
                <button type="button" className="text-[12px] text-accent-fg hover:underline" onClick={() => create('task', { project_id: p.id })}>
                  Add task
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    )

  return (
    <PanelFrame
      title="Goals & plan"
      subtitle="Outcomes, the projects that deliver them, and progress"
      actions={
        !unavailable && (
          <>
            <Button size="sm" icon={<Plus className="size-3.5" />} disabled={!goals.data} onClick={() => create('project')}>
              Project
            </Button>
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} disabled={!goals.data} onClick={() => create('goal')}>
              Goal
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <Card>
          <CardHeader
            title="Strategic goal"
            description="The headline every plan serves."
            actions={
              <Button size="sm" onClick={() => openPanel({ kind: 'settings' })}>
                Edit in settings
              </Button>
            }
          />
          <CardBody>
            {company.data?.strategic_goal ? <p className="text-[14px] whitespace-pre-wrap text-fg">{company.data.strategic_goal}</p> : <p className="text-[13px] text-fg-3">No strategic goal set yet.</p>}
          </CardBody>
        </Card>

        {goals.isPending ? (
          <Card>
            <LoadingState rows={5} label="Loading goals" />
          </Card>
        ) : unavailable ? (
          <Card>
            <NotAvailable milestone="M2" title="Goals and projects are not built yet" description="Structured goals, projects and progress come with the work engine." />
          </Card>
        ) : goals.isError ? (
          <Card>
            <ErrorState error={goals.error} onRetry={() => void goals.refetch()} retrying={goals.isFetching} />
          </Card>
        ) : goals.data.length === 0 && (projects.data?.length ?? 0) === 0 ? (
          <Card>
            <EmptyState title="No goals yet" description="Create a goal, or let the AI Co-founder propose them from the strategic goal." action={<Button variant="primary" onClick={() => create('goal')}>Create goal</Button>} />
          </Card>
        ) : (
          <>
            {goals.data.map((g) => (
              <Card key={g.id}>
                <CardHeader
                  title={g.title}
                  description={g.target_date ? `Target ${g.target_date}` : undefined}
                  actions={
                    <>
                      <PriorityBadge priority={g.priority} />
                      <Select aria-label={`Status of ${g.title}`} className="h-7 w-auto text-[12.5px]" value={g.status} disabled={update.isPending} onChange={(e) => void setStatus(g, e.target.value as GoalStatus)}>
                        {GOAL_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </Select>
                    </>
                  }
                />
                <CardBody className="space-y-3">
                  {g.description && <p className="text-[13px] whitespace-pre-wrap text-fg-2">{g.description}</p>}
                  <div className="flex items-center gap-3 text-[12px] text-fg-3">
                    Owner: <AgentChip agentId={g.owner_agent_id} agents={map} emptyLabel="AI Co-founder" />
                  </div>
                  {renderProjects(byGoal.get(g.id) ?? [])}
                  <button type="button" className="text-[12.5px] text-accent-fg hover:underline" onClick={() => create('project', { goal_id: g.id })}>
                    + Add project to this goal
                  </button>
                </CardBody>
              </Card>
            ))}
            {(byGoal.get(null)?.length ?? 0) > 0 && (
              <Card>
                <CardHeader title="Projects without a goal" />
                <CardBody>{renderProjects(byGoal.get(null)!)}</CardBody>
              </Card>
            )}
          </>
        )}
      </div>
      <ConnectRepoDialog companyId={companyId} open={connectFor !== null} onOpenChange={(o) => !o && setConnectFor(null)} projectId={connectFor} onConnected={(repo) => openPanel({ kind: 'repo', id: repo.id })} />
    </PanelFrame>
  )
}
