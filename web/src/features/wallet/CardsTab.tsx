import { useState } from 'react'
import { errorMessage } from '@/api'
import { useCardAction, useCards, type AgentCard, type CardAction } from '@/api/wallet'
import { Boundary, Button, ConfirmDialog, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { useAgentMap } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatDateTime } from '@/lib/format'
import { formatUsd } from '@/lib/money'
import { CardStatusBadge } from './badges'
import { RevealDialog } from './RevealDialog'
import { cardDescription, expiry } from './walletLogic'

const LIMIT_INTERVAL: Record<AgentCard['limit_interval'], string> = { per_authorization: 'per charge', monthly: 'per month', yearly: 'per year', all_time: 'in total' }

export function CardsTab({ companyId }: { companyId: string }) {
  const q = useCards(companyId)
  const { map } = useAgentMap(companyId)
  const act = useCardAction(companyId)
  const toast = useToast()
  const [confirm, setConfirm] = useState<{ card: AgentCard; action: 'freeze' | 'close' } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revealing, setRevealing] = useState<AgentCard | null>(null)

  async function run(card: AgentCard, action: CardAction) {
    setError(null)
    try {
      await act.mutateAsync({ id: card.id, action })
      toast.success(action === 'freeze' ? 'Card frozen' : action === 'unfreeze' ? 'Card unfrozen' : 'Card closed')
      setConfirm(null)
    } catch (err) {
      const msg = errorMessage(err, 'That did not go through')
      if (action === 'unfreeze') toast.error(msg)
      else setError(msg)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-fg-3">Cards issued for approved purchases. Only the last four digits are known here; the number is shown on the provider&apos;s own page.</p>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M15"
          unavailableTitle="Cards are not built yet"
          unavailableText="Agent cards come with M15."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No cards yet', description: 'A card is issued for each approved purchase when a provider is connected. With your own card, nothing is issued.' }}
        >
          {(cards) => (
            <Table>
              <THead>
                <TR>
                  <TH>Card</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Limit</TH>
                  <TH>Locked to</TH>
                  <TH>Agent</TH>
                  <TH>Expires</TH>
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {cards.map((c) => {
                  const label = cardDescription(c.brand, c.last4)
                  const live = c.status === 'active' || c.status === 'frozen'
                  return (
                    <TR key={c.id}>
                      <TD className="font-medium">
                        {label}
                        {expiry(c.exp_month, c.exp_year) && <span className="ml-1.5 text-[12px] font-normal text-fg-3">{expiry(c.exp_month, c.exp_year)}</span>}
                        <span className="block text-[11.5px] font-normal text-fg-3">{c.kind === 'single_use' ? 'Single use' : 'Subscription'}</span>
                      </TD>
                      <TD>
                        <CardStatusBadge status={c.status} />
                        {c.frozen_reason && <span className="mt-0.5 block text-[11.5px] text-fg-3">{c.frozen_reason}</span>}
                      </TD>
                      <TD className="text-right tabular-nums">
                        {formatUsd(c.limit_micros)}
                        <span className="block text-[11.5px] text-fg-3">{LIMIT_INTERVAL[c.limit_interval]}</span>
                      </TD>
                      <TD className="font-mono text-[12px] text-fg-2">{c.merchant_lock.domain ?? c.merchant_lock.name ?? <span className="font-sans text-fg-3">Any merchant</span>}</TD>
                      <TD>
                        <AgentChip agentId={c.agent_id} agents={map} emptyLabel="—" />
                      </TD>
                      <TD className="text-fg-3">{c.expires_at ? formatDateTime(c.expires_at) : '—'}</TD>
                      <TD className="text-right">
                        <div className="flex justify-end gap-1.5">
                          {live && (
                            <Button size="sm" aria-label={`Reveal ${label}`} onClick={() => setRevealing(c)}>
                              Reveal
                            </Button>
                          )}
                          {c.status === 'active' && (
                            <Button size="sm" aria-label={`Freeze ${label}`} onClick={() => setConfirm({ card: c, action: 'freeze' })}>
                              Freeze
                            </Button>
                          )}
                          {c.status === 'frozen' && (
                            <Button size="sm" aria-label={`Unfreeze ${label}`} loading={act.isPending && act.variables?.id === c.id} onClick={() => void run(c, 'unfreeze')}>
                              Unfreeze
                            </Button>
                          )}
                          {live && (
                            <Button size="sm" variant="danger" aria-label={`Close ${label}`} onClick={() => setConfirm({ card: c, action: 'close' })}>
                              Close
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
        open={!!confirm}
        onOpenChange={(o) => {
          if (!o) {
            setConfirm(null)
            setError(null)
          }
        }}
        title={confirm?.action === 'close' ? 'Close this card?' : 'Freeze this card?'}
        description={
          confirm?.action === 'close'
            ? 'A closed card can never be used again. Any purchase waiting on it is cancelled.'
            : 'A frozen card declines every charge until you unfreeze it. Only a person can unfreeze.'
        }
        confirmLabel={confirm?.action === 'close' ? 'Close card' : 'Freeze card'}
        destructive={confirm?.action === 'close'}
        loading={act.isPending}
        error={error}
        onConfirm={() => confirm && void run(confirm.card, confirm.action)}
      />
      {revealing && <RevealDialog companyId={companyId} cardId={revealing.id} cardLabel={cardDescription(revealing.brand, revealing.last4)} onClose={() => setRevealing(null)} />}
    </div>
  )
}
