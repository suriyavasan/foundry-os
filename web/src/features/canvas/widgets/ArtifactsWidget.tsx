import { Code2, Database, File, FileText, Link2, Palette, BarChart3, type LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import { useArtifacts } from '@/api'
import type { ArtifactKind } from '@/api/types'
import { Boundary, Widget } from '@/components/ui'
import { CreatorChip } from '@/features/shared/AgentChip'
import { ArtifactStatusBadge } from '@/features/shared/badges'
import { formatRelative } from '@/lib/format'
import { useCanvas } from '../CanvasContext'
import { useAgentMap } from '../hooks'

export const ARTIFACT_ICON: Record<ArtifactKind, LucideIcon> = {
  document: FileText,
  code: Code2,
  design: Palette,
  report: BarChart3,
  data: Database,
  link: Link2,
  other: File,
}

export function ArtifactsWidget({ className }: { className?: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useArtifacts(companyId)
  const { map } = useAgentMap(companyId)
  const latest = useMemo(() => [...(q.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 8), [q.data])

  return (
    <Widget title="Artifacts" subtitle="Latest" onExpand={() => openPanel({ kind: 'artifacts' })} expandLabel="Open all artifacts" className={className}>
      <Boundary
        query={q}
        isEmpty={(d) => d.items.length === 0}
        empty={{ title: 'No artifacts yet', description: 'Documents, code and reports the team produces show up here.' }}
        unavailableText="Artifact storage comes with the work engine."
      >
        {() => (
          <ul className="divide-y divide-line">
            {latest.map((a) => {
              const Icon = ARTIFACT_ICON[a.kind] ?? File
              return (
                <li key={a.id}>
                  <button type="button" onClick={() => openPanel({ kind: 'artifact', id: a.id })} className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-hover">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-hover text-fg-2">
                      <Icon className="size-3.5" aria-label={a.kind} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] text-fg">{a.title}</span>
                      <span className="flex items-center gap-1.5 text-[11.5px] text-fg-3">
                        <CreatorChip type={a.created_by_type} id={a.created_by_id} agents={map} />
                        <span aria-hidden>·</span>
                        <span>v{a.current_version}</span>
                        <span aria-hidden>·</span>
                        <time dateTime={a.updated_at}>{formatRelative(a.updated_at)}</time>
                      </span>
                    </span>
                    <ArtifactStatusBadge status={a.status} />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Boundary>
    </Widget>
  )
}
