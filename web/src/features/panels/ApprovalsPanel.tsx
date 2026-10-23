import { useState } from 'react'
import { useApprovals } from '@/api'
import type { ApprovalStatus } from '@/api/types'
import { Boundary, Select } from '@/components/ui'
import { ApprovalLanes } from '@/features/approvals/ApprovalLanes'
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
  return (
    <PanelFrame title="Approvals" subtitle="Decisions that need a human, the ones the AI Co-founder makes, and peer reviews, grouped by risk tier">
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
        {(items) => <ApprovalLanes framed items={items} agents={map} companyId={companyId} />}
      </Boundary>
    </PanelFrame>
  )
}
