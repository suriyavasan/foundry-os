import { ErrorState, Button, Dialog, LoadingState, DefinitionList, NotAvailable } from '@/components/ui'
import { isNotDeployed, useAccountFunding, useWalletProviders, type CardAccount } from '@/api'
import { formatDateTime } from '@/lib/format'
import { ProviderGuide } from './ProviderGuide'

/**
 * How to add funds. The instructions come live from the provider for this account (never stored by us); the provider's
 * guide is shown below because funding is always a step only the owner can do. A manual card has nothing to fund here.
 */
export function FundingDialog({ companyId, account, onClose }: { companyId: string; account: CardAccount; onClose: () => void }) {
  const manual = account.provider === 'manual'
  const q = useAccountFunding(companyId, manual ? null : account.id)
  const providers = useWalletProviders(companyId)
  const provider = providers.data?.items.find((p) => p.id === account.provider)
  const entries = q.data ? Object.entries(q.data.instructions) : []
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add funds: ${account.label}`}
      description="Only you can fund the account. Foundry AI OS never moves money."
      footer={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className="space-y-4">
        {manual ? (
          <p className="text-[13px] text-fg-2">This is your own card, so there is no balance to top up here: keep enough room on the card for the purchases agents request.</p>
        ) : q.isPending ? (
          <LoadingState rows={3} label="Fetching funding instructions" />
        ) : q.isError ? (
          isNotDeployed(q.error) ? (
            <NotAvailable compact milestone="M15" description="Live funding instructions are not served by this backend." />
          ) : (
            <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )
        ) : entries.length === 0 ? (
          <p className="text-[13px] text-fg-3">The provider returned no instructions. Use the steps below.</p>
        ) : (
          <div>
            <DefinitionList items={entries.map(([label, value]) => ({ label, value: <span className="font-mono text-[12.5px]">{value}</span> }))} />
            <p className="mt-2 text-[12px] text-fg-3">Fetched from the provider {formatDateTime(q.data.fetched_at)}. Not stored.</p>
          </div>
        )}
        {provider && <ProviderGuide provider={provider} guideUpdatedOn={providers.data?.guide_updated_on} />}
      </div>
    </Dialog>
  )
}
