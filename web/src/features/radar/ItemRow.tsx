import { Eye, EyeOff, Flag } from 'lucide-react'
import type { RadarItem } from '@/api/radar'
import { Badge, Button, Tooltip } from '@/components/ui'
import { formatRelative } from '@/lib/format'
import { ITEM_STATUS, engagementText } from './radarLogic'
import { ExternalLink, UntrustedText } from './Untrusted'

/**
 * One item read from outside. Title, excerpt, author and the matched words are plain untrusted text; the title is the
 * only link, and it opens in a new tab. Items with instruction-like text carry a "Flagged text" badge.
 */
export function ItemRow({ item, canAct, busy, onToggleDismiss }: { item: RadarItem; canAct?: boolean; busy?: boolean; onToggleDismiss?: (item: RadarItem) => void }) {
  const eng = engagementText(item.engagement)
  const dismissed = item.status === 'dismissed'
  const status = ITEM_STATUS[item.status] ?? { label: item.status, tone: 'neutral' as const }
  return (
    <li className="space-y-1.5 px-3 py-2.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-fg">
            <UntrustedText>
              <ExternalLink href={item.url} showHost>
                {item.title}
              </ExternalLink>
            </UntrustedText>
          </p>
          {item.excerpt && <UntrustedText as="p" className="mt-0.5 line-clamp-3 text-[12.5px] text-fg-2">{item.excerpt}</UntrustedText>}
        </div>
        {canAct && onToggleDismiss && (
          <Button size="sm" variant="ghost" loading={busy} icon={dismissed ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />} onClick={() => onToggleDismiss(item)} aria-label={`${dismissed ? 'Restore' : 'Dismiss'} ${item.title}`}>
            {dismissed ? 'Restore' : 'Dismiss'}
          </Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-fg-3">
        {item.flagged && (
          <Tooltip content={item.injection_flags.length ? `This text contains instruction-like phrases (${item.injection_flags.join(', ')}). Agents see it marked and never follow it.` : 'This text contains instruction-like phrases. Agents see it marked and never follow it.'}>
            <Badge tone="warning">
              <Flag className="size-3" aria-hidden />
              Flagged text
            </Badge>
          </Tooltip>
        )}
        <Badge tone={status.tone}>{status.label}</Badge>
        {item.source_name && <span className="font-medium text-fg-2">{item.source_name}</span>}
        {item.category && <span>{item.category}</span>}
        <span>{item.kind}</span>
        {item.author && <UntrustedText>by {item.author}</UntrustedText>}
        <span title={item.published_at ?? item.fetched_at}>{formatRelative(item.published_at ?? item.fetched_at)}</span>
        {eng && <span>{eng}</span>}
        <span title="Deterministic keyword score">score {item.match_score}</span>
        {item.relevance !== null && <span title="The agent's own score">relevance {item.relevance}</span>}
        {item.matched_keywords.map((k) => (
          <span key={k} className="rounded-sm border border-line bg-raised px-1.5 text-fg-2">
            <UntrustedText>{k}</UntrustedText>
          </span>
        ))}
      </div>
    </li>
  )
}
