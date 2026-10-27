import type { CardAccount, CardStatus, PurchaseStatus, SubscriptionStatus } from '@/api/wallet'
import { Badge } from '@/components/ui'
import { ACCOUNT_STATUS, CARD_STATUS, PURCHASE_STATUS, SUBSCRIPTION_STATUS } from './walletLogic'

export function PurchaseStatusBadge({ status }: { status: PurchaseStatus }) {
  const s = PURCHASE_STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

export function CardStatusBadge({ status }: { status: CardStatus }) {
  const s = CARD_STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

export function SubscriptionStatusBadge({ status }: { status: SubscriptionStatus }) {
  const s = SUBSCRIPTION_STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

export function AccountStatusBadge({ status }: { status: CardAccount['status'] }) {
  const s = ACCOUNT_STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={s.tone}>{s.label}</Badge>
}
