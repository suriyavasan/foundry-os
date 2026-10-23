import * as Popover from '@radix-ui/react-popover'
import clsx from 'clsx'
import { Bell, CheckCheck } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useAuthStatus, useMarkNotificationsRead, useNotifications } from '@/api'
import type { Notification } from '@/api/types'
import { Button, EmptyState, ErrorState, IconButton, LoadingState } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { isMissingEndpoint } from '@/lib/auth'
import { formatRelative } from '@/lib/format'
import { notificationKindLabel, panelForNotification } from '@/lib/notifications'
import { withPanel } from '@/lib/panels'

/**
 * The bell in the top bar: unread count, the list, mark read / mark all read. It is not drawn at all while the
 * backend has no notifications (legacy or pre-M7): there is nothing honest to show.
 */
export function NotificationBell() {
  const auth = useAuthStatus()
  const q = useNotifications(auth.kind === 'authenticated')
  const mark = useMarkNotificationsRead()
  const { companyId, openPanel } = useCanvas()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  if (auth.kind !== 'authenticated' || (q.isError && isMissingEndpoint(q.error))) return null

  const unread = q.data?.unread_count ?? 0

  function choose(n: Notification) {
    if (!n.read_at) mark.mutate({ ids: [n.id] })
    const panel = panelForNotification(n)
    setOpen(false)
    if (!panel) return
    if (n.company_id && n.company_id !== companyId) navigate({ pathname: `/c/${n.company_id}`, search: withPanel('', panel) })
    else openPanel(panel)
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <IconButton label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'} className="relative" aria-haspopup="dialog">
          <Bell className="size-4" aria-hidden />
          {unread > 0 && (
            <span aria-hidden data-testid="unread-count" className="absolute top-0.5 right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] leading-none font-semibold text-fg-on-accent">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </IconButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={6} aria-label="Notifications" className="z-[60] w-96 max-w-[calc(100vw-1.5rem)] rounded-md bg-surface shadow-pop">
          <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
            <h2 className="text-[13px] font-semibold text-fg">Notifications</h2>
            <Button size="sm" variant="ghost" icon={<CheckCheck className="size-3.5" />} disabled={unread === 0 || mark.isPending} onClick={() => mark.mutate({ all: true })}>
              Mark all read
            </Button>
          </div>
          <div className="max-h-[min(420px,60vh)] overflow-y-auto">
            {q.isPending ? (
              <LoadingState rows={3} label="Loading notifications" />
            ) : q.isError ? (
              <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
            ) : q.data.items.length === 0 ? (
              <EmptyState compact title="You are all caught up" description="Assignments, review requests, approvals and replies from your AI Co-founder show up here." />
            ) : (
              <ul className="divide-y divide-line">
                {q.data.items.map((n) => (
                  <li key={n.id}>
                    <button type="button" onClick={() => choose(n)} className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-hover">
                      <span aria-hidden className={clsx('mt-1.5 size-2 shrink-0 rounded-full', n.read_at ? 'bg-transparent' : 'bg-accent')} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[11px] font-medium tracking-wide text-fg-3 uppercase">
                          {notificationKindLabel(n.kind)}
                          {!n.read_at && <span className="sr-only"> (unread)</span>}
                        </span>
                        <span className={clsx('block text-[13px] break-words', n.read_at ? 'text-fg-2' : 'font-medium text-fg')}>{n.title}</span>
                        {n.body && <span className="mt-0.5 line-clamp-2 block text-[12px] text-fg-3">{n.body}</span>}
                        <time dateTime={n.created_at} className="mt-0.5 block text-[11.5px] text-fg-3">
                          {formatRelative(n.created_at)}
                        </time>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
