import { channelsForbidden, channelsNotBuilt, useChannels, useMe } from '@/api'
import { EmptyState, ErrorState, LoadingState, NotAvailable } from '@/components/ui'
import { kindCards } from '@/lib/channels'
import { ChannelCard } from './ChannelCard'

/**
 * The chat apps of a company (Telegram, Discord, WhatsApp, and the ones that come later). Used by the onboarding step and by
 * Settings. Handles every state itself: loading, an older server without chat apps, a person who may not manage them, errors, empty.
 */
export function ChannelsManager({ companyId }: { companyId: string }) {
  const q = useChannels(companyId)
  const me = useMe()
  const userId = me.data?.user.id

  if (q.isPending) return <LoadingState rows={5} label="Loading chat apps" />
  if (q.isError) {
    if (channelsNotBuilt(q.error)) return <NotAvailable milestone="M19" compact description="This server does not have chat apps yet, so Telegram, Discord and WhatsApp cannot be connected." />
    if (channelsForbidden(q.error)) {
      return <EmptyState compact title="Only owners and admins manage chat apps" description="Ask an owner or admin of this company to connect one and link you." />
    }
    return <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} title="Could not load chat apps" />
  }

  const cards = kindCards(q.data.available, q.data.items)
  if (!cards.length) return <EmptyState compact title="No chat apps on this server" description="This server does not offer any chat app to connect." />

  return (
    <div className="space-y-3">
      {cards.map((c) => (
        <ChannelCard key={c.kind} companyId={companyId} kind={c.kind} info={c.info} channel={c.channel} userId={userId} />
      ))}
    </div>
  )
}
