import clsx from 'clsx'
import { useMemo } from 'react'
import { useMemberDirectory } from '@/api'
import type { Agent, Member, Task } from '@/api/types'
import { MemberAvatar, type AvatarSize } from '@/components/avatar'
import { taskAssignee } from '@/lib/people'
import { AgentChip } from './AgentChip'

/** Members by id, for chips and labels. Empty when the backend has no members endpoint. */
export function useMemberMap(companyId: string): Map<string, Member> {
  const { members } = useMemberDirectory(companyId)
  return useMemo(() => new Map(members.map((m) => [m.id, m])), [members])
}

/** Who a task is assigned to: an agent or (M7) a human member, with honest fallbacks. */
export function AssigneeChip({
  task,
  agents,
  members,
  size = 'xs',
  className,
  nameClassName,
  hideName,
  emptyLabel = 'Unassigned',
}: {
  task: Pick<Task, 'assignee_ref' | 'assignee_agent_id'>
  agents: Map<string, Agent>
  members: Map<string, Member>
  size?: AvatarSize
  className?: string
  nameClassName?: string
  hideName?: boolean
  emptyLabel?: string
}) {
  const who = taskAssignee(task)
  if (who?.type !== 'member') {
    return <AgentChip agentId={who?.id ?? null} agents={agents} size={size} className={className} nameClassName={nameClassName} hideName={hideName} emptyLabel={emptyLabel} />
  }
  const member = members.get(who.id)
  if (!member) return <span className={clsx('text-fg-3', className)}>Unknown member</span>
  return (
    <span className={clsx('inline-flex min-w-0 items-center gap-1.5', className)}>
      <MemberAvatar name={member.display_name} seed={member.id} size={size} />
      {!hideName && <span className={clsx('truncate', nameClassName)}>{member.display_name}</span>}
      {hideName && <span className="sr-only">{member.display_name}</span>}
    </span>
  )
}

/** Plain-text name of the assignee (for lines that are not chips). */
export function assigneeName(task: Pick<Task, 'assignee_ref' | 'assignee_agent_id'>, agents: Map<string, Agent>, members: Map<string, Member>): string {
  const who = taskAssignee(task)
  if (!who) return 'Unassigned'
  if (who.type === 'member') return members.get(who.id)?.display_name ?? 'Unknown member'
  return agents.get(who.id)?.name ?? 'Unknown agent'
}
