import { isNotDeployed, useWallet } from '@/api'
import { Card, ErrorState, LoadingState, NotAvailable, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { CardsTab } from './CardsTab'
import { OverviewTab } from './OverviewTab'
import { PurchasesTab } from './PurchasesTab'
import { SetupWizard } from './SetupWizard'
import { SubscriptionsTab } from './SubscriptionsTab'
import { TransactionsTab } from './TransactionsTab'
import { isWalletTab, useWalletTab, type WalletTab } from './useWalletNav'

/**
 * Agent cards: the owner's view of what agents buy. Overview, Purchases (with approve / reject / cancel), Cards (freeze,
 * close, reveal on the provider), Subscriptions, Transactions and the Setup wizard. The tab lives in the URL
 * (`?panel=wallet&tab=setup`). Card numbers are never shown here: only last four digits and links to the provider.
 */
export function WalletPanel() {
  const { companyId } = useCanvas()
  const q = useWallet(companyId)
  const [tab, setTab] = useWalletTab()

  const frame = (children: React.ReactNode) => (
    <PanelFrame title="Agent cards" subtitle="What agents buy, and how it is paid">
      {children}
    </PanelFrame>
  )

  if (q.isPending) {
    return frame(
      <Card>
        <LoadingState rows={5} label="Loading agent cards" />
      </Card>,
    )
  }
  if (q.isError) {
    return frame(
      <Card>
        {isNotDeployed(q.error) ? (
          <NotAvailable milestone="M15" title="Agent cards are not built yet" description="Card accounts, purchase requests and subscriptions come with M15. Until then agents cannot buy anything." />
        ) : (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        )}
      </Card>,
    )
  }
  if (!q.data.available) {
    return frame(
      <Card>
        <NotAvailable milestone={q.data.milestone ?? 'M15'} title="Agent cards are not built yet" description="Card accounts, purchase requests and subscriptions come with M15." />
      </Card>,
    )
  }
  const wallet = q.data

  return frame(
    <Tabs value={tab} onValueChange={(v) => isWalletTab(v) && setTab(v)}>
      <TabsList aria-label="Agent cards sections">
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="purchases">
          Purchases{wallet.pending_approvals > 0 ? ` (${wallet.pending_approvals})` : ''}
        </TabsTrigger>
        <TabsTrigger value="cards">Cards</TabsTrigger>
        <TabsTrigger value="subscriptions">Subscriptions</TabsTrigger>
        <TabsTrigger value="transactions">Transactions</TabsTrigger>
        <TabsTrigger value="setup">Setup</TabsTrigger>
      </TabsList>
      <TabsContent value="overview">
        <OverviewTab companyId={companyId} wallet={wallet} onTab={(t: WalletTab) => setTab(t)} />
      </TabsContent>
      <TabsContent value="purchases">
        <PurchasesTab companyId={companyId} />
      </TabsContent>
      <TabsContent value="cards">
        <CardsTab companyId={companyId} />
      </TabsContent>
      <TabsContent value="subscriptions">
        <SubscriptionsTab companyId={companyId} />
      </TabsContent>
      <TabsContent value="transactions">
        <TransactionsTab companyId={companyId} />
      </TabsContent>
      <TabsContent value="setup">
        <SetupWizard companyId={companyId} wallet={wallet} onDone={() => setTab('overview')} />
      </TabsContent>
    </Tabs>,
  )
}
