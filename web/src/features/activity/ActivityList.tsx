import { Bot, Cog, User } from 'lucide-react'
import type { ActorType, AuditEvent } from '@/api/types'
import { Chip } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'

const ActorIcon = ({ type }: { type: ActorType }) => {
  const cls = 'size-3.5'
  if (type === 'agent') return <Bot className={cls} aria-label="Agent" />
  if (type === 'system') return <Cog className={cls} aria-label="System" />
  return <User className={cls} aria-label="Human" />
}

export function ActivityList({ events }: { events: AuditEvent[] }) {
  return (
    <ul className="divide-y divide-line">
      {events.map((e) => (
        <li key={e.id} className="flex items-start gap-3 px-4 py-2.5">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-hover text-fg-2">
            <ActorIcon type={e.actor_type} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] break-words text-fg">{e.summary}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-3">
              <span>{e.actor_label}</span>
              <Chip>{e.action}</Chip>
            </p>
          </div>
          <time dateTime={e.created_at} title={formatDateTime(e.created_at)} className="shrink-0 pt-0.5 text-[12px] text-fg-3">
            {formatRelative(e.created_at)}
          </time>
        </li>
      ))}
    </ul>
  )
}
