import { CreditCard } from 'lucide-react'
import { isNotDeployed, useWallet } from '@/api'
import { Button, EmptyState, ErrorState, LoadingState, NotAvailable, Widget } from '@/components/ui'
import { WalletSummary } from '@/features/wallet/WalletSummary'
import { useOpenWallet } from '@/features/wallet/useWalletNav'
import { useCanvas } from '../CanvasContext'

/**
 * Agent cards on the canvas, next to Spend. Every state is explicit: loading, error with retry, "not built yet" (the backend
 * has no purchasing), "not set up" (agents cannot buy anything; one button starts the setup wizard), "purchasing is off"
 * and the live summary.
 */
export function WalletWidget({ className }: { className?: string }) {
  const { companyId } = useCanvas()
  const openWallet = useOpenWallet()
  const q = useWallet(companyId)

  let body
  if (q.isPending) body = <LoadingState rows={4} label="Loading agent cards" />
  else if (q.isError) {
    body = isNotDeployed(q.error) ? (
      <NotAvailable compact className="p-4" milestone="M15" description="Agent cards come with M15." />
    ) : (
      <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
    )
  } else if (!q.data.available) {
    body = <NotAvailable compact className="p-4" milestone={q.data.milestone ?? 'M15'} description="Agent cards come with M15." />
  } else if (q.data.accounts.length === 0) {
    body = (
      <EmptyState
        compact
        icon={CreditCard}
        title="Agents can't buy anything yet."
        description="Until you set up a card, a purchase request becomes a task for you: the agent says what to buy and why, and you pay."
        action={
          <Button variant="primary" onClick={() => openWallet('setup')}>
            Set up agent cards
          </Button>
        }
      />
    )
  } else if (!q.data.enabled && !q.data.attention) {
    body = (
      <EmptyState
        compact
        icon={CreditCard}
        title="Purchasing is off"
        description="A card is connected, but agents' purchase requests still become tasks for you until you turn purchasing on."
        action={
          <Button variant="primary" onClick={() => openWallet('setup')}>
            Finish setup
          </Button>
        }
      />
    )
  } else {
    body = <WalletSummary companyId={companyId} wallet={q.data} dense />
  }

  return (
    <Widget title="Agent cards" subtitle="Purchases" onExpand={() => openWallet('overview')} expandLabel="Open agent cards" className={className}>
      {body}
    </Widget>
  )
}
