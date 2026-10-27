import { useCardTransactions } from '@/api/wallet'
import { Badge, Boundary, CostKindBadge, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { formatDateTime, titleCase } from '@/lib/format'
import { formatCost } from '@/lib/money'
import { transactionProvenance } from './walletLogic'

const MATCH_TONE = { matched: 'success', unmatched: 'warning', mismatch: 'danger' } as const

export function TransactionsTab({ companyId }: { companyId: string }) {
  const q = useCardTransactions(companyId)
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-fg-3">
        The ledger of card transactions. &quot;Verified · provider&quot; was reported by the card provider; &quot;verified · confirmed by you&quot; is the amount you entered when you marked a purchase paid.
      </p>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M15"
          unavailableTitle="Transactions are not built yet"
          unavailableText="The card ledger comes with M15."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No transactions yet', description: 'Charges appear here once a card is used or you mark a purchase paid.' }}
        >
          {(txns) => (
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Merchant</TH>
                  <TH>Kind</TH>
                  <TH className="text-right">Amount</TH>
                  <TH>Provenance</TH>
                  <TH>Match</TH>
                </TR>
              </THead>
              <TBody>
                {txns.map((t) => (
                  <TR key={t.id}>
                    <TD className="text-fg-3">{formatDateTime(t.occurred_at)}</TD>
                    <TD className="max-w-[14rem] truncate">{t.merchant_name ?? <span className="text-fg-3">Unknown</span>}</TD>
                    <TD>
                      {titleCase(t.kind)}
                      <span className="block text-[11.5px] text-fg-3">{titleCase(t.status)}</span>
                    </TD>
                    <TD className="text-right tabular-nums">{formatCost(t.amount_micros, t.cost_kind)}</TD>
                    <TD>
                      <span className="flex flex-wrap items-center gap-1.5">
                        <CostKindBadge kind={t.cost_kind} />
                        <span className="text-[12px] text-fg-3" title={transactionProvenance(t)}>
                          {t.source === 'human' ? 'confirmed by you' : 'provider'}
                        </span>
                      </span>
                    </TD>
                    <TD>
                      <Badge tone={MATCH_TONE[t.match_status]}>{titleCase(t.match_status)}</Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Boundary>
      </div>
    </div>
  )
}
