import { List, Network, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { useMe, useMemberDirectory } from '@/api'
import { isWorkingMember } from '@/lib/people'
import { Button, EmptyState, ErrorState, LoadingState, Segmented, Widget } from '@/components/ui'
import { getOrgView, setOrgView, type OrgView } from '@/lib/storage'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'
import { OrgList } from '../OrgList'
import { OrgMap, useOrgRoots } from '../OrgMap'

const VIEWS = [
  { value: 'map', label: 'Map', icon: <Network className="size-3.5" aria-hidden /> },
  { value: 'list', label: 'List', icon: <List className="size-3.5" aria-hidden /> },
] as const

/**
 * The organization, compact: a radial map of faces (default) or a list grouped by manager. The choice is remembered in this
 * browser. The full tree, with filters and the table, lives in the Organization panel.
 */
export function OrgWidget({ className }: { className?: string }) {
  const { companyId, openPanel, create } = useCanvas()
  const { query, agents } = useAgentMap(companyId)
  const me = useMe()
  const directory = useMemberDirectory(companyId)
  const [view, setView] = useState<OrgView>(getOrgView)
  const members = directory.available ? directory.members : undefined
  const selfUserId = me.data?.user.id
  const roots = useOrgRoots(agents, members, selfUserId)
  const active = agents.filter((a) => a.status === 'active').length
  const humans = members ? members.filter((m) => isWorkingMember(m) && m.user_id !== selfUserId).length : 0
  const working = agents.filter((a) => a.status === 'active' && a.live?.state === 'working').length
  const selfMember = members?.find((m) => m.user_id === selfUserId)

  return (
    <Widget
      title="Organization"
      subtitle={query.data ? `${agents.filter((a) => a.status !== 'retired' && a.status !== 'rejected').length + humans} people · ${active} active${working ? ` · ${working} working` : ''}` : undefined}
      chat
      onExpand={() => openPanel({ kind: 'org' })}
      expandLabel="Open organization"
      className={className}
      bodyClassName="overflow-hidden"
      actions={
        <>
          <Segmented
            label="Organization view"
            value={view}
            options={VIEWS}
            onChange={(v) => {
              setView(v)
              setOrgView(v)
            }}
          />
          <Button size="sm" variant="ghost" icon={<UserPlus className="size-3.5" />} onClick={() => create('hire')} disabled={!query.data}>
            Hire
          </Button>
        </>
      }
    >
      {query.isPending ? (
        <LoadingState rows={6} label="Loading organization" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      ) : agents.length === 0 ? (
        <EmptyState title="No employees yet" description="Propose a hire to add the first employee." />
      ) : view === 'map' ? (
        <OrgMap roots={roots} agents={agents} members={members} ownerName={me.data?.user.display_name} selfUserId={selfUserId} onOpenAgent={(id) => openPanel({ kind: 'agent', id })} onOpenMember={() => openPanel({ kind: 'team' })} />
      ) : (
        <div className="h-full overflow-y-auto">
          <OrgList roots={roots} ownerName={me.data?.user.display_name} ownerSeed={selfMember?.id ?? selfUserId} onOpenAgent={(id) => openPanel({ kind: 'agent', id })} onOpenMember={() => openPanel({ kind: 'team' })} />
        </div>
      )}
    </Widget>
  )
}
