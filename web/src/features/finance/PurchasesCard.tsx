import { isNotDeployed, useWallet } from '@/api'
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, LoadingState, NotAvailable, SectionLabel, Stat } from '@/components/ui'
import { useOpenWallet } from '@/features/wallet/useWalletNav'
import { budgetUse } from '@/features/wallet/walletLogic'
import { formatUsd } from '@/lib/money'

/**
 * What agents bought this month (M15). Purchases are kept apart from model spend: the budgets above never include them. Only
 * settled, verified charges are called "spent"; money that is merely committed to an approved purchase is shown separately.
 */
export function PurchasesCard({ companyId }: { companyId: string }) {
  const q = useWallet(companyId)
  const openWallet = useOpenWallet()
  return (
    <Card aria-label="Purchases">
      <CardHeader
        title="Purchases"
        description="Things agents bought with a card. Separate from model spend: the budgets above never include them."
        actions={
          <Button size="sm" onClick={() => openWallet('purchases')}>
            Open agent cards
          </Button>
        }
      />
      {q.isPending ? (
        <LoadingState rows={2} label="Loading purchases" />
      ) : q.isError ? (
        isNotDeployed(q.error) ? (
          <NotAvailable compact className="p-4" milestone="M15" description="Agent purchases come with M15." />
        ) : (
          <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        )
      ) : !q.data.available ? (
        <NotAvailable compact className="p-4" milestone={q.data.milestone ?? 'M15'} description="Agent purchases come with M15." />
      ) : q.data.accounts.length === 0 && q.data.month.settled_verified_micros === 0 && q.data.month.held_micros === 0 ? (
        <CardBody>
          <p className="text-[13px] text-fg-3">Agents have not bought anything. Set up agent cards to let them request purchases.</p>
        </CardBody>
      ) : (
        <CardBody className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Settled this month" value={formatUsd(q.data.month.settled_verified_micros)} hint={<Badge tone="success">Verified</Badge>} />
            <Stat label="Committed" value={formatUsd(q.data.month.held_micros)} hint="Approved, not charged yet" />
            <Stat label="Purchase budget" value={q.data.month.budget_micros != null ? formatUsd(q.data.month.budget_micros) : 'No cap'} hint={budgetUse(q.data.month) ? `${Math.round(budgetUse(q.data.month)!.settled + budgetUse(q.data.month)!.held)}% used or committed` : undefined} />
          </div>
          <div>
            <SectionLabel>Period</SectionLabel>
            <p className="text-[12.5px] text-fg-3">{q.data.month.period} (company month). Waiting for you to pay: {q.data.awaiting_checkout.length}. Purchase approvals pending: {q.data.pending_approvals}.</p>
          </div>
        </CardBody>
      )}
    </Card>
  )
}
