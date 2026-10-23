import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { normalizeReachability } from '@/lib/reachability'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type {
  ConnectedAccount,
  ConnectedAccountCreate,
  ConnectedProvider,
  CreateCredentialRequest,
  Credential,
  DomainAvailability,
  DomainPlan,
  DomainRegistration,
  DomainRegistrationCreate,
  DomainRegistrationPreview,
  DomainRegistrationPreviewRequest,
  DomainRegistry,
  EmailIdentity,
  EmailIdentityUpdate,
  FreeDomainProvider,
  HostedUiDeployment,
  Mailbox,
  MailboxCreate,
  ProviderCheck,
  ReachabilityStatus,
  TunnelConfig,
  TunnelStatus,
} from './types'
import { isNotDeployed } from './work'

/**
 * Milestone 8 (reachability & identity). The backend is built in parallel: a 404/405 from any of these means
 * "not built yet" and is never an error banner (see Boundary / isNotDeployed). Types and paths: types.ts "Milestone 8".
 */
const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/* ------------------------------- device reachability ------------------------------- */

/**
 * LAN + tunnel + the public-exposure gate in one poll. Refreshed every 15 s (the tunnel's health is time-based),
 * on window focus, and on `reachability.updated` / `tunnel.updated` stream events.
 */
export function useReachability() {
  return useQuery({
    queryKey: qk.reachability,
    queryFn: async ({ signal }): Promise<ReachabilityStatus> => normalizeReachability(await apiFetch<unknown>('/reachability', { signal })),
    retry,
    refetchInterval: (q) => (q.state.error && isNotDeployed(q.state.error) ? false : 15_000),
  })
}

function useReachabilityMutation<V, R>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.reachability }),
  })
}

export const useSetLanAccess = () =>
  useReachabilityMutation((enabled: boolean) => apiFetch<unknown>('/reachability/lan', { method: 'PATCH', body: { enabled } }))
export const useStartTunnel = () => useReachabilityMutation((body: TunnelConfig) => apiFetch<TunnelStatus>('/reachability/tunnel/start', { method: 'POST', body }))
export const useStopTunnel = () => useReachabilityMutation((_: void) => apiFetch<TunnelStatus>('/reachability/tunnel/stop', { method: 'POST', body: {} }))
/** `confirm: true` is the owner's explicit yes to downloading the binary (the server verifies the checksum). */
export const useInstallTunnelBinary = () => useReachabilityMutation((_: void) => apiFetch<TunnelStatus>('/reachability/tunnel/install', { method: 'POST', body: { confirm: true } }))

/* ------------------------------- connected accounts -------------------------------- */

export function useConnectedAccounts(cid: string | undefined) {
  return useQuery({
    queryKey: qk.connectedAccounts(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<ConnectedAccount>(`${co(cid)}/connected-accounts`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

function useAccountMutation<V, R>(cid: string, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.connectedAccounts(cid) }),
        qc.invalidateQueries({ queryKey: qk.reachability }),
        qc.invalidateQueries({ queryKey: qk.emailAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ]),
  })
}

/**
 * Stores one provider secret in the M3b credential store. `gcTime: 0` so the cached mutation variables do not keep the
 * secret in memory afterwards.
 */
export function useCreateProviderSecret(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (body: CreateCredentialRequest) => apiFetch<Credential>(`${co(cid)}/credentials`, { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['company', cid, 'credentials'] }),
  })
}

export const useCreateConnectedAccount = (cid: string) =>
  useAccountMutation(cid, (body: ConnectedAccountCreate) => apiFetch<ConnectedAccount>(`${co(cid)}/connected-accounts`, { method: 'POST', body }))
export const useTestConnectedAccount = (cid: string) =>
  useAccountMutation(cid, (id: string) => apiFetch<ProviderCheck>(`${co(cid)}/connected-accounts/${id}/test`, { method: 'POST', body: {} }))
export const useRevokeConnectedAccount = (cid: string) =>
  useAccountMutation(cid, (id: string) => apiFetch<unknown>(`${co(cid)}/connected-accounts/${id}`, { method: 'DELETE' }))
export const useStartOAuth = (cid: string) =>
  useMutation({ mutationFn: (provider: ConnectedProvider) => apiFetch<{ authorize_url: string }>(`${co(cid)}/connected-accounts/oauth/start`, { method: 'POST', body: { provider } }) })

/* ------------------------------------- domains ------------------------------------- */

export function useDomainPlan(cid: string | undefined) {
  return useQuery({ queryKey: qk.domain(cid ?? ''), queryFn: ({ signal }) => apiFetch<DomainPlan>(`${co(cid)}/domain`, { signal }), enabled: !!cid, retry })
}

export function useDomainRegistries(cid: string | undefined, enabled = true) {
  return useQuery({ queryKey: qk.domainRegistries(cid ?? ''), queryFn: ({ signal }) => fetchAllPages<DomainRegistry>(`${co(cid)}/domain/registries`, {}, signal, 3), enabled: !!cid && enabled, retry })
}

export function useFreeDomainProviders(cid: string | undefined, enabled = true) {
  return useQuery({ queryKey: qk.freeDomainProviders(cid ?? ''), queryFn: ({ signal }) => fetchAllPages<FreeDomainProvider>(`${co(cid)}/domain/free-providers`, {}, signal, 3), enabled: !!cid && enabled, retry })
}

/** Availability search is an explicit action (the term is submitted), so it only runs for a non-empty submitted term. */
export function useDomainSearch(cid: string | undefined, term: string) {
  return useQuery({
    queryKey: qk.domainSearch(cid ?? '', term),
    queryFn: ({ signal }) => fetchAllPages<DomainAvailability>(`${co(cid)}/domain/search`, { q: term }, signal, 1),
    enabled: !!cid && term.trim() !== '',
    retry,
    staleTime: 60_000,
  })
}

export function usePreviewRegistration(cid: string) {
  return useMutation({ mutationFn: (body: DomainRegistrationPreviewRequest) => apiFetch<DomainRegistrationPreview>(`${co(cid)}/domain/registrations/preview`, { method: 'POST', body }) })
}

/** Creates the registration and, where the guardrails demand it, the human approval that gates it. */
export function useCreateRegistration(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DomainRegistrationCreate) => apiFetch<DomainRegistration>(`${co(cid)}/domain/registrations`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.domainAll(cid) }), qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useVerifyDomain(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (_: void) => apiFetch<DomainPlan>(`${co(cid)}/domain/verify`, { method: 'POST', body: {} }),
    onSuccess: (plan) => {
      qc.setQueryData(qk.domain(cid), plan)
      return qc.invalidateQueries({ queryKey: qk.reachability })
    },
  })
}

/* ---------------------------------- hosted UI (Vercel) ---------------------------------- */

export function useHostedUi(cid: string | undefined) {
  return useQuery({ queryKey: qk.hostedUi(cid ?? ''), queryFn: ({ signal }) => apiFetch<HostedUiDeployment | null>(`${co(cid)}/hosted-ui`, { signal }), enabled: !!cid, retry, refetchInterval: (q) => (q.state.data?.status === 'deploying' ? 5_000 : false) })
}

export function useDeployHostedUi(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (_: void) => apiFetch<HostedUiDeployment>(`${co(cid)}/hosted-ui/deploy`, { method: 'POST', body: {} }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.hostedUi(cid) }), qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

/* ------------------------------------ company email ------------------------------------ */

export function useEmailIdentity(cid: string | undefined) {
  return useQuery({ queryKey: qk.email(cid ?? ''), queryFn: ({ signal }) => apiFetch<EmailIdentity>(`${co(cid)}/email`, { signal }), enabled: !!cid, retry })
}

export function useUpdateEmailIdentity(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: EmailIdentityUpdate) => apiFetch<EmailIdentity>(`${co(cid)}/email`, { method: 'PATCH', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.emailAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useMailboxes(cid: string | undefined) {
  return useQuery({ queryKey: qk.mailboxes(cid ?? ''), queryFn: ({ signal }) => fetchAllPages<Mailbox>(`${co(cid)}/mailboxes`, {}, signal, 5), enabled: !!cid, retry })
}

export function useCreateMailbox(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MailboxCreate) => apiFetch<Mailbox>(`${co(cid)}/mailboxes`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.emailAll(cid) }), qc.invalidateQueries({ queryKey: qk.approvalsAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}
