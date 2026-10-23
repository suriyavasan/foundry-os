import { UserPlus } from 'lucide-react'
import { useMe, useMemberDirectory } from '@/api'
import { isWorkingMember } from '@/lib/people'
import { Button, EmptyState, ErrorState, LoadingState, Widget } from '@/components/ui'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'
import { OrgTree } from '../OrgTree'

export function OrgWidget({ className }: { className?: string }) {
  const { companyId, openPanel, create } = useCanvas()
  const { query, agents } = useAgentMap(companyId)
  const me = useMe()
  const directory = useMemberDirectory(companyId)
  const active = agents.filter((a) => a.status === 'active').length
  const humans = directory.available ? directory.members.filter((m) => isWorkingMember(m) && m.user_id !== me.data?.user.id).length : 0
  const working = agents.filter((a) => a.status === 'active' && a.live?.state === 'working').length

  return (
    <Widget
      title="Organization"
      subtitle={query.data ? `${agents.filter((a) => a.status !== 'retired' && a.status !== 'rejected').length + humans} people · ${active} active${working ? ` · ${working} working` : ''}` : undefined}
      tall
      onExpand={() => openPanel({ kind: 'org' })}
      expandLabel="Open organization"
      className={className}
      bodyClassName="overflow-hidden"
      actions={
        <Button size="sm" variant="ghost" icon={<UserPlus className="size-3.5" />} onClick={() => create('hire')} disabled={!query.data}>
          Hire
        </Button>
      }
    >
      {query.isPending ? (
        <LoadingState rows={6} label="Loading organization" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      ) : agents.length === 0 ? (
        <EmptyState title="No employees yet" description="Propose a hire to add the first employee." />
      ) : (
        <OrgTree
          agents={agents}
          ownerName={me.data?.user.display_name}
          onOpenAgent={(id) => openPanel({ kind: 'agent', id })}
          members={directory.available ? directory.members : undefined}
          selfUserId={me.data?.user.id}
          onOpenMember={() => openPanel({ kind: 'team' })}
        />
      )}
    </Widget>
  )
}
