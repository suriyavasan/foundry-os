import { Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useProjects, useTasks } from '@/api'
import type { TaskKind, TaskStatus } from '@/api/types'
import { Boundary, Button, Card, Input, Select, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { AgentChip } from '@/features/shared/AgentChip'
import { PriorityBadge, TaskStatusBadge } from '@/features/shared/badges'
import { formatRelative } from '@/lib/format'
import { PRIORITY_LABEL, TASK_STATUSES, TASK_STATUS_LABEL } from '@/lib/taskStatus'

const KINDS: TaskKind[] = ['work', 'review', 'planning', 'hiring', 'routine']
const PRIORITY_RANK = { urgent: 0, high: 1, normal: 2, low: 3 } as const

export function TasksPanel() {
  const { companyId, openPanel, create } = useCanvas()
  const [status, setStatus] = useState<TaskStatus | ''>('')
  const [assignee, setAssignee] = useState('')
  const [project, setProject] = useState('')
  const [kind, setKind] = useState<TaskKind | ''>('')
  const [search, setSearch] = useState('')
  const filters = useMemo(
    () => ({ ...(status ? { status } : null), ...(assignee ? { assignee_agent_id: assignee } : null), ...(project ? { project_id: project } : null), ...(kind ? { kind } : null) }),
    [status, assignee, project, kind],
  )
  const q = useTasks(companyId, filters)
  const projects = useProjects(companyId)
  const { agents, map } = useAgentMap(companyId)
  const projectName = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.name])), [projects.data])

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (q.data ?? [])
      .filter((t) => !needle || t.title.toLowerCase().includes(needle))
      .sort((a, b) => TASK_STATUSES.indexOf(a.status) - TASK_STATUSES.indexOf(b.status) || PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || b.updated_at.localeCompare(a.updated_at))
  }, [q.data, search])

  return (
    <PanelFrame
      title="Tasks"
      subtitle="Everything the company is working on"
      actions={
        <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => create('task')} disabled={!q.data}>
          Create task
        </Button>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input aria-label="Search tasks" placeholder="Search titles…" className="h-7 w-48 text-[12.5px]" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Select aria-label="Filter by status" className="h-7 w-auto text-[12.5px]" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus | '')}>
          <option value="">All statuses</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {TASK_STATUS_LABEL[s]}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by assignee" className="h-7 w-auto text-[12.5px]" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Any assignee</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by project" className="h-7 w-auto text-[12.5px]" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">Any project</option>
          {(projects.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by kind" className="h-7 w-auto text-[12.5px]" value={kind} onChange={(e) => setKind(e.target.value as TaskKind | '')}>
          <option value="">Any kind</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
        {q.data && <span className="text-[12px] text-fg-3">{rows.length} shown</span>}
      </div>
      <Card>
        <Boundary
          query={q}
          compact={false}
          unavailableTitle="Tasks are not built yet"
          unavailableText="Tasks, dependencies and execution runs come with the work engine."
          isEmpty={() => rows.length === 0}
          empty={{ title: Object.keys(filters).length || search ? 'No tasks match' : 'No tasks yet', description: Object.keys(filters).length || search ? 'Adjust the filters.' : 'Create a task, or let the AI Co-founder plan the work.' }}
        >
          {() => (
            <Table>
              <THead>
                <TR>
                  <TH>Task</TH>
                  <TH>Status</TH>
                  <TH>Priority</TH>
                  <TH>Assignee</TH>
                  <TH>Project</TH>
                  <TH>Updated</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((t) => (
                  <TR key={t.id} interactive>
                    <TD className="max-w-[320px]">
                      <button type="button" onClick={() => openPanel({ kind: 'task', id: t.id })} className="block max-w-full truncate text-left font-medium hover:underline">
                        {t.title}
                      </button>
                    </TD>
                    <TD>
                      <TaskStatusBadge status={t.status} />
                    </TD>
                    <TD>
                      <PriorityBadge priority={t.priority} />
                      <span className="sr-only">{PRIORITY_LABEL[t.priority]}</span>
                    </TD>
                    <TD>
                      <AgentChip agentId={t.assignee_agent_id} agents={map} />
                    </TD>
                    <TD className="text-fg-2">{(t.project_id && projectName.get(t.project_id)) || <span className="text-fg-3">—</span>}</TD>
                    <TD className="text-fg-3">{formatRelative(t.updated_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Boundary>
      </Card>
    </PanelFrame>
  )
}
