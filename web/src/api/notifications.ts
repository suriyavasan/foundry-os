import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isMissingEndpoint } from '@/lib/auth'
import { markAllRead, markRead, normalizeNotifications } from '@/lib/notifications'
import { apiFetch } from './client'
import { qk } from './keys'
import type { NotificationList } from './types'

/**
 * The signed-in person's notifications, newest first. `notification.created` on the company stream invalidates
 * this; the slow poll covers a dropped stream. A 404 means the backend predates M7: the bell is simply not drawn.
 */
export function useNotifications(enabled = true) {
  return useQuery({
    queryKey: qk.notifications,
    enabled,
    queryFn: async ({ signal }): Promise<NotificationList> => normalizeNotifications(await apiFetch<unknown>('/notifications', { query: { limit: 50 }, signal })),
    refetchInterval: 60_000,
    retry: (n, e) => !isMissingEndpoint(e) && n < 2,
  })
}

/** Mark some (or all) notifications read. The list updates at once and rolls back if the server refuses. */
export function useMarkNotificationsRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { ids: string[] } | { all: true }) => apiFetch<unknown>('/notifications/read', { method: 'POST', body }),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: qk.notifications })
      const previous = qc.getQueryData<NotificationList>(qk.notifications)
      const now = new Date().toISOString()
      qc.setQueryData<NotificationList>(qk.notifications, (old) => (old ? ('all' in body ? markAllRead(old, now) : markRead(old, body.ids, now)) : old))
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(qk.notifications, ctx.previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.notificationsAll }),
  })
}
