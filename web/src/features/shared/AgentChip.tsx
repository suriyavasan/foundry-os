import clsx from 'clsx'
import type { Agent, CreatedByType } from '@/api/types'
import { AgentAvatar, Avatar, type AvatarSize } from '@/components/avatar'

/** Avatar + name for an agent id; honest fallbacks for unassigned or unknown agents. */
export function AgentChip({
  agentId,
  agents,
  size = 'xs',
  className,
  nameClassName,
  hideName,
  emptyLabel = 'Unassigned',
}: {
  agentId: string | null | undefined
  agents: Map<string, Agent>
  size?: AvatarSize
  className?: string
  nameClassName?: string
  hideName?: boolean
  emptyLabel?: string
}) {
  const agent = agentId ? agents.get(agentId) : undefined
  if (!agentId) return <span className={clsx('text-fg-3', className)}>{emptyLabel}</span>
  if (!agent) return <span className={clsx('text-fg-3', className)}>Unknown agent</span>
  return (
    <span className={clsx('inline-flex min-w-0 items-center gap-1.5', className)}>
      <AgentAvatar agent={agent} size={size} />
      {!hideName && <span className={clsx('truncate', nameClassName)}>{agent.name}</span>}
      {hideName && <span className="sr-only">{agent.name}</span>}
    </span>
  )
}

/** Who created something: an agent, the human owner, or the system. */
export function CreatorChip({
  type,
  id,
  agents,
  size = 'xs',
  className,
}: {
  type: CreatedByType
  id: string | null
  agents: Map<string, Agent>
  size?: AvatarSize
  className?: string
}) {
  if (type === 'agent') return <AgentChip agentId={id} agents={agents} size={size} className={className} />
  if (type === 'human')
    return (
      <span className={clsx('inline-flex items-center gap-1.5', className)}>
        <Avatar family="human" size={size} />
        You
      </span>
    )
  return <span className={clsx('text-fg-3', className)}>System</span>
}
