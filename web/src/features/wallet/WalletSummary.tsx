import { AlertTriangle, Landmark } from 'lucide-react'
import { useState } from 'react'
import type { CardAccount, WalletOverview } from '@/api/wallet'
import { Badge, Button, SectionLabel } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatDateTime, formatRelative } from '@/lib/format'
import { formatUsd } from '@/lib/money'
import { FundingDialog } from './FundingDialog'
import { useOpenWallet } from './useWalletNav'
import { budgetUse, cardDescription } from './walletLogic'

export const defaultAccount = (accounts: CardAccount[]): CardAccount | undefined => accounts.find((a) => a.is_default && a.status !== 'disabled') ?? accounts.find((a) => a.status !== 'disabled')

/** Settled and committed spend against the purchase budget. Committed (held) money is not a charge and looks different. */
function MonthBar({ month }: { month: WalletOverview['month'] }) {
  const use = budgetUse(month)
  if (!use) return <p className="text-[12px] text-fg-3">No purchase budget set. Each charge still needs the approval the policy asks for.</p>
  return (
    <div
      role="img"
      aria-label={`Purchase budget: ${formatUsd(month.settled_verified_micros)} settled, ${formatUsd(month.held_micros)} committed, of ${formatUsd(month.budget_micros)}`}
      className="flex h-1.5 w-full overflow-hidden rounded-full bg-active"
    >
      <div className="h-full bg-success" style={{ width: `${use.settled}%` }} />
      <div className="h-full bg-warning" style={{ width: `${Math.min(use.held, 100 - use.settled)}%` }} />
    </div>
  )
}

/**
 * The state of the wallet when it is set up: balance (or the manual card), this month's purchases (settled is a real charge,
 * committed is only a hold), checkouts waiting for you, approvals, upcoming renewals and any attention banner. Shared by the
 * canvas widget and the Wallet panel's overview.
 */
export function WalletSummary({ companyId, wallet, dense = false }: { companyId: string; wallet: WalletOverview; dense?: boolean }) {
  const { openPanel } = useCanvas()
  const openWallet = useOpenWallet()
  const [funding, setFunding] = useState<CardAccount | null>(null)
  const account = defaultAccount(wallet.accounts)
  const bal = wallet.balance
  const providerBalance = bal && bal.source === 'provider' && bal.available_micros !== null

  return (
    <div className="space-y-4 p-3.5">
      {wallet.attention && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-danger">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{wallet.attention}</span>
        </div>
      )}
      {wallet.low_balance && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-warning">
          <span className="flex items-center gap-2">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
            Balance is low. Agents may not be able to pay.
          </span>
          {account && (
            <Button size="sm" onClick={() => setFunding(account)}>
              How to add funds
            </Button>
          )}
        </div>
      )}

      <div>
        <SectionLabel>{providerBalance ? 'Balance' : 'Card'}</SectionLabel>
        {providerBalance ? (
          <>
            <p className="mt-0.5 text-[20px] leading-7 font-semibold text-fg tabular-nums">{formatUsd(bal.available_micros)}</p>
            <p className="text-[12px] text-fg-3">
              {bal.as_of ? (
                <>
                  As of <time dateTime={bal.as_of} title={formatDateTime(bal.as_of)}>{formatRelative(bal.as_of)}</time>, reported by {account ? account.label : 'the provider'}
                </>
              ) : (
                'Reported by the provider'
              )}
              {bal.pending_micros ? ` · ${formatUsd(bal.pending_micros)} pending` : ''}
            </p>
          </>
        ) : account ? (
          <p className="mt-0.5 flex items-center gap-2 text-[14px] font-medium text-fg">
            <Landmark className="size-4 text-fg-3" aria-hidden />
            {account.provider === 'manual' ? `Manual card ${cardDescription(account.manual_brand, account.manual_last4)}` : account.label}
          </p>
        ) : (
          <p className="mt-0.5 text-[13px] text-fg-3">No account connected.</p>
        )}
        {!providerBalance && <p className="text-[12px] text-fg-3">No balance to show: you pay from your own card.</p>}
      </div>

      <div>
        <SectionLabel>This month</SectionLabel>
        <div className="mt-1 grid grid-cols-2 gap-3">
          <div>
            <p className="text-[16px] leading-6 font-semibold text-fg tabular-nums">{formatUsd(wallet.month.settled_verified_micros)}</p>
            <p className="text-[12px] text-fg-3">Settled · verified</p>
          </div>
          <div>
            <p className="text-[16px] leading-6 font-semibold text-fg-2 tabular-nums">{formatUsd(wallet.month.held_micros)}</p>
            <p className="text-[12px] text-fg-3">Committed · not charged yet</p>
          </div>
        </div>
        <div className="mt-2">
          <MonthBar month={wallet.month} />
          {wallet.month.budget_micros ? <p className="mt-1 text-[12px] text-fg-3">Purchase budget {formatUsd(wallet.month.budget_micros)}</p> : null}
        </div>
      </div>

      <div>
        <SectionLabel>
          Awaiting your checkout ({wallet.awaiting_checkout.length})
        </SectionLabel>
        {wallet.awaiting_checkout.length === 0 ? (
          <p className="mt-1 text-[12.5px] text-fg-3">Nothing is waiting for you to pay.</p>
        ) : (
          <ul className="mt-1 divide-y divide-line rounded-md border border-line">
            {wallet.awaiting_checkout.slice(0, dense ? 3 : 20).map((c) => (
              <li key={c.purchase_request_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium text-fg">{c.item}</p>
                  <p className="truncate text-[12px] text-fg-3">
                    {c.vendor_domain} · {formatUsd(c.amount_micros)}
                  </p>
                </div>
                <Button size="sm" variant="primary" aria-label={`Open checkout for ${c.item}`} onClick={() => (c.checkout_task_id ? openPanel({ kind: 'task', id: c.checkout_task_id }) : openWallet('purchases'))}>
                  Open checkout
                </Button>
              </li>
            ))}
            {dense && wallet.awaiting_checkout.length > 3 && (
              <li className="px-3 py-2 text-[12px] text-fg-3">
                +{wallet.awaiting_checkout.length - 3} more.{' '}
                <button type="button" className="underline" onClick={() => openWallet('purchases')}>
                  See all
                </button>
              </li>
            )}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px]">
        <button type="button" onClick={() => openPanel({ kind: 'approvals' })} className="inline-flex items-center gap-1.5 text-fg-2 hover:text-fg">
          <Badge tone={wallet.pending_approvals > 0 ? 'warning' : 'neutral'}>{wallet.pending_approvals}</Badge>
          <span className="underline">{wallet.pending_approvals === 1 ? 'purchase approval pending' : 'purchase approvals pending'}</span>
        </button>
      </div>

      {wallet.renewals_due.length > 0 && (
        <div>
          <SectionLabel>Renewing soon</SectionLabel>
          <ul className="mt-1 space-y-1 text-[12.5px] text-fg-2">
            {wallet.renewals_due.map((r) => (
              <li key={r.subscription_id} className="flex justify-between gap-3">
                <span className="truncate">{r.vendor}</span>
                <span className="shrink-0 tabular-nums">
                  {formatUsd(r.amount_micros)} · {r.next_renewal_on}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {funding && <FundingDialog companyId={companyId} account={funding} onClose={() => setFunding(null)} />}
    </div>
  )
}
