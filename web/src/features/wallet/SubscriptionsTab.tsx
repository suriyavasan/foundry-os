import { useState } from 'react'
import { errorMessage } from '@/api'
import { useSubscriptionAction, useSubscriptions, type Subscription } from '@/api/wallet'
import { Boundary, Button, ConfirmDialog, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative } from '@/lib/format'
import { formatUsd } from '@/lib/money'
import { SubscriptionStatusBadge } from './badges'
import { recurrenceSuffix } from './walletLogic'

export function SubscriptionsTab({ companyId }: { companyId: string }) {
  const q = useSubscriptions(companyId)
  const { openPanel } = useCanvas()
  const act = useSubscriptionAction(companyId)
  const toast = useToast()
  const [cancelling, setCancelling] = useState<Subscription | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run(sub: Subscription, action: 'cancelled' | 'keep') {
    setError(null)
    try {
      await act.mutateAsync({ id: sub.id, action })
      toast.success(action === 'keep' ? `Keeping ${sub.vendor}` : `${sub.vendor} marked cancelled`)
      setCancelling(null)
    } catch (err) {
      const msg = errorMessage(err, 'That did not go through')
      if (action === 'keep') toast.error(msg)
      else setError(msg)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-fg-3">Recurring purchases. Before each renewal the agent that owns it reviews whether to keep it. Cancelling happens on the vendor&apos;s site: only you can do that.</p>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M15"
          unavailableTitle="Subscriptions are not built yet"
          unavailableText="Subscription tracking comes with M15."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No subscriptions', description: 'A recurring purchase you approve appears here with its next renewal.' }}
        >
          {(subs) => (
            <Table>
              <THead>
                <TR>
                  <TH>Vendor</TH>
                  <TH className="text-right">Amount</TH>
                  <TH>Status</TH>
                  <TH>Next renewal</TH>
                  <TH>Last charge</TH>
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {subs.map((s) => {
                  const live = s.status === 'active' || s.status === 'review_due' || s.status === 'cancel_requested'
                  return (
                    <TR key={s.id}>
                      <TD className="font-medium">
                        {s.item}
                        <span className="block font-mono text-[11.5px] font-normal text-fg-3">{s.vendor_domain}</span>
                      </TD>
                      <TD className="text-right tabular-nums">
                        {formatUsd(s.amount_micros)}
                        <span className="text-fg-3">{recurrenceSuffix(s.recurrence)}</span>
                      </TD>
                      <TD>
                        <SubscriptionStatusBadge status={s.status} />
                        {s.reviewed_at && <span className="mt-0.5 block text-[11.5px] text-fg-3">Reviewed {formatRelative(s.reviewed_at)}</span>}
                      </TD>
                      <TD className="text-fg-2">{s.next_renewal_on ?? '—'}</TD>
                      <TD className="text-fg-2 tabular-nums">{s.last_charged_on ? `${s.last_charged_on}${s.last_charge_micros != null ? ` · ${formatUsd(s.last_charge_micros)}` : ''}` : '—'}</TD>
                      <TD className="text-right">
                        <div className="flex justify-end gap-1.5">
                          {(s.review_task_id || s.cancel_task_id) && (
                            <Button size="sm" aria-label={`Open the task for ${s.vendor}`} onClick={() => openPanel({ kind: 'task', id: (s.cancel_task_id ?? s.review_task_id)! })}>
                              Open task
                            </Button>
                          )}
                          {(s.status === 'review_due' || s.status === 'cancel_requested') && (
                            <Button size="sm" aria-label={`Keep ${s.vendor}`} loading={act.isPending && act.variables?.id === s.id && act.variables.action === 'keep'} onClick={() => void run(s, 'keep')}>
                              Keep
                            </Button>
                          )}
                          {live && (
                            <Button size="sm" variant="danger" aria-label={`Mark ${s.vendor} cancelled`} onClick={() => setCancelling(s)}>
                              Mark cancelled
                            </Button>
                          )}
                        </div>
                      </TD>
                    </TR>
                  )
                })}
              </TBody>
            </Table>
          )}
        </Boundary>
      </div>
      <ConfirmDialog
        open={!!cancelling}
        onOpenChange={(o) => {
          if (!o) {
            setCancelling(null)
            setError(null)
          }
        }}
        title={`Mark ${cancelling?.vendor ?? 'subscription'} as cancelled?`}
        description="Only do this after you cancelled it on the vendor's site. Foundry AI OS cannot cancel for you, and will watch for a charge that still arrives."
        confirmLabel="I cancelled it"
        destructive={false}
        loading={act.isPending}
        error={error}
        onConfirm={() => cancelling && void run(cancelling, 'cancelled')}
      />
    </div>
  )
}
