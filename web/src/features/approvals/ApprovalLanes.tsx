import { useMemo } from 'react'
import type { Agent, Approval } from '@/api/types'
import { Card, SectionLabel } from '@/components/ui'
import { TierBadge } from '@/features/guardrails/DecisionChip'
import { LANE_LABEL, LANE_ORDER, groupByTier, splitByLane, type ApprovalLane } from '@/lib/approvalLanes'
import { ApprovalCard } from './ApprovalCard'

const EMPTY: Record<ApprovalLane, string> = {
  human: 'Nothing is waiting for you.',
  cofounder: 'No AI Co-founder approvals pending.',
  peer: 'No peer reviews pending.',
}
const REGION: Record<ApprovalLane, string> = { human: 'Waiting for you', cofounder: 'Decided by the AI Co-founder', peer: 'Waiting for a peer reviewer' }
const WHY: Record<ApprovalLane, string> = {
  human: 'Needs you',
  cofounder: 'AI Co-founder decides',
  peer: 'Peer review',
}

/** The cards of one lane, grouped by risk tier when any of them carries one. */
function LaneCards({ items, agents, companyId, dense }: { items: Approval[]; agents: Map<string, Agent>; companyId: string; dense: boolean }) {
  const groups = useMemo(() => groupByTier(items), [items])
  const tiered = groups.some((g) => g.tier !== null)
  if (!tiered) {
    return (
      <div className="divide-y divide-line">
        {groups.flatMap((g) => g.items).map((a) => (
          <ApprovalCard key={a.id} approval={a} agents={agents} companyId={companyId} dense={dense} />
        ))}
      </div>
    )
  }
  return (
    <div>
      {groups.map((g) => (
        <div key={g.tier ?? 'none'} role="group" aria-label={g.tier ? `Tier ${g.tier}` : 'No tier'}>
          <div className="flex items-center gap-2 border-b border-line bg-canvas px-3.5 py-1">
            {g.tier ? <TierBadge tier={g.tier} /> : <span className="text-[11.5px] text-fg-3">No risk tier</span>}
            <span className="text-[11.5px] text-fg-3">{g.items.length}</span>
          </div>
          <div className="divide-y divide-line border-b border-line last:border-b-0">
            {g.items.map((a) => (
              <ApprovalCard key={a.id} approval={a} agents={agents} companyId={companyId} dense={dense} showTier={false} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * Pending (or any) approvals split by who decides (you, the AI Co-founder, a peer reviewer) and, inside each, by risk
 * tier. The peer lane only appears when it has items: it is the maker-checker queue, informational for the human.
 */
export function ApprovalLanes({ items, agents, companyId, dense = false, framed = false }: { items: Approval[]; agents: Map<string, Agent>; companyId: string; dense?: boolean; framed?: boolean }) {
  const lanes = useMemo(() => splitByLane(items), [items])
  const visible = LANE_ORDER.filter((l) => l !== 'peer' || lanes.peer.length > 0)
  return (
    <div className={framed ? `grid gap-4 ${visible.length > 2 ? 'xl:grid-cols-3 lg:grid-cols-2' : 'lg:grid-cols-2'}` : undefined}>
      {visible.map((lane) => {
        const content = (
          <>
            <SectionLabel className="border-b border-line bg-raised px-3.5 py-1.5">
              {framed ? LANE_LABEL[lane] : WHY[lane]} ({lanes[lane].length})
            </SectionLabel>
            {lanes[lane].length === 0 ? <p className="px-3.5 py-3 text-[12.5px] text-fg-3">{EMPTY[lane]}</p> : <LaneCards items={lanes[lane]} agents={agents} companyId={companyId} dense={dense} />}
          </>
        )
        return framed ? (
          <Card key={lane} aria-label={REGION[lane]}>
            {content}
          </Card>
        ) : (
          <section key={lane} aria-label={REGION[lane]} className="border-t border-line first:border-t-0">
            {content}
          </section>
        )
      })}
    </div>
  )
}
