import { ExternalLink, Pause, Play, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useDeleteChannel, useUpdateChannel } from '@/api'
import type { ChatChannel } from '@/api/types'
import { Badge, Button, ConfirmDialog, Dot, useToast } from '@/components/ui'
import { KIND_LABEL, STATUS_LABEL, STATUS_TONE, WHATSAPP_WINDOW_NOTE, safeHttpsUrl } from '@/lib/channels'
import { formatRelative } from '@/lib/format'
import { CopyField } from './CopyButton'
import { LinkAccount } from './LinkAccount'
import { LinkedAccounts } from './LinkedAccounts'

/** What the connect response carried once and never again (WhatsApp's verify token). */
export interface Reveal {
  webhook_url: string | null
  verify_token: string
}

function WhatsAppWebhook({ channel, reveal, onDismiss }: { channel: ChatChannel; reveal: Reveal | null; onDismiss: () => void }) {
  const url = reveal?.webhook_url ?? channel.webhook_url
  return (
    <div className="space-y-3 rounded-md border border-line-strong bg-raised p-3">
      <p className="text-[12.5px] font-medium text-fg">Finish in Meta: WhatsApp, Configuration, Webhook</p>
      {url ? (
        <CopyField label="Callback URL" value={url} />
      ) : (
        <p className="text-[12.5px] text-warning">There is no public https address right now, so Meta cannot reach this server. Set one up in Settings, Reachability.</p>
      )}
      <p className="text-[12px] text-fg-3">{WHATSAPP_WINDOW_NOTE}</p>
      {reveal ? (
        <>
          <CopyField label="Verify token" value={reveal.verify_token} />
          <p role="alert" className="text-[12px] text-warning">
            This token is shown only once. Copy it into Meta now: it is not stored where you can read it again. If you lose it, remove WhatsApp here and connect it again.
          </p>
          <Button size="sm" onClick={onDismiss}>
            I have copied it
          </Button>
        </>
      ) : (
        <p className="text-[12px] text-fg-3">The verify token was shown once, when you connected. If you lost it, remove WhatsApp here and connect it again.</p>
      )}
    </div>
  )
}

/** A connected chat app: status, the one-time WhatsApp webhook details, linking, linked accounts, pause and remove. */
export function ConnectedView({
  companyId,
  channel,
  userId,
  reveal,
  onDismissReveal,
  onRemoved,
}: {
  companyId: string
  channel: ChatChannel
  userId: string | undefined
  reveal: Reveal | null
  onDismissReveal: () => void
  onRemoved?: () => void
}) {
  const update = useUpdateChannel(companyId)
  const remove = useDeleteChannel(companyId)
  const toast = useToast()
  const [confirm, setConfirm] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const label = KIND_LABEL[channel.kind] ?? channel.kind
  const invite = safeHttpsUrl(channel.invite_url)
  const disabled = channel.status === 'disabled'

  async function setEnabled(enabled: boolean) {
    try {
      await update.mutateAsync({ id: channel.id, enabled })
      toast.success(enabled ? `${label} resumed` : `${label} paused`)
    } catch (err) {
      toast.error(errorMessage(err, `Could not ${enabled ? 'resume' : 'pause'} ${label}`))
    }
  }

  async function doRemove() {
    setRemoveError(null)
    try {
      await remove.mutateAsync(channel.id)
      setConfirm(false)
      toast.success(`${label} removed`)
      onRemoved?.()
    } catch (err) {
      setRemoveError(errorMessage(err, `Could not remove ${label}`))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Badge tone={STATUS_TONE[channel.status]}>
            <Dot tone={STATUS_TONE[channel.status]} />
            {STATUS_LABEL[channel.status]}
          </Badge>
          <p className="truncate text-[13px] font-medium text-fg">{channel.name}</p>
          {channel.last_event_at && <p className="text-[12px] text-fg-3">· last activity {formatRelative(channel.last_event_at)}</p>}
        </div>
        <div className="flex items-center gap-1.5">
          {channel.status === 'error' ? (
            <Button size="sm" icon={<Play className="size-3.5" />} loading={update.isPending} onClick={() => void setEnabled(true)}>
              Retry connection
            </Button>
          ) : disabled ? (
            <Button size="sm" icon={<Play className="size-3.5" />} loading={update.isPending} onClick={() => void setEnabled(true)}>
              Resume
            </Button>
          ) : (
            <Button size="sm" variant="ghost" icon={<Pause className="size-3.5" />} loading={update.isPending} onClick={() => void setEnabled(false)}>
              Pause
            </Button>
          )}
          <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm(true)}>
            Remove {label}
          </Button>
        </div>
      </div>

      {channel.last_error && (
        <p role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-danger">
          {channel.last_error}
        </p>
      )}

      {channel.kind === 'whatsapp' && <WhatsAppWebhook channel={channel} reveal={reveal} onDismiss={onDismissReveal} />}

      {channel.kind === 'discord' && (
        <div className="space-y-1.5 text-[12.5px] text-fg-2">
          {invite ? (
            <>
              <p>Discord only lets you message a bot you share a server with. Invite it to one of yours (scopes bot and applications.commands):</p>
              <a href={invite} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 font-medium text-accent-fg hover:underline">
                <ExternalLink className="size-3.5" aria-hidden />
                Invite the bot to a server
              </a>
            </>
          ) : (
            <p>Discord only lets you message a bot you share a server with. In the Developer Portal, OAuth2, build an invite link with the scopes bot and applications.commands and add the bot to one of your servers.</p>
          )}
        </div>
      )}

      <LinkAccount companyId={companyId} channel={channel} userId={userId} />
      <LinkedAccounts companyId={companyId} channel={channel} userId={userId} />

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Remove ${label}?`}
        description={`The bot stops answering, every linked chat is unlinked and the stored ${label} credentials are deleted. You can connect it again later with a new token.`}
        confirmLabel={`Remove ${label}`}
        loading={remove.isPending}
        error={removeError}
        onConfirm={() => void doRemove()}
      />
    </div>
  )
}
