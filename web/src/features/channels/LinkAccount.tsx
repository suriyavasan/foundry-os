import { ExternalLink, Link2, Loader2, UserCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { errorMessage, useChannels, useLinkCode } from '@/api'
import type { ChatChannel, ChatLinkCode } from '@/api/types'
import { Button, FormError } from '@/components/ui'
import { OPEN_LABEL, formatCountdown, linkMessage, linkName, linksOf, newLinkOf, safeHttpsUrl, secondsLeft } from '@/lib/channels'
import { CopyField } from './CopyButton'

const anchor =
  'inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md border border-transparent bg-accent px-3 text-[13px] font-medium whitespace-nowrap text-fg-on-accent transition-colors hover:bg-accent-hover'

interface Issued extends ChatLinkCode {
  /** The person's link ids at the moment the code was issued: a link that is not in here is the one that code made. */
  known: Set<string>
}

/**
 * "Link my account": one-time code (10 minutes) -> the person sends it to the bot -> this component polls the channel until a
 * link of theirs appears. The code is shown once and kept only in this component's memory.
 */
export function LinkAccount({ companyId, channel, userId }: { companyId: string; channel: ChatChannel; userId: string | undefined }) {
  const issue = useLinkCode(companyId)
  const [issued, setIssued] = useState<Issued | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const left = issued ? secondsLeft(issued.expires_at, now) : 0
  const expired = !!issued && left === 0
  // Polling is cheap (the channel list) and only runs while a code is live and unanswered.
  const waiting = !!issued && !expired
  const live = useChannels(companyId, { poll: waiting })
  const liveChannel = live.data?.items.find((c) => c.id === channel.id) ?? channel
  const linked = issued ? newLinkOf(liveChannel, userId, issued.known) : undefined

  const ticking = waiting && !linked
  const tick = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => {
    if (!ticking) return
    tick.current = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      if (tick.current) clearInterval(tick.current)
    }
  }, [ticking])

  async function getCode() {
    try {
      const res = await issue.mutateAsync(channel.id)
      setNow(Date.now())
      setIssued({ ...res, known: new Set(linksOf(channel, userId).map((l) => l.id)) })
    } catch {
      /* the error is shown below from the mutation state */
    }
  }

  const mine = linksOf(channel, userId)
  const startLabel = mine.length ? 'Link another account' : 'Link my account'

  if (linked) {
    return (
      <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-success/40 bg-success-subtle px-3 py-2.5">
        <p className="flex items-center gap-2 text-[13px] text-fg">
          <UserCheck className="size-4 text-success" aria-hidden />
          Linked as <span className="font-semibold">{linkName(linked)}</span>. Say hello to your company from this chat.
        </p>
        <Button size="sm" variant="ghost" onClick={() => setIssued(null)}>
          Done
        </Button>
      </div>
    )
  }

  if (!issued) {
    return (
      <div className="space-y-2">
        <Button icon={<Link2 className="size-3.5" />} loading={issue.isPending} onClick={() => void getCode()} disabled={channel.status === 'disabled' || channel.status === 'error'}>
          {startLabel}
        </Button>
        {(channel.status === 'disabled' || channel.status === 'error') && <p className="text-[12px] text-fg-3">Resume this chat app first: a code only works while the bot is running.</p>}
        {issue.isError && <FormError message={errorMessage(issue.error)} />}
      </div>
    )
  }

  const deep = safeHttpsUrl(issued.deep_link)
  const kind = channel.kind
  return (
    <div className="space-y-3 rounded-md border border-line-strong bg-raised p-3">
      <CopyField label="Link code" value={issued.code} />
      <p className="text-[12.5px] text-fg-2">
        {kind === 'discord' ? (
          <>
            Send the bot a direct message: <code className="rounded-sm bg-canvas px-1 py-0.5 font-mono text-fg">{linkMessage(kind, issued.code)}</code>
          </>
        ) : deep ? (
          <>Tap the button to open the chat and send the pre-filled message. Or send it by hand: <code className="rounded-sm bg-canvas px-1 py-0.5 font-mono text-fg">{linkMessage(kind, issued.code)}</code></>
        ) : (
          <>
            Send the bot: <code className="rounded-sm bg-canvas px-1 py-0.5 font-mono text-fg">{linkMessage(kind, issued.code)}</code>
          </>
        )}
      </p>
      {issued.instructions && <p className="text-[12px] text-fg-3">{issued.instructions}</p>}
      {deep && kind !== 'discord' && (
        <a href={deep} target="_blank" rel="noopener noreferrer" className={anchor}>
          <ExternalLink className="size-3.5" aria-hidden />
          {OPEN_LABEL[kind]}
        </a>
      )}
      {expired ? (
        <div className="flex flex-wrap items-center gap-2">
          <p role="status" className="text-[12.5px] text-warning">
            This code expired.
          </p>
          <Button size="sm" loading={issue.isPending} onClick={() => void getCode()}>
            Get a new code
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p role="status" className="flex items-center gap-2 text-[12.5px] text-fg-2">
            <Loader2 className="cos-spin size-3.5" aria-hidden />
            Waiting for your message…
          </p>
          <p className="text-[12px] text-fg-3 tabular-nums">
            Expires in <span aria-label={`${left} seconds`}>{formatCountdown(left)}</span>
          </p>
        </div>
      )}
      {issue.isError && <FormError message={errorMessage(issue.error)} />}
    </div>
  )
}
