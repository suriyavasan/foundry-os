import { ShoppingCart } from 'lucide-react'
import type { Approval } from '@/api/types'
import { usePurchase } from '@/api/wallet'
import { Badge, SectionLabel } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatUsd } from '@/lib/money'
import { recurrenceSuffix } from './walletLogic'

export interface PurchaseScope {
  purchaseId: string
  vendorDomain: string | null
  amountMicros: number | null
  recurrence: 'one_time' | 'monthly' | 'yearly' | null
  limitMicros: number | null
  lockDomain: string | null
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** The scope of a purchase approval (`{action, purchase_request_id, vendor_domain, amount_micros, recurrence, limit_micros, merchant_lock}`), or null for any other approval. */
export function readPurchaseScope(approval: Pick<Approval, 'kind' | 'scope'>): PurchaseScope | null {
  if (approval.kind !== 'external_purchase') return null
  const s = approval.scope
  if (!s || typeof s !== 'object') return null
  const o = s as Record<string, unknown>
  const purchaseId = str(o.purchase_request_id)
  if (!purchaseId) return null
  const lock = o.merchant_lock && typeof o.merchant_lock === 'object' ? (o.merchant_lock as Record<string, unknown>) : null
  const rec = str(o.recurrence)
  return {
    purchaseId,
    vendorDomain: str(o.vendor_domain),
    amountMicros: num(o.amount_micros),
    recurrence: rec === 'one_time' || rec === 'monthly' || rec === 'yearly' ? rec : null,
    limitMicros: num(o.limit_micros),
    lockDomain: lock ? (str(lock.domain) ?? str(lock.name)) : null,
  }
}

/**
 * What an agent wants to buy, for the person deciding: item, vendor domain (large), exact amount, recurring or not, the card
 * limit and merchant lock the approval authorises, the requesting agent and the justification. The scope shown is the one the
 * approval is bound to; the item and justification are agent-written text, rendered as plain text. Renders nothing for other
 * approvals.
 */
export function PurchaseApprovalDetails({ approval, companyId, dense = false }: { approval: Approval; companyId: string; dense?: boolean }) {
  const scope = readPurchaseScope(approval)
  const { openPanel } = useCanvas()
  const q = usePurchase(companyId, scope?.purchaseId)
  if (!scope) return null
  const p = q.data
  const recurrence = scope.recurrence ?? p?.recurrence ?? approval.recurrence ?? null
  const amount = scope.amountMicros ?? p?.amount_micros ?? approval.amount_micros
  const domain = scope.vendorDomain ?? p?.vendor_domain ?? approval.vendor
  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-raised p-2.5" role="group" aria-label={`Purchase details for ${approval.title}`}>
      <div className="flex items-center gap-1.5">
        <ShoppingCart className="size-3.5 text-fg-3" aria-hidden />
        <SectionLabel>Purchase</SectionLabel>
        {recurrence && recurrence !== 'one_time' && <Badge tone="warning">Recurring</Badge>}
      </div>
      <dl className="grid grid-cols-[minmax(7rem,9rem)_1fr] gap-x-3 gap-y-1 text-[12.5px]">
        {p && (
          <>
            <dt className="text-fg-3">Item</dt>
            <dd className="min-w-0 break-words text-fg">
              {p.item}
              {p.quantity > 1 ? ` × ${p.quantity}` : ''}
            </dd>
          </>
        )}
        <dt className="text-fg-3">Vendor domain</dt>
        <dd className="min-w-0 font-mono text-[13px] font-semibold break-all text-fg">{domain ?? '—'}</dd>
        <dt className="text-fg-3">Amount</dt>
        <dd className="text-fg tabular-nums">{amount != null ? `${formatUsd(amount)}${recurrenceSuffix(recurrence)}` : '—'}</dd>
        {scope.limitMicros != null && (
          <>
            <dt className="text-fg-3">Card limit</dt>
            <dd className="text-fg tabular-nums">{formatUsd(scope.limitMicros)}</dd>
          </>
        )}
        {scope.lockDomain && (
          <>
            <dt className="text-fg-3">Card locked to</dt>
            <dd className="min-w-0 font-mono text-[12px] break-all text-fg">{scope.lockDomain}</dd>
          </>
        )}
        {p && (
          <>
            <dt className="text-fg-3">Requested by</dt>
            <dd className="text-fg">
              {p.agent_name ?? 'An agent'}{' '}
              <button type="button" className="text-fg-2 underline" onClick={() => openPanel({ kind: 'task', id: p.task_id })}>
                (open its task)
              </button>
            </dd>
          </>
        )}
      </dl>
      {p?.justification && (
        <div>
          <SectionLabel>Why (written by the agent)</SectionLabel>
          <p className={dense ? 'mt-0.5 line-clamp-3 text-[12.5px] text-fg-2' : 'mt-0.5 text-[12.5px] whitespace-pre-wrap text-fg-2'}>{p.justification}</p>
        </div>
      )}
      {q.isError && !p && <p className="text-[12px] text-fg-3">The full request could not be loaded; the approved scope above is still exact.</p>}
    </div>
  )
}
