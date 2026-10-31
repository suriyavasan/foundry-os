import { Unlink } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useDeleteLink, useUpdateLink } from '@/api'
import type { ChatChannel, ChatLink, ChatNotifyPrefs } from '@/api/types'
import { Button, Switch, useToast } from '@/components/ui'
import { NOTIFY_FIELDS, linkName } from '@/lib/channels'
import { formatRelative } from '@/lib/format'

function LinkRow({ companyId, channel, link, mine }: { companyId: string; channel: ChatChannel; link: ChatLink; mine: boolean }) {
  const update = useUpdateLink(companyId)
  const remove = useDeleteLink(companyId)
  const toast = useToast()
  const [pending, setPending] = useState<string | null>(null)
  const name = linkName(link)

  async function toggle(key: keyof ChatNotifyPrefs, next: boolean) {
    setPending(key)
    try {
      await update.mutateAsync({ id: link.id, notify: { [key]: next } })
    } catch (err) {
      toast.error(errorMessage(err, 'Could not save that setting'))
    } finally {
      setPending(null)
    }
  }

  async function unlink() {
    try {
      await remove.mutateAsync(link.id)
      toast.success(`Unlinked ${name}`)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not unlink'))
    }
  }

  return (
    <li className="rounded-md border border-line bg-canvas p-3" aria-label={`Linked account ${name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-fg">
            {name}
            {link.user_name && link.display_name && link.user_name !== link.display_name ? <span className="font-normal text-fg-3"> · {link.user_name}</span> : null}
            {mine && <span className="ml-1.5 text-[11.5px] font-normal text-fg-3">(you)</span>}
          </p>
          <p className="text-[12px] text-fg-3">
            Linked {formatRelative(link.linked_at)}
            {link.last_inbound_at ? ` · last message ${formatRelative(link.last_inbound_at)}` : ''}
          </p>
        </div>
        <Button size="sm" variant="ghost" icon={<Unlink className="size-3.5" />} loading={remove.isPending} onClick={() => void unlink()} aria-label={`Unlink ${name}`}>
          Unlink
        </Button>
      </div>
      {mine ? (
        <fieldset className="mt-3 grid gap-2 sm:grid-cols-2">
          <legend className="sr-only">Send {name} these messages</legend>
          {NOTIFY_FIELDS.map((f) => (
            <div key={f.key} className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[12.5px] text-fg">{f.label}</p>
                <p className="text-[11.5px] text-fg-3">{f.hint}</p>
              </div>
              <Switch label={`${f.label} to ${name}`} checked={!!link.notify[f.key]} loading={pending === f.key} disabled={update.isPending} onCheckedChange={(next) => void toggle(f.key, next)} />
            </div>
          ))}
        </fieldset>
      ) : (
        <p className="mt-2 text-[12px] text-fg-3">Only {link.user_name ?? 'the linked person'} can change what this chat is sent. You can unlink it.</p>
      )}
      {channel.kind === 'whatsapp' && link.window_open === false && <p className="mt-2 text-[12px] text-warning">Outside the 24-hour window: send any message to the WhatsApp number to receive updates again.</p>}
    </li>
  )
}

/** The people linked to a chat app, each with what the chat is sent (only the linked person can change that) and Unlink. */
export function LinkedAccounts({ companyId, channel, userId }: { companyId: string; channel: ChatChannel; userId: string | undefined }) {
  if (!channel.links.length) {
    return <p className="text-[12.5px] text-fg-3">Nobody is linked yet. Link your account to start talking to your company from here.</p>
  }
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {channel.links.map((l) => (
          <LinkRow key={l.id} companyId={companyId} channel={channel} link={l} mine={l.user_id === userId} />
        ))}
      </ul>
    </div>
  )
}
