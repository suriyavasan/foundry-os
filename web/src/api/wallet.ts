import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import { isNotDeployed } from './work'

/**
 * M15 agent cards and purchasing (docs/specs/m15-agent-cards-and-purchasing.md section 4.8). Hand-written mirror of
 * `backend/app/schemas/purchasing.py`. There is deliberately no field anywhere in these types that could carry a card
 * number or CVC: the server never has one, and neither does this client.
 */

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/* ---------------------------------- types ---------------------------------- */

export type CardProviderId = 'manual' | 'stripe_issuing' | 'mercury' | 'fake'
export type CardAccountStatus = 'setup' | 'active' | 'attention' | 'disabled'
export type CardMode = 'live' | 'test'
export type CardKind = 'single_use' | 'subscription'
export type CardStatus = 'active' | 'frozen' | 'closed' | 'expired'
export type LimitInterval = 'per_authorization' | 'monthly' | 'yearly' | 'all_time'
export type PurchaseStatus =
  | 'requested'
  | 'pending_approval'
  | 'approved'
  | 'issuing'
  | 'issue_failed'
  | 'card_issued'
  | 'awaiting_checkout'
  | 'authorized'
  | 'settled'
  | 'human_task'
  | 'rejected'
  | 'expired'
  | 'cancelled'
export type PurchaseDecision = 'auto' | 'cofounder' | 'human' | 'report_only'
export type CheckoutPath = 'programmatic' | 'human_checkout' | 'human_buys'
export type Recurrence = 'one_time' | 'monthly' | 'yearly'
export type CardTxnKind = 'authorization' | 'capture' | 'refund' | 'decline' | 'reversal'
export type CardTxnStatus = 'pending' | 'settled' | 'reversed' | 'declined'
export type CardTxnMatch = 'matched' | 'unmatched' | 'mismatch'
export type CardTxnSource = 'provider' | 'human'
export type SubscriptionStatus = 'active' | 'review_due' | 'cancel_requested' | 'cancelled' | 'lapsed'
export type WalletCostKind = 'verified' | 'estimated' | 'unknown'
export type CompanyKind = 'india_company' | 'us_company' | 'not_incorporated' | 'other'

export interface ProviderCapabilities {
  issue_cards: boolean
  single_use: boolean
  merchant_mcc_lock: boolean
  per_auth_limit: boolean
  interval_limits: string[]
  list_transactions: boolean
  balance: boolean
  webhooks: boolean
  programmatic_checkout: boolean
  test_mode: boolean
}

export interface ProviderGuideStep {
  title: string
  body: string
  /** A step only the human can do (KYC, funding, OTP ...). */
  owner_only: boolean
}

export interface CardProviderInfo {
  id: CardProviderId
  label: string
  capabilities: ProviderCapabilities
  /** Honest, dated text per country. */
  eligibility: string
  recommended_for: CompanyKind[]
  steps: ProviderGuideStep[]
  /** What was and was not exercised against the real system. */
  verification: string
  needs_connected_account: boolean
  available: boolean
}

export interface CardProviderList {
  items: CardProviderInfo[]
  guide_updated_on: string
}

export interface CardAccount {
  id: string
  company_id: string
  provider: CardProviderId
  label: string
  status: CardAccountStatus
  mode: CardMode
  connected_account_id: string | null
  external_ref: string | null
  manual_brand: string | null
  manual_last4: string | null
  is_default: boolean
  last_balance_micros: number | null
  balance_checked_at: string | null
  last_synced_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

/** Connect a provider (`connected_account_id` of the vault) or register the owner's own card (label, brand, LAST FOUR only). */
export interface CardAccountCreate {
  provider: CardProviderId
  label: string
  mode?: CardMode
  connected_account_id?: string | null
  external_ref?: string | null
  manual_brand?: string | null
  manual_last4?: string | null
  make_default?: boolean
}

export interface CardAccountCheck {
  account_id: string
  ok: boolean
  mode?: CardMode | null
  issuing_enabled?: boolean | null
  balance_readable?: boolean | null
  balance_micros?: number | null
  message?: string | null
}

export interface FundingInstructions {
  account_id: string
  provider: CardProviderId
  instructions: Record<string, string>
  fetched_at: string
}

export interface MerchantLock {
  name?: string | null
  domain?: string | null
  mcc_allow?: string[]
  country?: string | null
}

export interface AgentCard {
  id: string
  company_id: string
  card_account_id: string
  provider_card_id: string
  kind: CardKind
  status: CardStatus
  purchase_request_id: string | null
  subscription_id: string | null
  agent_id: string | null
  last4: string
  brand: string | null
  exp_month: number | null
  exp_year: number | null
  limit_micros: number
  limit_interval: LimitInterval
  merchant_lock: MerchantLock
  expires_at: string | null
  frozen_reason: string | null
  closed_at: string | null
  created_at: string
}

/** How a HUMAN can see the number: a link to the provider's own surface. The number never passes through this server. */
export interface CardReveal {
  kind: 'provider_dashboard'
  url: string
  provider: CardProviderId
  note: string
}

export interface CheckoutPacketData {
  vendor: string
  vendor_domain: string
  url: string
  item: string
  quantity: number
  amount_micros: number
  card_limit_micros?: number | null
  recurrence: Recurrence
  company_email?: string | null
  bring_back?: string | null
  card_last4?: string | null
  card_brand?: string | null
  card_exp_month?: number | null
  card_exp_year?: number | null
  steps: string[]
}

export interface CardTransaction {
  id: string
  company_id: string
  card_account_id: string
  card_id: string | null
  purchase_request_id: string | null
  subscription_id: string | null
  provider_txn_id: string
  kind: CardTxnKind
  status: CardTxnStatus
  amount_micros: number
  cost_kind: WalletCostKind
  merchant_amount_minor: number | null
  merchant_currency: string | null
  merchant_name: string | null
  merchant_mcc: string | null
  merchant_country: string | null
  match_status: CardTxnMatch
  /** "human" is shown as "confirmed by you". */
  source: CardTxnSource
  occurred_at: string
  settled_at: string | null
  created_at: string
}

export interface PurchaseApprovalSummary {
  id: string
  status: string
  approver: string
  decided_by_type?: string | null
  decision_note?: string | null
  decided_at?: string | null
}

export interface PurchaseRequest {
  id: string
  company_id: string
  agent_id: string
  agent_name?: string | null
  run_id: string | null
  task_id: string
  project_id: string | null
  item: string
  vendor: string
  vendor_domain: string
  url: string
  quantity: number
  amount_micros: number
  original_amount_minor: number | null
  original_currency: string | null
  recurrence: Recurrence
  justification: string
  alternatives: string | null
  status: PurchaseStatus
  decision: PurchaseDecision | null
  checkout_path: CheckoutPath | null
  approval_id: string | null
  card_id: string | null
  subscription_id: string | null
  checkout_task_id: string | null
  held_micros: number
  settled_micros: number | null
  cost_kind: WalletCostKind | null
  checkout_packet?: CheckoutPacketData | null
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface PurchaseRequestDetail extends PurchaseRequest {
  approval?: PurchaseApprovalSummary | null
  card?: AgentCard | null
  transactions?: CardTransaction[]
}

export interface PurchaseMarkPaid {
  merchant_amount_usd: string
  billed_amount_inr?: string | null
  receipt_artifact_id?: string | null
  note?: string | null
}

export interface Subscription {
  id: string
  company_id: string
  purchase_request_id: string
  agent_id: string | null
  agent_name?: string | null
  vendor: string
  vendor_domain: string
  item: string
  amount_micros: number
  recurrence: Recurrence
  card_id: string | null
  status: SubscriptionStatus
  started_on: string
  next_renewal_on: string | null
  last_charged_on: string | null
  last_charge_micros: number | null
  review_task_id: string | null
  cancel_task_id: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface WalletBalance {
  available_micros: number | null
  pending_micros?: number | null
  as_of: string | null
  /** "manual" has no balance to report. */
  source: 'provider' | 'manual'
}

export interface WalletMonth {
  /** YYYY-MM */
  period: string
  settled_verified_micros: number
  /** Committed, NOT charged. */
  held_micros: number
  budget_micros: number | null
}

export interface WalletCheckoutItem {
  purchase_request_id: string
  item: string
  vendor: string
  vendor_domain: string
  amount_micros: number
  checkout_task_id: string | null
  expires_at: string | null
}

export interface WalletRenewal {
  subscription_id: string
  vendor: string
  amount_micros: number
  next_renewal_on: string
}

export interface PurchasePolicyRecommendation {
  purchase_cofounder_limit_micros: number
  purchase_cofounder_monthly_micros: number
  purchase_auto_approve_micros: number
  purchase_recurring_requires_human: boolean
  purchase_new_vendor_requires_human: boolean
}

export interface WalletSetupHint {
  providers: CardProviderId[]
  recommended_policy?: PurchasePolicyRecommendation | null
}

/** `GET /wallet`. `available: false` + `milestone` = the subsystem is not built on this backend. */
export interface WalletOverview {
  available: boolean
  milestone?: string | null
  enabled: boolean
  accounts: CardAccount[]
  balance: WalletBalance | null
  month: WalletMonth
  awaiting_checkout: WalletCheckoutItem[]
  pending_approvals: number
  renewals_due: WalletRenewal[]
  low_balance?: boolean
  /** Provider check failing, or frozen after an emergency stop. */
  attention?: string | null
  setup?: WalletSetupHint | null
}

export interface WalletSyncResult {
  accounts_synced: number
  transactions_seen: number
  transactions_new: number
  errors: string[]
}

/** The purchasing part of the company policy (all optional on the wire: a backend without M15 sends none of it). */
export interface PurchasePolicyFields {
  purchasing_enabled?: boolean
  purchase_auto_approve_micros?: number
  purchase_agent_auto_monthly_micros?: number
  purchase_cofounder_limit_micros?: number
  purchase_cofounder_monthly_micros?: number
  purchase_monthly_budget_micros?: number | null
  purchase_recurring_requires_human?: boolean
  purchase_new_vendor_requires_human?: boolean
  purchase_tolerance_bps?: number
  purchase_tolerance_min_micros?: number
  purchase_card_ttl_hours?: number
  purchase_checkout_member_id?: string | null
  subscription_renewal_alert_days?: number
}

/* ---------------------------------- reads ---------------------------------- */

/** `GET /wallet`. The widget treats a 404 (older backend) and `available: false` the same way: "not built yet". */
export function useWallet(cid: string | undefined) {
  return useQuery({
    queryKey: qk.wallet(cid ?? ''),
    queryFn: ({ signal }) => apiFetch<WalletOverview>(`${co(cid)}/wallet`, { signal }),
    enabled: !!cid,
    retry,
  })
}

export function useWalletProviders(cid: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.walletProviders(cid ?? ''),
    queryFn: ({ signal }) => apiFetch<CardProviderList>(`${co(cid)}/wallet/providers`, { signal }),
    enabled: !!cid && enabled,
    retry,
  })
}

export function useCards(cid: string | undefined) {
  return useQuery({
    queryKey: qk.walletCards(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<AgentCard>(`${co(cid)}/wallet/cards`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

export function usePurchases(cid: string | undefined, status?: PurchaseStatus | 'all') {
  const s = status && status !== 'all' ? status : undefined
  return useQuery({
    queryKey: qk.purchaseList(cid ?? '', s ?? 'all'),
    queryFn: ({ signal }) => fetchAllPages<PurchaseRequest>(`${co(cid)}/purchases`, { status: s }, signal, 5),
    enabled: !!cid,
    retry,
  })
}

export function usePurchase(cid: string | undefined, id: string | null | undefined) {
  return useQuery({
    queryKey: qk.purchase(cid ?? '', id ?? ''),
    queryFn: ({ signal }) => apiFetch<PurchaseRequestDetail>(`${co(cid)}/purchases/${id}`, { signal }),
    enabled: !!cid && !!id,
    retry,
  })
}

/**
 * The purchase whose checkout task this is (`checkout_task_id`), or null. A backend without purchasing answers 404 and
 * the task simply has no packet.
 */
export function usePurchaseForTask(cid: string | undefined, taskId: string | undefined) {
  const q = useQuery({
    queryKey: qk.purchaseForTask(cid ?? '', taskId ?? ''),
    queryFn: ({ signal }) => fetchAllPages<PurchaseRequest>(`${co(cid)}/purchases`, { checkout_task_id: taskId }, signal, 1),
    enabled: !!cid && !!taskId,
    retry,
  })
  // The server filters by `checkout_task_id`; match again here so an unfiltered answer can never attach a packet to the wrong task.
  const purchase = q.data?.find((p) => p.checkout_task_id === taskId) ?? null
  return { purchase, isPending: q.isPending, isError: q.isError && !isNotDeployed(q.error), error: q.error, refetch: q.refetch }
}

export function useSubscriptions(cid: string | undefined) {
  return useQuery({
    queryKey: qk.subscriptions(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Subscription>(`${co(cid)}/subscriptions`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

export function useCardTransactions(cid: string | undefined) {
  return useQuery({
    queryKey: qk.cardTransactions(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<CardTransaction>(`${co(cid)}/wallet/transactions`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

/** Live funding instructions: fetched on demand, never cached beyond the dialog that asked. */
export function useAccountFunding(cid: string | undefined, accountId: string | null) {
  return useQuery({
    queryKey: qk.accountFunding(cid ?? '', accountId ?? ''),
    queryFn: ({ signal }) => apiFetch<FundingInstructions>(`${co(cid)}/wallet/accounts/${accountId}/funding`, { signal }),
    enabled: !!cid && !!accountId,
    gcTime: 0,
    staleTime: 0,
    retry,
  })
}

/* --------------------------------- mutations --------------------------------- */

function useWalletMutation<TVars, TData>(cid: string, fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.walletAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.purchasesAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.subscriptions(cid) }),
        qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.financeAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.policy(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ]),
  })
}

export const useCreateCardAccount = (cid: string) =>
  useWalletMutation(cid, (body: CardAccountCreate) => apiFetch<CardAccount>(`${co(cid)}/wallet/accounts`, { method: 'POST', body }))

export const useCheckCardAccount = (cid: string) =>
  useWalletMutation(cid, (id: string) => apiFetch<CardAccountCheck>(`${co(cid)}/wallet/accounts/${id}/check`, { method: 'POST', body: {} }))

export const useSetDefaultCardAccount = (cid: string) =>
  useWalletMutation(cid, (id: string) => apiFetch<CardAccount>(`${co(cid)}/wallet/accounts/${id}/default`, { method: 'POST', body: {} }))

export const useDisableCardAccount = (cid: string) =>
  useWalletMutation(cid, (id: string) => apiFetch<unknown>(`${co(cid)}/wallet/accounts/${id}`, { method: 'DELETE' }))

export type CardAction = 'freeze' | 'unfreeze' | 'close'
export const useCardAction = (cid: string) =>
  useWalletMutation(cid, ({ id, action, reason }: { id: string; action: CardAction; reason?: string }) =>
    apiFetch<AgentCard>(`${co(cid)}/wallet/cards/${id}/${action}`, { method: 'POST', body: reason ? { reason } : {} }),
  )

/** `GET /wallet/cards/{id}/reveal` is a read that needs a recent sign-in. It is a mutation so it only runs on a click and is never cached. */
export const useRevealCard = (cid: string) =>
  useMutation({ gcTime: 0, mutationFn: (id: string) => apiFetch<CardReveal>(`${co(cid)}/wallet/cards/${id}/reveal`) })

export const useDecidePurchase = (cid: string) =>
  useWalletMutation(cid, ({ id, decision, note }: { id: string; decision: 'approve' | 'reject'; note?: string }) =>
    apiFetch<PurchaseRequestDetail>(`${co(cid)}/purchases/${id}/${decision}`, { method: 'POST', body: note ? { note } : {} }),
  )

export const useMarkPurchasePaid = (cid: string) =>
  useWalletMutation(cid, ({ id, body }: { id: string; body: PurchaseMarkPaid }) =>
    apiFetch<PurchaseRequestDetail>(`${co(cid)}/purchases/${id}/mark-paid`, { method: 'POST', body }),
  )

export const useCancelPurchase = (cid: string) =>
  useWalletMutation(cid, ({ id, reason }: { id: string; reason?: string }) =>
    apiFetch<PurchaseRequestDetail>(`${co(cid)}/purchases/${id}/cancel`, { method: 'POST', body: reason ? { reason } : {} }),
  )

export const useSubscriptionAction = (cid: string) =>
  useWalletMutation(cid, ({ id, action, note }: { id: string; action: 'cancelled' | 'keep'; note?: string }) =>
    apiFetch<Subscription>(`${co(cid)}/subscriptions/${id}/${action}`, { method: 'POST', body: note ? { note } : {} }),
  )

export const useSyncWallet = (cid: string) =>
  useWalletMutation(cid, () => apiFetch<WalletSyncResult>(`${co(cid)}/wallet/sync`, { method: 'POST', body: {} }))
