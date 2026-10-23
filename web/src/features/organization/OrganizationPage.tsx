import { FolderPlus, LayoutList, Network, Pencil, UserPlus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useAgents, useDepartments, useMe } from '@/api'
import type { Agent, AgentStatus, Department } from '@/api/types'
import { Page } from '@/components/layout/Page'
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Select,
  Tooltip,
  agentStatusLabel,
} from '@/components/ui'
import { useCompanyId } from '@/features/shell/useCompanyId'
import { buildOrgTree, flattenOrgTree } from '@/lib/orgTree'
import { AgentsTable } from './AgentsTable'
import { DepartmentDialog } from './DepartmentDialog'
import { HireDialog } from './HireDialog'
import { OrgChart } from './OrgChart'

type View = 'chart' | 'table'
type StatusFilter = 'current' | 'all' | AgentStatus

const STATUSES: AgentStatus[] = ['active', 'proposed', 'paused', 'retiring', 'retired', 'rejected']

export function statusMatches(filter: StatusFilter, status: AgentStatus): boolean {
  if (filter === 'all') return true
  if (filter === 'current') return status !== 'retired' && status !== 'rejected'
  return status === filter
}

export function OrganizationPage() {
  const companyId = useCompanyId()
  const base = `/c/${companyId}`
  const agents = useAgents(companyId)
  const departments = useDepartments(companyId)
  const me = useMe()

  const [view, setView] = useState<View>('chart')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('current')
  const [deptFilter, setDeptFilter] = useState<string>('all')
  const [hireOpen, setHireOpen] = useState(false)
  const [deptDialog, setDeptDialog] = useState<{ open: boolean; department?: Department }>({ open: false })

  const matches = useMemo(
    () => (a: Agent) =>
      statusMatches(statusFilter, a.status) &&
      (deptFilter === 'all' || (deptFilter === 'none' ? !a.department_id : a.department_id === deptFilter)),
    [statusFilter, deptFilter],
  )

  const tree = useMemo(() => buildOrgTree(agents.data ?? []), [agents.data])
  const filtered = useMemo(() => (agents.data ?? []).filter(matches), [agents.data, matches])
  const tableRows = useMemo(() => {
    const order = flattenOrgTree(tree).map((n) => n.agent)
    return order.filter(matches)
  }, [tree, matches])

  const loading = agents.isPending || departments.isPending
  const error = agents.error ?? departments.error
  const ready = agents.data && departments.data

  return (
    <Page
      title="Organization"
      description="Who works here, who they report to, and what state they are in."
      actions={
        <>
          <Button
            icon={<FolderPlus className="size-3.5" />}
            disabled={!ready}
            onClick={() => setDeptDialog({ open: true })}
          >
            New department
          </Button>
          <Tooltip content={!ready ? 'Loading organization…' : undefined}>
            <Button variant="primary" icon={<UserPlus className="size-3.5" />} disabled={!ready} onClick={() => setHireOpen(true)}>
              Propose hire
            </Button>
          </Tooltip>
        </>
      }
    >
      {departments.data && departments.data.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-[12px] font-medium tracking-wide text-fg-3 uppercase">Departments</span>
          {departments.data.map((d) => (
            <button
              key={d.id}
              type="button"
              title={d.description ?? `Edit ${d.name}`}
              onClick={() => setDeptDialog({ open: true, department: d })}
              className="group inline-flex h-7 items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[12.5px] text-fg hover:bg-hover"
            >
              {d.name}
              <span className="text-fg-3 tabular-nums">{d.agent_count}</span>
              <Pencil className="size-3 text-fg-3 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" aria-label={`Edit ${d.name}`} />
            </button>
          ))}
        </div>
      )}

      <Card>
        <CardHeader
          title={view === 'chart' ? 'Org chart' : 'Agents'}
          description={ready ? `${filtered.length} of ${agents.data.length} shown` : undefined}
          actions={
            <>
              <Select
                aria-label="Filter by status"
                className="h-7 w-auto text-[12.5px]"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              >
                <option value="current">Current (hide retired)</option>
                <option value="all">All statuses</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {agentStatusLabel[s]}
                  </option>
                ))}
              </Select>
              <Select
                aria-label="Filter by department"
                className="h-7 w-auto text-[12.5px]"
                value={deptFilter}
                onChange={(e) => setDeptFilter(e.target.value)}
              >
                <option value="all">All departments</option>
                <option value="none">No department</option>
                {departments.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
              <div role="group" aria-label="View" className="flex rounded-md border border-line-strong p-0.5">
                {(
                  [
                    ['chart', 'Chart', Network],
                    ['table', 'Table', LayoutList],
                  ] as const
                ).map(([v, label, Icon]) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={view === v}
                    onClick={() => setView(v)}
                    className={
                      'inline-flex h-6 items-center gap-1.5 rounded-sm px-2 text-[12.5px] font-medium ' +
                      (view === v ? 'bg-active text-fg' : 'text-fg-3 hover:text-fg')
                    }
                  >
                    <Icon className="size-3.5" aria-hidden />
                    {label}
                  </button>
                ))}
              </div>
            </>
          }
        />
        {loading ? (
          <LoadingState rows={6} label="Loading organization" />
        ) : error ? (
          <ErrorState
            error={error}
            onRetry={() => {
              void agents.refetch()
              void departments.refetch()
            }}
            retrying={agents.isFetching || departments.isFetching}
          />
        ) : agents.data!.length === 0 ? (
          <EmptyState title="No agents yet" description="Propose a hire to add the first employee." />
        ) : view === 'chart' ? (
          <>
            <OrgChart
              roots={tree}
              base={base}
              departments={departments.data!}
              matches={matches}
              ownerName={me.data?.user.display_name}
            />
            {filtered.length === 0 && (
              <p className="border-t border-line px-4 py-3 text-[12.5px] text-fg-3">No agents match the current filters.</p>
            )}
          </>
        ) : tableRows.length === 0 ? (
          <EmptyState title="No agents match" description="Adjust the status or department filter." />
        ) : (
          <AgentsTable agents={tableRows} departments={departments.data!} allAgents={agents.data!} base={base} />
        )}
      </Card>

      {hireOpen && ready && (
        <HireDialog
          open
          onOpenChange={setHireOpen}
          companyId={companyId}
          agents={agents.data}
          departments={departments.data}
        />
      )}
      {deptDialog.open && agents.data && (
        <DepartmentDialog
          open
          onOpenChange={(o) => !o && setDeptDialog({ open: false })}
          companyId={companyId}
          agents={agents.data}
          department={deptDialog.department}
        />
      )}
    </Page>
  )
}
