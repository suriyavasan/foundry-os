import clsx from 'clsx'
import { AlertTriangle } from 'lucide-react'
import { useMemo } from 'react'
import { isNotDeployed, useAllTasks, useOutsourceJobs } from '@/api'
import type { Agent, Member } from '@/api/types'
import { AgentAvatar, Avatar, MemberAvatar } from '@/components/avatar'
import { RING_STYLE, ringStateFor } from '@/lib/liveState'
import { MARKETPLACE_LABEL, OUTSOURCE_STATUS_LABEL, isContractor } from '@/lib/outsourcing'
import { formatCost } from '@/lib/money'
import { agentStateText, currentTaskTitle, orgListRows, type OrgListRow } from '@/lib/orgList'
import { OrgIssueText } from '@/lib/orgIssue'
import type { OrgPerson } from '@/lib/orgTree'
import { openTasksOfMember, roleLabel } from '@/lib/people'
import { shortRole } from '@/lib/roleFamily'
import { AI_COFOUNDER_LABEL, YOU_FOUNDER_LABEL, displayRole, isCofounder } from '@/lib/terms'
import { useCanvas } from './CanvasContext'
import { useCompanyTimezone } from './hooks'
import { useAgentSpendToday } from './spend'

const STATE_TEXT_CLASS: Partial<Record<string, string>> = {
  working: 'text-accent-fg',
  blocked: 'text-warning',
  failed: 'text-danger',
  proposed: 'text-info',
  paused: 'text-fg-3',
  offline: 'text-fg-3',
}

/** Dense rows grouped by manager: face, name, role, what they are doing, and today's spend when the backend reports it. */
export function OrgList({
  roots,
  ownerName,
  ownerSeed,
  onOpenAgent,
  onOpenMember,
}: {
  roots: OrgPerson[]
  ownerName?: string
  ownerSeed?: string
  onOpenAgent: (id: string) => void
  onOpenMember?: (memberId: string) => void
}) {
  const rows = useMemo(() => orgListRows(roots), [roots])
  return (
    <ul aria-label="People" className="divide-y divide-line">
      <li className="flex items-center gap-2.5 py-1.5 pr-3" style={indentStyle(0)}>
        <Avatar family="human" size="sm" seed={ownerSeed ?? 'founder'} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] leading-4 font-medium text-fg">{YOU_FOUNDER_LABEL}</span>
          {ownerName && <span className="block truncate text-[11.5px] leading-4 text-fg-3">{ownerName}</span>}
        </span>
      </li>
      {rows.map((r) => (
        <li key={r.id}>{r.person.agent ? <AgentRow row={r} agent={r.person.agent} onOpen={() => onOpenAgent(r.id)} /> : r.person.member ? <MemberRow row={r} member={r.person.member} onOpen={() => onOpenMember?.(r.person.member!.id)} /> : null}</li>
      ))}
      <ContractorRows />
    </ul>
  )
}

/**
 * M22 "Contractors": freelancers HR hired through a marketplace (hired or delivered, not yet closed). They are not employees and are
 * not on the map; a click opens the job. Nothing shows while loading or on a server without outsourcing.
 */
function ContractorRows() {
  const { companyId, openPanel } = useCanvas()
  const jobs = useOutsourceJobs(companyId)
  if (jobs.isPending || (jobs.isError && isNotDeployed(jobs.error))) return null
  if (jobs.isError) {
    return (
      <li role="alert" className="px-3.5 py-1.5 text-[11.5px] text-fg-3">
        Contractors could not be loaded.
      </li>
    )
  }
  const contractors = (jobs.data ?? []).filter(isContractor)
  if (contractors.length === 0) return null
  return (
    <li>
      <h4 className="px-3.5 pt-2.5 pb-1 text-[11px] font-medium tracking-wide text-fg-3 uppercase">Contractors</h4>
      <ul aria-label="Contractors" className="divide-y divide-line">
        {contractors.map((j) => (
          <li key={j.id}>
            <button type="button" aria-label={`${j.freelancer_name}, contractor, ${j.title}`} onClick={() => openPanel({ kind: 'outsource', id: j.id })} className={rowClass} style={indentStyle(0)}>
              <Avatar family="generic" size="sm" seed={j.freelancer_name ?? j.id} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] leading-4 font-medium text-fg">{j.freelancer_name}</span>
                <span className="block truncate text-[11.5px] leading-4 text-fg-3">
                  {j.title} · {MARKETPLACE_LABEL[j.marketplace]}
                </span>
              </span>
              <span className="max-w-[44%] truncate text-right text-[12px] text-fg-3">{OUTSOURCE_STATUS_LABEL[j.status]}</span>
            </button>
          </li>
        ))}
      </ul>
    </li>
  )
}

const rowClass = 'flex w-full items-center gap-2.5 py-1.5 pr-3 text-left transition-colors hover:bg-hover/60 focus-visible:bg-hover/60 focus-visible:outline-none'
/** One level of indentation per manager, capped so a deep chain still leaves room for the text. */
const indentStyle = (indent: number) => ({ paddingLeft: 14 + Math.min(indent, 4) * 16 })

function AgentRow({ row, agent, onOpen }: { row: OrgListRow; agent: Agent; onOpen: () => void }) {
  const { companyId } = useCanvas()
  const tasks = useAllTasks(companyId)
  const tz = useCompanyTimezone(companyId)
  const spend = useAgentSpendToday(companyId, agent.id, tz)
  const ring = ringStateFor(agent)
  const state = agentStateText(agent, currentTaskTitle(agent, tasks.data))
  const label = [agent.name, displayRole(agent), RING_STYLE[ring].label].join(', ')
  return (
    <button type="button" aria-label={label} onClick={onOpen} className={rowClass} style={indentStyle(row.indent)}>
      <AgentAvatar agent={agent} size="sm" />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1 text-[13px] leading-4 font-medium text-fg">
          <span className="truncate">{agent.name}</span>
          {row.person.issue && <AlertTriangle className="size-3 shrink-0 text-warning" aria-label={OrgIssueText[row.person.issue]} />}
        </span>
        <span className="block truncate text-[11.5px] leading-4 text-fg-3">{isCofounder(agent) ? AI_COFOUNDER_LABEL : shortRole(displayRole(agent), 28)}</span>
      </span>
      <span className={clsx('max-w-[44%] truncate text-right text-[12px]', STATE_TEXT_CLASS[ring] ?? 'text-fg-3')}>{state}</span>
      {!spend.unavailable && !spend.loading && spend.row && (
        <span className="shrink-0 text-right text-[11.5px] text-fg-3 tabular-nums" title="Spent today">
          {formatCost(spend.row.by_kind.verified, 'verified')}
          {spend.row.by_kind.estimated > 0 && <span className="block">{formatCost(spend.row.by_kind.estimated, 'estimated')}</span>}
        </span>
      )}
    </button>
  )
}

function MemberRow({ row, member, onOpen }: { row: OrgListRow; member: Member; onOpen: () => void }) {
  const { companyId } = useCanvas()
  const tasks = useAllTasks(companyId)
  const open = tasks.data ? openTasksOfMember(tasks.data, member.id).length : 0
  const role = member.title ?? roleLabel(member.role)
  return (
    <button type="button" aria-label={[member.display_name, role, 'Human'].join(', ')} onClick={onOpen} className={rowClass} style={indentStyle(row.indent)}>
      <MemberAvatar name={member.display_name} seed={member.id} size="sm" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] leading-4 font-medium text-fg">{member.display_name}</span>
        <span className="block truncate text-[11.5px] leading-4 text-fg-3">{shortRole(role, 28)}</span>
      </span>
      <span className="max-w-[44%] truncate text-right text-[12px] text-fg-3">{open > 0 ? `${open} open task${open === 1 ? '' : 's'}` : 'Human'}</span>
    </button>
  )
}
