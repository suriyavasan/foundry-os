import { useQueries } from '@tanstack/react-query'
import { useMemo } from 'react'
import { ApiError, fetchAllPages, isNotDeployed, useAgent, useAgents, useDepartments, useTasks } from '@/api'
import { qk } from '@/api/keys'
import type { TaskRun } from '@/api/types'
import { AgentAvatar } from '@/components/avatar'
import {
  Boundary,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { AgentActivityTab } from '@/features/organization/profile/AgentActivityTab'
import { BudgetTab } from '@/features/organization/profile/BudgetTab'
import { ConfigurationTab } from '@/features/organization/profile/ConfigurationTab'
import { LifecycleActions } from '@/features/organization/profile/LifecycleActions'
import { OverviewTab } from '@/features/organization/profile/OverviewTab'
import { PermissionsTab } from '@/features/organization/profile/PermissionsTab'
import { PriorityBadge, RunStatusBadge, TaskStatusBadge } from '@/features/shared/badges'
import { formatDateTime, formatRelative, formatTokens } from '@/lib/format'
import { formatCost } from '@/lib/money'
import { formatDuration } from '@/lib/dates'
import { NotAvailable, Badge, Chip } from '@/components/ui'
import { AgentMeetings } from '@/features/meetings/AgentMeetings'
import { AgentSkillsTab } from '@/features/skills/AgentSkillsTab'
import { ROLE_FAMILY_LABEL, inferRoleFamily } from '@/lib/roleFamily'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'

export function AgentPanel({ agentId }: { agentId: string }) {
  const { companyId, openPanel } = useCanvas()
  const agentQ = useAgent(companyId, agentId)
  const agentsQ = useAgents(companyId)
  const departmentsQ = useDepartments(companyId)

  if (agentQ.isError && agentQ.error instanceof ApiError && agentQ.error.status === 404) {
    return (
      <PanelFrame title="Agent not found">
        <EmptyState title="Agent not found" description="This agent does not exist in this company." action={<button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'org' })}>Back to organization</button>} />
      </PanelFrame>
    )
  }
  if (agentQ.isPending) {
    return (
      <PanelFrame title="Agent">
        <Card>
          <LoadingState rows={6} label="Loading agent" />
        </Card>
      </PanelFrame>
    )
  }
  if (agentQ.isError) {
    return (
      <PanelFrame title="Agent">
        <Card>
          <ErrorState error={agentQ.error} onRetry={() => agentQ.refetch()} retrying={agentQ.isFetching} />
        </Card>
      </PanelFrame>
    )
  }

  // The list carries the freshest `live` block (the stream patches it); the detail GET may predate it.
  const fromList = agentsQ.data?.find((a) => a.id === agentId)
  const agent = fromList && fromList.updated_at >= agentQ.data.updated_at ? { ...agentQ.data, live: fromList.live } : agentQ.data
  const allAgents = agentsQ.data ?? []
  const departments = departmentsQ.data ?? []

  return (
    <PanelFrame
      title={
        <span className="flex items-center gap-2.5">
          {agent.name}
          <StatusBadge status={agent.status} />
          <Badge tone="accent" title="Employee type (role family)">
            {ROLE_FAMILY_LABEL[inferRoleFamily(agent)]}
          </Badge>
          {agent.role_template && (
            <span className="hidden sm:inline-flex" title="Role template this agent was hired from">
              <Chip>{agent.role_template}</Chip>
            </span>
          )}
        </span>
      }
      leading={<AgentAvatar agent={agent} size="md" />}
      subtitle={agent.kind === 'founder' ? `${AI_COFOUNDER_LABEL} · ${agent.role_title}` : agent.role_title}
      onBack={() => openPanel({ kind: 'org' })}
      actions={
        <>
          {agent.kind === 'founder' && (
            <Button size="sm" onClick={() => openPanel({ kind: 'cofounder' })}>
              Open co-founder view
            </Button>
          )}
          {inferRoleFamily(agent) === 'hr' && (
            <Button size="sm" onClick={() => openPanel({ kind: 'people-ops' })}>
              Open People ops
            </Button>
          )}
          <LifecycleActions agent={agent} companyId={companyId} />
        </>
      }
    >
      {agent.status === 'retired' && (
        <div className="mb-4 rounded-md border border-line bg-surface px-3 py-2 text-[13px] text-fg-2">This agent is retired and read-only. Its history is preserved.</div>
      )}
      <Tabs defaultValue="overview">
        <TabsList aria-label="Agent sections">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="meetings">Meetings</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="configuration">Configuration</TabsTrigger>
          <TabsTrigger value="budget">Budget</TabsTrigger>
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab agent={agent} agents={allAgents} departments={departments} />
        </TabsContent>
        <TabsContent value="tasks">
          <AgentTasksTab companyId={companyId} agentId={agent.id} />
        </TabsContent>
        <TabsContent value="runs">
          <AgentRunsTab companyId={companyId} agentId={agent.id} />
        </TabsContent>
        <TabsContent value="activity">
          <AgentActivityTab companyId={companyId} agentId={agent.id} />
        </TabsContent>
        <TabsContent value="meetings">
          <AgentMeetings companyId={companyId} agentId={agent.id} />
        </TabsContent>
        <TabsContent value="skills">
          <AgentSkillsTab agent={agent} />
        </TabsContent>
        <TabsContent value="configuration">
          <ConfigurationTab key={agent.updated_at} agent={agent} agents={allAgents} departments={departments} />
        </TabsContent>
        <TabsContent value="budget">
          <BudgetTab key={agent.updated_at} agent={agent} />
        </TabsContent>
        <TabsContent value="permissions">
          <PermissionsTab key={agent.updated_at} agent={agent} />
        </TabsContent>
      </Tabs>
    </PanelFrame>
  )
}

function AgentTasksTab({ companyId, agentId }: { companyId: string; agentId: string }) {
  const { openPanel } = useCanvas()
  const q = useTasks(companyId, { assignee_agent_id: agentId })
  return (
    <Card>
      <Boundary query={q} isEmpty={(d) => d.length === 0} empty={{ title: 'No tasks assigned', description: 'Tasks assigned to this agent appear here.' }} unavailableText="Tasks come with the work engine." compact={false}>
        {(tasks) => (
          <Table>
            <THead>
              <TR>
                <TH>Task</TH>
                <TH>Status</TH>
                <TH>Priority</TH>
                <TH className="text-right">Attempts</TH>
                <TH>Updated</TH>
              </TR>
            </THead>
            <TBody>
              {[...tasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map((t) => (
                <TR key={t.id} interactive>
                  <TD>
                    <button type="button" className="text-left font-medium hover:underline" onClick={() => openPanel({ kind: 'task', id: t.id })}>
                      {t.title}
                    </button>
                  </TD>
                  <TD>
                    <TaskStatusBadge status={t.status} />
                  </TD>
                  <TD>
                    <PriorityBadge priority={t.priority} />
                  </TD>
                  <TD className="text-right tabular-nums text-fg-2">{t.attempt_count}</TD>
                  <TD className="text-fg-3">{formatRelative(t.updated_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Boundary>
    </Card>
  )
}

const RUN_TASK_LIMIT = 12

/**
 * There is no per-agent runs endpoint in M2, so this lists runs of the agent's most recently updated
 * tasks (`GET /tasks/{id}/runs`), filtered to this agent. It says so when it had to cap.
 */
function AgentRunsTab({ companyId, agentId }: { companyId: string; agentId: string }) {
  const { openPanel } = useCanvas()
  const tasksQ = useTasks(companyId, { assignee_agent_id: agentId })
  const attempted = useMemo(
    () => (tasksQ.data ?? []).filter((t) => t.attempt_count > 0 || t.status === 'in_progress').sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [tasksQ.data],
  )
  const capped = attempted.slice(0, RUN_TASK_LIMIT)
  const runQs = useQueries({
    queries: capped.map((t) => ({
      queryKey: qk.taskRuns(companyId, t.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchAllPages<TaskRun>(`/companies/${companyId}/tasks/${t.id}/runs`, {}, signal, 5),
    })),
  })
  const runs = useMemo(() => {
    const rows = runQs.flatMap((q, i) => (q.data ?? []).filter((r) => r.agent_id === agentId).map((run) => ({ run, task: capped[i]! })))
    return rows.sort((a, b) => (b.run.queued_at ?? '').localeCompare(a.run.queued_at ?? ''))
  }, [runQs, capped, agentId])

  if (tasksQ.isPending) return <Card><LoadingState rows={4} label="Loading runs" /></Card>
  if (tasksQ.isError) {
    return (
      <Card>
        {isNotDeployed(tasksQ.error) ? <NotAvailable milestone="M2" title="Runs are not built yet" description="Task runs come with the work engine." /> : <ErrorState error={tasksQ.error} onRetry={() => tasksQ.refetch()} />}
      </Card>
    )
  }
  const loadingRuns = runQs.some((q) => q.isPending)
  return (
    <Card>
      {loadingRuns && runs.length === 0 ? (
        <LoadingState rows={4} label="Loading runs" />
      ) : runs.length === 0 ? (
        <EmptyState title="No runs yet" description="Each time this agent works on a task, the run is listed here with its cost and outcome." />
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Task</TH>
                <TH>Attempt</TH>
                <TH>Status</TH>
                <TH>Started</TH>
                <TH>Duration</TH>
                <TH className="text-right">Tokens in / out</TH>
                <TH className="text-right">Cost</TH>
              </TR>
            </THead>
            <TBody>
              {runs.map(({ run, task }) => (
                <TR key={run.id} interactive>
                  <TD>
                    <button type="button" className="text-left font-medium hover:underline" onClick={() => openPanel({ kind: 'task', id: task.id, run: run.id })}>
                      {task.title}
                    </button>
                  </TD>
                  <TD className="tabular-nums text-fg-2">#{run.attempt_number}</TD>
                  <TD>
                    <RunStatusBadge status={run.status} />
                  </TD>
                  <TD className="text-fg-2" title={formatDateTime(run.started_at)}>
                    {run.started_at ? formatRelative(run.started_at) : '—'}
                  </TD>
                  <TD className="tabular-nums text-fg-2">{run.started_at && run.finished_at ? formatDuration(new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) : '—'}</TD>
                  <TD className="text-right tabular-nums text-fg-2">
                    {formatTokens(run.input_tokens)} / {formatTokens(run.output_tokens)}
                  </TD>
                  <TD className="text-right tabular-nums text-fg-2">{formatCost(run.cost_micros, run.cost_kind)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {attempted.length > RUN_TASK_LIMIT && (
            <p className="border-t border-line px-3 py-2 text-[12px] text-fg-3">
              Showing runs from this agent&apos;s {RUN_TASK_LIMIT} most recently updated tasks ({attempted.length} have run).
            </p>
          )}
        </>
      )}
    </Card>
  )
}
