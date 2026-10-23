import type { Agent } from '@/api/types'
import { ProgressBar } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { GoalStatusBadge, PriorityBadge, ProjectStatusBadge, TaskStatusBadge } from '@/features/shared/badges'
import type { PlanGoalNode } from '@/lib/cofounder'
import type { Panel } from '@/lib/panels'

/** Goals -> projects (lead, progress) -> open tasks, exactly as the AI Co-founder's summary reports them. */
export function PlanTree({ nodes, agents, onOpen }: { nodes: PlanGoalNode[]; agents: Map<string, Agent>; onOpen: (p: Panel) => void }) {
  return (
    <ul aria-label="Active plan" className="divide-y divide-line">
      {nodes.map((n, i) => (
        <li key={n.goal?.id ?? `loose-${i}`} className="px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            {n.goal ? (
              <>
                <button type="button" onClick={() => onOpen({ kind: 'goals' })} className="min-w-0 flex-1 truncate text-left text-[13.5px] font-semibold text-fg hover:underline">
                  {n.goal.title}
                </button>
                <PriorityBadge priority={n.goal.priority} />
                <GoalStatusBadge status={n.goal.status} />
              </>
            ) : (
              <span className="text-[13px] font-medium text-fg-2">Projects without a current goal</span>
            )}
          </div>
          {n.projects.length === 0 ? (
            <p className="mt-1.5 text-[12.5px] text-fg-3">No projects yet. This goal is waiting to be planned.</p>
          ) : (
            <ul className="mt-2 space-y-2.5 border-l border-line pl-3">
              {n.projects.map(({ project: p, done, total, tasks, hiddenTasks }) => (
                <li key={p.id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" onClick={() => onOpen({ kind: 'goals' })} className="min-w-0 truncate text-left text-[13px] font-medium text-fg hover:underline">
                      {p.name}
                    </button>
                    <ProjectStatusBadge status={p.status} />
                    <span className="ml-auto flex items-center gap-1.5 text-[12px] text-fg-3">
                      Lead <AgentChip agentId={p.lead_agent_id} agents={agents} emptyLabel="none yet" nameClassName="text-fg-2" />
                    </span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-3">
                    <ProgressBar className="flex-1" value={done} max={total} tone={total > 0 && done === total ? 'success' : 'accent'} label={`${p.name} progress`} />
                    <span className="text-[12px] text-fg-3 tabular-nums">{total === 0 ? 'no tasks' : `${done}/${total} done`}</span>
                  </div>
                  {tasks.length > 0 && (
                    <ul className="mt-1.5 space-y-1 pl-1">
                      {tasks.map((t) => (
                        <li key={t.id} className="flex items-center gap-2 text-[12.5px]">
                          <button type="button" onClick={() => onOpen({ kind: 'task', id: t.id })} className="min-w-0 flex-1 truncate text-left text-fg-2 hover:text-fg hover:underline">
                            {t.title}
                          </button>
                          <TaskStatusBadge status={t.status} />
                          <AgentChip agentId={t.assignee_agent_id} agents={agents} hideName emptyLabel="–" />
                        </li>
                      ))}
                      {hiddenTasks > 0 && <li className="text-[12px] text-fg-3">and {hiddenTasks} more open task{hiddenTasks === 1 ? '' : 's'}</li>}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  )
}
