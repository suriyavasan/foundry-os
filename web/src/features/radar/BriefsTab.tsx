import { ChevronDown, ChevronRight, FileText } from 'lucide-react'
import { useState } from 'react'
import { useRadarBrief, useRadarBriefs, type RadarBrief, type RadarStatus } from '@/api/radar'
import { Badge, Boundary, Button } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatDateTime, formatRelative } from '@/lib/format'
import { IdeaCard } from './IdeasTab'
import { ItemRow } from './ItemRow'
import { BRIEF_KIND_LABEL, THEME_KIND } from './radarLogic'
import { UntrustedBanner, UntrustedText } from './Untrusted'

export function BriefsTab({ companyId, status }: { companyId: string; status: RadarStatus }) {
  const q = useRadarBriefs(companyId)
  const run = status.runner_role === 'none' ? null : status.runner_agent_name
  return (
    <div className="space-y-3">
      <UntrustedBanner>Briefs are written by agents from what they read outside. Summaries and themes are shown as plain text; the report itself is a normal artifact.</UntrustedBanner>
      <Boundary
        query={q}
        compact={false}
        milestone="M16"
        unavailableTitle="Briefs are not built yet"
        unavailableText="Radar briefs come with M16."
        isEmpty={(d) => d.length === 0}
        empty={{
          title: 'No briefs yet',
          description: status.enabled
            ? run
              ? `${run} writes the first brief after the next daily radar run. Until then the Feed shows what has been collected.`
              : 'No agent runs the daily radar yet. Hire a Market Researcher, or the AI Co-founder does it. The Feed shows what has been collected meanwhile.'
            : 'The radar is off, so nothing is read and no brief is written.',
        }}
      >
        {(briefs) => (
          <ul className="space-y-3">
            {briefs.map((b) => (
              <BriefCard key={b.id} companyId={companyId} brief={b} />
            ))}
          </ul>
        )}
      </Boundary>
    </div>
  )
}

function BriefCard({ companyId, brief }: { companyId: string; brief: RadarBrief }) {
  const { openPanel } = useCanvas()
  const [open, setOpen] = useState(false)
  const detail = useRadarBrief(companyId, open ? brief.id : null)
  return (
    <li className="rounded-md border border-line bg-surface">
      <div className="space-y-2 p-3">
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-3">
          <Badge tone="accent">{BRIEF_KIND_LABEL[brief.kind] ?? brief.kind}</Badge>
          <span title={formatDateTime(brief.created_at)}>{formatRelative(brief.created_at)}</span>
          {brief.author_name && <span>by {brief.author_name}</span>}
          <span>{brief.item_count} items read</span>
          <span>{brief.ideas_created} {brief.ideas_created === 1 ? 'idea' : 'ideas'} created</span>
          {brief.artifact_id && (
            <Button size="sm" variant="ghost" className="ml-auto" icon={<FileText className="size-3.5" />} onClick={() => openPanel({ kind: 'artifact', id: brief.artifact_id! })}>
              Open the report
            </Button>
          )}
        </div>
        <UntrustedText as="p" className="text-[13px] text-fg">{brief.summary}</UntrustedText>
        {brief.themes.length > 0 && (
          <ul className="space-y-1.5">
            {brief.themes.map((t, i) => {
              const k = THEME_KIND[t.kind] ?? { label: t.kind, tone: 'neutral' as const }
              return (
                <li key={`${t.title}-${i}`} className="flex flex-wrap items-baseline gap-2 text-[12.5px]">
                  <Badge tone={k.tone}>{k.label}</Badge>
                  <UntrustedText className="font-medium text-fg">{t.title}</UntrustedText>
                  {t.why && <UntrustedText className="text-fg-3">{t.why}</UntrustedText>}
                  {t.item_ids.length > 0 && <span className="text-[11.5px] text-fg-3">{t.item_ids.length} {t.item_ids.length === 1 ? 'item' : 'items'}</span>}
                </li>
              )
            })}
          </ul>
        )}
        <Button size="sm" variant="ghost" icon={open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Hide what it cites' : 'Show what it cites'}
        </Button>
      </div>
      {open && (
        <div className="border-t border-line p-3">
          <Boundary query={detail} compact rows={3} milestone="M16" isEmpty={(d) => d.items.length === 0 && d.ideas.length === 0} empty={{ title: 'Nothing cited', description: 'This brief cites no items and created no ideas.' }}>
            {(d) => (
              <div className="space-y-3">
                {d.items.length > 0 && (
                  <ul className="divide-y divide-line rounded-md border border-line bg-surface">
                    {d.items.map((it) => (
                      <ItemRow key={it.id} item={it} />
                    ))}
                  </ul>
                )}
                {d.ideas.length > 0 && (
                  <ul className="space-y-2">
                    {d.ideas.map((idea) => (
                      <IdeaCard key={idea.id} companyId={companyId} idea={idea} canDecide={false} />
                    ))}
                  </ul>
                )}
              </div>
            )}
          </Boundary>
        </div>
      )}
    </li>
  )
}
