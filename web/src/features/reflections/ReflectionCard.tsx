import { BookMarked } from 'lucide-react'
import type { Reflection } from '@/api/types'
import { formatRelative } from '@/lib/format'
import type { Panel } from '@/lib/panels'
import { reflectionExcerpt } from '@/lib/reflections'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'
import { EvidenceChips, KindChip, StatusChip } from './ReflectionBadges'
import { ReactionBar } from './ReactionBar'

/** One reflection in a list: chips, title (opens the thread), excerpt, evidence, the proposed standard, reactions. */
export function ReflectionCard({
  reflection: r,
  companyId,
  canRespond,
  onOpenPanel,
  excerptLength = 170,
  now,
}: {
  reflection: Reflection
  companyId: string
  canRespond: boolean
  onOpenPanel: (p: Panel) => void
  excerptLength?: number
  now?: number
}) {
  return (
    <li data-testid="reflection-row" className="space-y-2 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <KindChip kind={r.kind} />
        <StatusChip status={r.status} />
        <span className="ml-auto text-[11.5px] text-fg-3" title={r.created_at}>
          {r.author_name ? `${r.author_name} · ` : ''}
          {formatRelative(r.created_at, now)}
        </span>
      </div>
      <button type="button" onClick={() => onOpenPanel({ kind: 'reflection', id: r.id })} aria-label={`Open reflection: ${r.title}`} className="block w-full text-left">
        <span className="block text-[13.5px] leading-snug font-semibold text-fg hover:underline">{r.title}</span>
        <span className="mt-0.5 block text-[12.5px] leading-snug text-fg-2">{reflectionExcerpt(r.body, excerptLength)}</span>
      </button>
      {r.proposed_standard && (
        <p className="flex items-start gap-1.5 rounded-sm border border-line bg-raised px-2 py-1.5 text-[12.5px] text-fg-2">
          <BookMarked className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
          <span className="min-w-0">
            <span className="text-fg-3">Proposed standard: </span>
            {r.proposed_standard}
          </span>
        </p>
      )}
      <EvidenceChips evidence={r.evidence} onOpen={onOpenPanel} />
      <ReactionBar companyId={companyId} reflection={r} canRespond={canRespond} compact />
      {r.response_count ? (
        <p className="text-[11.5px] text-fg-3">
          {r.response_count} response{r.response_count === 1 ? '' : 's'} · the {AI_COFOUNDER_LABEL} answers or revises
        </p>
      ) : null}
    </li>
  )
}
