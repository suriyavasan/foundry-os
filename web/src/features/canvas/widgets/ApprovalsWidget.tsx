import { useMemo } from 'react'
import { useApprovals } from '@/api'
import { Boundary, Widget } from '@/components/ui'
import { ApprovalLanes } from '@/features/approvals/ApprovalLanes'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'

export function ApprovalsWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useApprovals(companyId, { status: 'pending' })
  const { map } = useAgentMap(companyId)
  const split = useMemo(() => {
    const items = q.data ?? []
    return { human: items.filter((a) => a.approver === 'human'), peer: items.filter((a) => a.approver === 'peer') }
  }, [q.data])

  return (
    <Widget
      title="Approvals"
      subtitle={q.data ? (split.human.length > 0 ? `${split.human.length} need you` : 'Nothing needs you') + (split.peer.length > 0 ? ` · ${split.peer.length} in peer review` : '') : undefined}
      onExpand={() => openPanel({ kind: 'approvals' })}
      expandLabel="Open approvals"
      className={className}
    >
      <Boundary
        query={q}
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No pending approvals', description: 'The company only asks you about real-money commitments outside budget, as set in the policy.' }}
        unavailableText="Approvals come with the work engine."
      >
        {(items) => <ApprovalLanes items={items} agents={map} companyId={companyId} dense />}
      </Boundary>
    </Widget>
  )
}
