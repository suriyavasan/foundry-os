import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isMissingEndpoint } from '@/lib/auth'
import { apiFetch, fetchAllPages } from './client'
import { useMe } from './system'
import { qk } from './keys'
import type { InvitationAccept, InvitationAccepted, InvitationCreate, InvitationCreated, Invitation, Member, MemberUpdate } from './types'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isMissingEndpoint(e) && n < 2

/* --------------------------------- members --------------------------------- */

export function useMembers(cid: string | undefined) {
  return useQuery({
    queryKey: qk.members(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Member>(`${co(cid)}/members`, {}, signal, 10),
    enabled: !!cid,
    retry,
  })
}

/**
 * The people of the company for places that merely *enrich* a view (org chart, assignee pickers, task rows).
 * A backend without the members endpoint (or an error) yields an empty list and `available: false`: those views
 * then behave exactly as they did before M7.
 */
export function useMemberDirectory(cid: string | undefined) {
  const q = useMembers(cid)
  const members = q.data ?? EMPTY
  return { members, available: !!q.data, loading: q.isPending && !!cid, query: q }
}
const EMPTY: Member[] = []

export function useUpdateMember(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: MemberUpdate & { id: string }) => apiFetch<Member>(`${co(cid)}/members/${id}`, { method: 'PATCH', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.membersAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) }), qc.invalidateQueries({ queryKey: qk.me })]),
  })
}

/** Removing a member reassigns their open tasks server-side; the task lists are refreshed. */
export function useRemoveMember(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<unknown>(`${co(cid)}/members/${id}`, { method: 'DELETE' }),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.membersAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
      ]),
  })
}

/* ------------------------------- invitations ------------------------------- */

export function useInvitations(cid: string | undefined) {
  return useQuery({
    queryKey: qk.invitations(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<Invitation>(`${co(cid)}/invitations`, {}, signal, 5),
    enabled: !!cid,
    retry,
  })
}

/** The invite link is in the response once; it must not stay in the mutation cache. */
export function useCreateInvitation(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (body: InvitationCreate) => apiFetch<InvitationCreated>(`${co(cid)}/invitations`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.invitations(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

export function useRevokeInvitation(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<unknown>(`${co(cid)}/invitations/${id}`, { method: 'DELETE' }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.invitations(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}

/** Global path: the person accepting is not yet a member of the company. The token travels in the path only. */
export function useAcceptInvitation(token: string) {
  const qc = useQueryClient()
  return useMutation({
    gcTime: 0,
    mutationFn: (body: InvitationAccept) => apiFetch<InvitationAccepted>(`/invitations/${encodeURIComponent(token)}/accept`, { method: 'POST', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'auth' })
      void qc.invalidateQueries({ queryKey: qk.authSession })
    },
  })
}

/** The signed-in person's own member record in this company, once the members endpoint answers. */
export function useMyMember(cid: string | undefined) {
  const me = useMe()
  const dir = useMemberDirectory(cid)
  const fromMembership = me.data?.memberships.find((m) => m.company_id === cid)?.member_id
  const member = dir.members.find((m) => m.id === fromMembership) ?? dir.members.find((m) => m.user_id === me.data?.user.id) ?? null
  return { member, memberId: member?.id ?? fromMembership ?? null, available: dir.available, loading: me.isPending || dir.loading }
}
