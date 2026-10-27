import { useState } from 'react'
import { errorMessage } from '@/api'
import { useCheckCardAccount, useDisableCardAccount, useSetDefaultCardAccount, type CardAccount, type CardAccountCheck } from '@/api/wallet'
import { Badge, Button, Card, CardHeader, ConfirmDialog, useToast } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { formatUsd } from '@/lib/money'
import { AccountStatusBadge } from './badges'
import { FundingDialog } from './FundingDialog'
import { PROVIDER_LABEL, cardDescription } from './walletLogic'

/** The connected accounts: check them live, choose the default, see how to fund, disable (which freezes its cards). */
export function AccountsCard({ companyId, accounts, onSetup }: { companyId: string; accounts: CardAccount[]; onSetup: () => void }) {
  const check = useCheckCardAccount(companyId)
  const setDefault = useSetDefaultCardAccount(companyId)
  const disable = useDisableCardAccount(companyId)
  const toast = useToast()
  const [results, setResults] = useState<Record<string, CardAccountCheck>>({})
  const [funding, setFunding] = useState<CardAccount | null>(null)
  const [disabling, setDisabling] = useState<CardAccount | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function runCheck(a: CardAccount) {
    try {
      const r = await check.mutateAsync(a.id)
      setResults((m) => ({ ...m, [a.id]: r }))
    } catch (err) {
      toast.error(errorMessage(err, 'The check did not run'))
    }
  }

  return (
    <Card aria-label="Accounts">
      <CardHeader
        title="Accounts"
        description="Where agent cards come from. Keys live in the encrypted credential store and are never shown again."
        actions={
          <Button size="sm" onClick={onSetup}>
            Add an account
          </Button>
        }
      />
      <ul className="divide-y divide-line">
        {accounts.map((a) => {
          const r = results[a.id]
          const active = a.status !== 'disabled'
          return (
            <li key={a.id} className="space-y-1.5 px-4 py-3">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[13px] font-medium text-fg">{a.label}</span>
                <AccountStatusBadge status={a.status} />
                {a.is_default && <Badge tone="accent">Default</Badge>}
                {a.mode === 'test' && <Badge tone="info">Test mode</Badge>}
                <span className="text-[12px] text-fg-3">
                  {PROVIDER_LABEL[a.provider]}
                  {a.provider === 'manual' ? ` · ${cardDescription(a.manual_brand, a.manual_last4)}` : ''}
                </span>
              </div>
              <p className="text-[12px] text-fg-3">
                {a.last_balance_micros !== null && a.provider !== 'manual' ? `Balance ${formatUsd(a.last_balance_micros)}${a.balance_checked_at ? `, checked ${formatRelative(a.balance_checked_at)}` : ''}. ` : ''}
                {a.last_synced_at ? `Synced ${formatRelative(a.last_synced_at)}.` : 'Not synced yet.'}
              </p>
              {a.last_error && (
                <p role="alert" className="text-[12px] text-danger">
                  {a.last_error}
                </p>
              )}
              {r && (
                <p role="status" className={r.ok ? 'text-[12px] text-success' : 'text-[12px] text-danger'}>
                  {r.ok ? 'Check passed.' : 'Check failed.'} {r.message ?? ''}
                  {r.ok && r.issuing_enabled === false ? ' Issuing is not enabled on this account yet.' : ''}
                </p>
              )}
              {active && (
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {a.provider !== 'manual' && (
                    <Button size="sm" aria-label={`Check ${a.label}`} loading={check.isPending && check.variables === a.id} onClick={() => void runCheck(a)}>
                      Check connection
                    </Button>
                  )}
                  <Button size="sm" aria-label={`How to add funds to ${a.label}`} onClick={() => setFunding(a)}>
                    {a.provider === 'manual' ? 'About funding' : 'How to add funds'}
                  </Button>
                  {!a.is_default && (
                    <Button
                      size="sm"
                      aria-label={`Make ${a.label} the default`}
                      loading={setDefault.isPending && setDefault.variables === a.id}
                      onClick={() => setDefault.mutate(a.id, { onError: (err) => toast.error(errorMessage(err)) })}
                    >
                      Make default
                    </Button>
                  )}
                  <Button size="sm" variant="danger" aria-label={`Disable ${a.label}`} onClick={() => setDisabling(a)}>
                    Disable
                  </Button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {funding && <FundingDialog companyId={companyId} account={funding} onClose={() => setFunding(null)} />}
      <ConfirmDialog
        open={!!disabling}
        onOpenChange={(o) => {
          if (!o) {
            setDisabling(null)
            setError(null)
          }
        }}
        title={`Disable ${disabling?.label ?? 'account'}?`}
        description="Its active cards are frozen and no new card is issued from it. Purchases then become tasks for you until you connect another account."
        confirmLabel="Disable account"
        loading={disable.isPending}
        error={error}
        onConfirm={async () => {
          if (!disabling) return
          setError(null)
          try {
            await disable.mutateAsync(disabling.id)
            toast.success('Account disabled')
            setDisabling(null)
          } catch (err) {
            setError(errorMessage(err))
          }
        }}
      />
    </Card>
  )
}
