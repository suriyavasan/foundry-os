import { useMemo, useState } from 'react'
import { useApprovals } from '@/api'
import type { ApprovalStatus } from '@/api/types'
import { Boundary, Card, SectionLabel, Select } from '@/components/ui'
import { ApprovalCard } from '@/features/approvals/ApprovalCard'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { titleCase } from '@/lib/format'

const STATUSES: Array<ApprovalStatus | 'all'> = ['pending', 'all', 'approved', 'rejected', 'changes_requested', 'expired']

export function ApprovalsPanel() {
  const { companyId } = useCanvas()
  const [status, setStatus] = useState<ApprovalStatus | 'all'>('pending')
  const q = useApprovals(companyId, status === 'all' ? {} : { status })
  const { map } = useAgentMap(companyId)
  const split = useMemo(() => {
    const items = [...(q.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))
    return { human: items.filter((a) => a.approver === 'human'), founder: items.filter((a) => a.approver === 'founder') }
  }, [q.data])

  return (
    <PanelFrame title="Approvals" subtitle="Decisions that need a human, and the ones the AI Founder makes">
      <div className="mb-3 flex items-center gap-2">
        <Select aria-label="Filter by status" className="h-7 w-auto text-[12.5px]" value={status} onChange={(e) => setStatus(e.target.value as ApprovalStatus | 'all')}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s === 'all' ? 'All statuses' : titleCase(s)}
            </option>
          ))}
        </Select>
      </div>
      <Boundary
        query={q}
        compact={false}
        unavailableTitle="Approvals are not built yet"
        unavailableText="The approval inbox comes with the work engine. Each approval authorises exactly one scoped action."
        isEmpty={(d) => d.length === 0}
        empty={{ title: status === 'pending' ? 'Nothing pending' : 'No approvals', description: 'The company only asks you about real-money commitments outside budget, as set in the policy.' }}
      >
        {() => (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionLabel className="border-b border-line bg-raised px-3.5 py-2">You decide ({split.human.length})</SectionLabel>
              {split.human.length === 0 ? <p className="px-3.5 py-4 text-[13px] text-fg-3">None.</p> : <div className="divide-y divide-line">{split.human.map((a) => <ApprovalCard key={a.id} approval={a} agents={map} companyId={companyId} />)}</div>}
            </Card>
            <Card>
              <SectionLabel className="border-b border-line bg-raised px-3.5 py-2">AI Founder decides ({split.founder.length})</SectionLabel>
              {split.founder.length === 0 ? <p className="px-3.5 py-4 text-[13px] text-fg-3">None.</p> : <div className="divide-y divide-line">{split.founder.map((a) => <ApprovalCard key={a.id} approval={a} agents={map} companyId={companyId} />)}</div>}
            </Card>
          </div>
        )}
      </Boundary>
    </PanelFrame>
  )
}
