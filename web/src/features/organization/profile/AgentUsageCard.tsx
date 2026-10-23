import type { Agent } from '@/api/types'
import { useUsage } from '@/api'
import { Boundary, Card, CardBody, CardHeader } from '@/components/ui'
import { useCompanyDates } from '@/features/canvas/spend'
import { useCompanyTimezone } from '@/features/canvas/hooks'
import { SpendBlock } from '@/features/finance/SpendBlock'
import { emptyRow } from '@/lib/usage'

/** This agent's spend today and this month against its own limits, split verified vs estimated. */
export function AgentUsageCard({ agent }: { agent: Agent }) {
  const tz = useCompanyTimezone(agent.company_id)
  const { today, monthStart } = useCompanyDates(tz)
  const q = useUsage(agent.company_id, 'agent', monthStart, today)
  const todayQ = useUsage(agent.company_id, 'agent', today, today)
  return (
    <Card>
      <CardHeader title="Usage" description="Verified charges and estimates are shown separately." />
      <Boundary query={q} unavailableText="The usage ledger comes with the work engine. Limits are stored but not enforced until then.">
        {(report) => {
          const month = report.rows.find((r) => r.key === agent.id) ?? emptyRow(agent.id)
          const day = todayQ.data?.rows.find((r) => r.key === agent.id) ?? emptyRow(agent.id)
          return (
            <CardBody className="space-y-4">
              <SpendBlock title="Today" row={day} limitMicros={agent.daily_budget_micros} />
              <div className="border-t border-line pt-4">
                <SpendBlock title="This month" row={month} limitMicros={agent.monthly_budget_micros} />
              </div>
            </CardBody>
          )
        }}
      </Boundary>
    </Card>
  )
}
