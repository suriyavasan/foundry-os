import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch } from './client'
import { qk } from './keys'
import type { ChatChannel, ChatChannelCreate, ChatChannelList, ChatChannelPatch, ChatLink, ChatLinkCode, ChatNotifyPrefs } from './types'

/**
 * M19 chat channels: talk to the company from Telegram, Discord or WhatsApp (docs/specs/m19-chat-channels.md).
 *
 *   GET    /companies/{cid}/channels                   { items, available, next_cursor }   (owner/admin only)
 *   POST   /companies/{cid}/channels                   201 ChatChannel (WhatsApp: webhook_url + verify_token, shown ONCE)
 *   PATCH  /companies/{cid}/channels/{id}              { enabled?, name? }
 *   DELETE /companies/{cid}/channels/{id}              204
 *   POST   /companies/{cid}/channels/{id}/link-code    { code, expires_at, deep_link, instructions }
 *   PATCH  /companies/{cid}/channel-links/{id}         { notify } (the linked person only)
 *   DELETE /companies/{cid}/channel-links/{id}         204 (the linked person, or an owner/admin)
 *
 * Secrets are write-only: no response ever carries a token, an access token or an app secret.
 */
const co = (cid: string | undefined) => `/companies/${cid}`

/** The backend predates chat channels (404/405). The section or step is then hidden behind "not built yet". */
export const channelsNotBuilt = (err: unknown): boolean => err instanceof ApiError && (err.status === 404 || err.status === 405)
/** Only owners and admins may see and manage channels. */
export const channelsForbidden = (err: unknown): boolean => err instanceof ApiError && err.status === 403

/** The channels and which kinds this install can connect. `poll` refreshes every 3 s (waiting for a link to appear). */
export function useChannels(cid: string | undefined, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: qk.channels(cid ?? ''),
    enabled: !!cid,
    retry: (n, e) => !channelsNotBuilt(e) && !channelsForbidden(e) && n < 2,
    refetchInterval: opts.poll ? 3000 : false,
    queryFn: ({ signal }) => apiFetch<ChatChannelList>(`${co(cid)}/channels`, { signal }),
  })
}

function useChannelMutation<V, R>(cid: string, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.channels(cid) }),
  })
}

/** Validates the credentials against the platform first (422 `invalid_credentials` with the platform's message). */
export function useConnectChannel(cid: string) {
  return useChannelMutation(cid, (body: ChatChannelCreate) => apiFetch<ChatChannel>(`${co(cid)}/channels`, { method: 'POST', body }))
}

export function useUpdateChannel(cid: string) {
  return useChannelMutation(cid, ({ id, ...body }: { id: string } & ChatChannelPatch) => apiFetch<ChatChannel>(`${co(cid)}/channels/${id}`, { method: 'PATCH', body }))
}

export function useDeleteChannel(cid: string) {
  return useChannelMutation(cid, (id: string) => apiFetch<void>(`${co(cid)}/channels/${id}`, { method: 'DELETE' }))
}

/** A one-time code (10 minutes) for the calling person. Shown once; not cached. */
export function useLinkCode(cid: string) {
  return useMutation({
    mutationFn: (channelId: string) => apiFetch<ChatLinkCode>(`${co(cid)}/channels/${channelId}/link-code`, { method: 'POST' }),
  })
}

export function useUpdateLink(cid: string) {
  return useChannelMutation(cid, ({ id, notify }: { id: string; notify: ChatNotifyPrefs }) => apiFetch<ChatLink>(`${co(cid)}/channel-links/${id}`, { method: 'PATCH', body: { notify } }))
}

export function useDeleteLink(cid: string) {
  return useChannelMutation(cid, (id: string) => apiFetch<void>(`${co(cid)}/channel-links/${id}`, { method: 'DELETE' }))
}
