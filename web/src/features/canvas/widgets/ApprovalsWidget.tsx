import { useMemo } from 'react'
import { useApprovals } from '@/api'
import { Boundary, SectionLabel, Widget } from '@/components/ui'
import { ApprovalCard } from '@/features/approvals/ApprovalCard'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'

export function ApprovalsWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useApprovals(companyId, { status: 'pending' })
  const { map } = useAgentMap(companyId)
  const split = useMemo(() => {
    const items = q.data ?? []
    return { human: items.filter((a) => a.approver === 'human'), founder: items.filter((a) => a.approver === 'founder') }
  }, [q.data])

  return (
    <Widget
      title="Approvals"
      subtitle={q.data ? (split.human.length > 0 ? `${split.human.length} need you` : 'Nothing needs you') : undefined}
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
        {() => (
          <div>
            <section aria-label="Waiting for you">
              <SectionLabel className="border-b border-line bg-raised px-3.5 py-1.5">Needs you ({split.human.length})</SectionLabel>
              {split.human.length === 0 ? (
                <p className="px-3.5 py-3 text-[12.5px] text-fg-3">Nothing is waiting for you.</p>
              ) : (
                <div className="divide-y divide-line">
                  {split.human.map((a) => (
                    <ApprovalCard key={a.id} approval={a} agents={map} companyId={companyId} dense />
                  ))}
                </div>
              )}
            </section>
            <section aria-label="Decided by the AI Co-founder" className="border-t border-line">
              <SectionLabel className="border-b border-line bg-raised px-3.5 py-1.5">AI Co-founder decides ({split.founder.length})</SectionLabel>
              {split.founder.length === 0 ? (
                <p className="px-3.5 py-3 text-[12.5px] text-fg-3">No AI Co-founder approvals pending.</p>
              ) : (
                <div className="divide-y divide-line">
                  {split.founder.map((a) => (
                    <ApprovalCard key={a.id} approval={a} agents={map} companyId={companyId} dense />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </Boundary>
    </Widget>
  )
}
