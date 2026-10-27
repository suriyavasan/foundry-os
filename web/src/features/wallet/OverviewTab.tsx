import { RefreshCw } from 'lucide-react'
import { errorMessage } from '@/api'
import { useSyncWallet, type WalletOverview } from '@/api/wallet'
import { Button, Card, CardHeader, EmptyState, useToast } from '@/components/ui'
import { AccountsCard } from './AccountsCard'
import { WalletSummary } from './WalletSummary'
import { type WalletTab } from './useWalletNav'

/** The summary the widget shows, in full, plus the accounts behind it and a "Sync now". */
export function OverviewTab({ companyId, wallet, onTab }: { companyId: string; wallet: WalletOverview; onTab: (t: WalletTab) => void }) {
  const sync = useSyncWallet(companyId)
  const toast = useToast()

  async function runSync() {
    try {
      const r = await sync.mutateAsync()
      if (r.errors.length > 0) toast.error(`Synced with ${r.errors.length === 1 ? 'a problem' : `${r.errors.length} problems`}: ${r.errors[0]}`)
      else toast.success(r.transactions_new > 0 ? `${r.transactions_new} new transaction${r.transactions_new === 1 ? '' : 's'}` : 'Up to date')
    } catch (err) {
      toast.error(errorMessage(err, 'Sync did not run'))
    }
  }

  if (wallet.accounts.length === 0) {
    return (
      <Card>
        <EmptyState
          title="Agents can't buy anything yet."
          description="Set up a card and they can request purchases: you approve (or the AI Co-founder, within limits you set), and you pay at checkout. Until then a purchase request becomes a task for you."
          action={
            <Button variant="primary" onClick={() => onTab('setup')}>
              Set up agent cards
            </Button>
          }
        />
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Overview"
          description={wallet.enabled ? 'Purchasing is on.' : 'Purchasing is off: requests become tasks for you.'}
          actions={
            wallet.accounts.some((a) => a.provider !== 'manual') ? (
              <Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={sync.isPending} onClick={() => void runSync()}>
                Sync now
              </Button>
            ) : undefined
          }
        />
        <WalletSummary companyId={companyId} wallet={wallet} />
      </Card>
      <AccountsCard companyId={companyId} accounts={wallet.accounts} onSetup={() => onTab('setup')} />
    </div>
  )
}
