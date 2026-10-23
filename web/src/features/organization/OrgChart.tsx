import clsx from 'clsx'
import { AlertTriangle, User } from 'lucide-react'
import { Link } from 'react-router'
import type { Agent, Department } from '@/api/types'
import { Chip, StatusBadge, Tooltip } from '@/components/ui'
import type { OrgIssue, OrgNode } from '@/lib/orgTree'

const ISSUE_TEXT: Record<OrgIssue, string> = {
  no_manager: 'No manager assigned',
  missing_manager: 'Manager is not in this company',
  cycle: 'Part of a reporting cycle',
}

function NodeCard({
  node,
  base,
  departments,
  dimmed,
}: {
  node: OrgNode
  base: string
  departments: Map<string, Department>
  dimmed: boolean
}) {
  const a = node.agent
  const dept = a.department_id ? departments.get(a.department_id) : undefined
  return (
    <Link
      to={`${base}/organization/agents/${a.id}`}
      className={clsx(
        'flex min-w-0 items-center gap-3 rounded-md border border-line bg-surface px-3 py-2 transition-colors hover:border-line-strong hover:bg-hover',
        dimmed && 'opacity-40',
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate text-[13px] font-semibold text-fg">{a.name}</span>
          {a.kind === 'founder' && <Chip>founder</Chip>}
          {node.issue && (
            <Tooltip content={ISSUE_TEXT[node.issue]}>
              <AlertTriangle className="size-3.5 text-warning" aria-label={ISSUE_TEXT[node.issue]} />
            </Tooltip>
          )}
        </div>
        <div className="truncate text-[12px] text-fg-3">
          {a.role_title}
          {dept ? ` · ${dept.name}` : ''}
        </div>
      </div>
      <StatusBadge status={a.status} />
    </Link>
  )
}

function Branch({
  nodes,
  base,
  departments,
  matches,
}: {
  nodes: OrgNode[]
  base: string
  departments: Map<string, Department>
  matches: (a: Agent) => boolean
}) {
  return (
    <ul className="ml-4 space-y-2 border-l border-line pl-5">
      {nodes.map((n) => (
        <li
          key={n.agent.id}
          className="relative before:absolute before:top-[22px] before:-left-5 before:h-px before:w-5 before:bg-line"
        >
          <NodeCard node={n} base={base} departments={departments} dimmed={!matches(n.agent)} />
          {n.children.length > 0 && (
            <div className="mt-2">
              <Branch nodes={n.children} base={base} departments={departments} matches={matches} />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

export function OrgChart({
  roots,
  base,
  departments,
  matches,
  ownerName,
}: {
  roots: OrgNode[]
  base: string
  departments: Department[]
  matches: (a: Agent) => boolean
  ownerName?: string
}) {
  const deptMap = new Map(departments.map((d) => [d.id, d]))
  return (
    <div className="p-4">
      <div className="flex w-fit max-w-full items-center gap-3 rounded-md border border-line-strong bg-raised px-3 py-2">
        <span className="flex size-7 items-center justify-center rounded-full bg-accent-subtle text-accent-fg">
          <User className="size-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-fg">You</div>
          <div className="truncate text-[12px] text-fg-3">Human co-founder{ownerName ? ` · ${ownerName}` : ''}</div>
        </div>
      </div>
      <Branch nodes={roots} base={base} departments={deptMap} matches={matches} />
    </div>
  )
}
