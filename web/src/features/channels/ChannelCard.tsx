import { MessageCircle, MessagesSquare, Send, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import type { ChatChannel, ChatChannelKind, ChatKindInfo } from '@/api/types'
import { Badge } from '@/components/ui'
import { CHANNEL_GUIDES, KIND_LABEL, isChannelKind } from '@/lib/channels'
import { ConnectForm } from './ConnectForm'
import { ConnectedView, type Reveal } from './ConnectedView'

const ICON: Record<string, LucideIcon> = { telegram: Send, discord: MessagesSquare, whatsapp: MessageCircle }

function HowTo({ kind }: { kind: ChatChannelKind }) {
  const g = CHANNEL_GUIDES[kind]
  return (
    <div className="space-y-2">
      <ol className="list-decimal space-y-1 pl-5 text-[12.5px] text-fg-2">
        {g.steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      <p className="flex flex-wrap gap-x-3 text-[12.5px]">
        {g.links.map((l) => (
          <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer" className="font-medium text-accent-fg hover:underline">
            {l.label}
          </a>
        ))}
      </p>
    </div>
  )
}

/**
 * One chat app: its how-to and credential form until it is connected, then its status and linked accounts. A kind the server lists
 * as `available: false` ("coming later") is greyed with the server's reason and has nothing to click.
 */
export function ChannelCard({ companyId, kind, info, channel, userId }: { companyId: string; kind: string; info: ChatKindInfo | null; channel: ChatChannel | null; userId: string | undefined }) {
  const [reveal, setReveal] = useState<Reveal | null>(null)
  const Icon = ICON[kind] ?? MessageCircle
  const label = info?.label ?? KIND_LABEL[kind] ?? kind
  const comingLater = !channel && info?.available === false
  const known = isChannelKind(kind)

  return (
    <section aria-label={label} className={`rounded-md border border-line bg-surface ${comingLater ? 'opacity-60' : ''}`}>
      <header className="flex items-center gap-3 border-b border-line px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-accent-fg" aria-hidden>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13.5px] font-semibold text-fg">{label}</h3>
          {known && !channel && !comingLater && <p className="text-[12px] text-fg-3">{CHANNEL_GUIDES[kind].blurb}</p>}
          {comingLater && <p className="text-[12px] text-fg-3">{info?.reason ?? 'Coming later'}</p>}
        </div>
        {comingLater && <Badge>Coming later</Badge>}
      </header>
      {!comingLater && (
        <div className="space-y-4 p-4">
          {channel ? (
            <ConnectedView
              companyId={companyId}
              channel={channel}
              userId={userId}
              reveal={reveal}
              onDismissReveal={() => setReveal(null)}
              onRemoved={() => setReveal(null)}
            />
          ) : known ? (
            <>
              <HowTo kind={kind} />
              <ConnectForm
                companyId={companyId}
                kind={kind}
                blockedReason={info?.needs_public_url ? info.reason : null}
                onConnected={(c) => setReveal(c.verify_token ? { verify_token: c.verify_token, webhook_url: c.webhook_url } : null)}
              />
            </>
          ) : (
            <p className="text-[12.5px] text-fg-3">This server does not know how to connect {label} yet.</p>
          )}
        </div>
      )}
    </section>
  )
}
