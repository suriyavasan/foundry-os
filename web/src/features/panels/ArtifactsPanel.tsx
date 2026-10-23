import { useMemo, useState } from 'react'
import { useArtifacts } from '@/api'
import type { ArtifactKind, ArtifactStatus } from '@/api/types'
import { Boundary, Card, Chip, Input, Select, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { ARTIFACT_ICON } from '@/features/canvas/widgets/ArtifactsWidget'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { CreatorChip } from '@/features/shared/AgentChip'
import { ARTIFACT_KINDS, ArtifactStatusBadge } from '@/features/shared/badges'
import { formatRelative } from '@/lib/format'

const STATUSES: ArtifactStatus[] = ['draft', 'submitted', 'accepted', 'rejected']

export function ArtifactsPanel() {
  const { companyId, openPanel } = useCanvas()
  const [kind, setKind] = useState<ArtifactKind | ''>('')
  const [status, setStatus] = useState<ArtifactStatus | ''>('')
  const [search, setSearch] = useState('')
  const filters = useMemo(() => ({ ...(kind ? { kind } : null), ...(status ? { status } : null), ...(search.trim() ? { q: search.trim() } : null) }), [kind, status, search])
  const q = useArtifacts(companyId, filters)
  const { map } = useAgentMap(companyId)
  const items = useMemo(() => [...(q.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [q.data])
  const filtered = Object.keys(filters).length > 0

  return (
    <PanelFrame title="Artifacts" subtitle="Documents, code and reports produced by the company">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input aria-label="Search artifacts" placeholder="Search titles…" className="h-7 w-48 text-[12.5px]" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Select aria-label="Filter by kind" className="h-7 w-auto text-[12.5px]" value={kind} onChange={(e) => setKind(e.target.value as ArtifactKind | '')}>
          <option value="">Any kind</option>
          {ARTIFACT_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by status" className="h-7 w-auto text-[12.5px]" value={status} onChange={(e) => setStatus(e.target.value as ArtifactStatus | '')}>
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        {q.data && <span className="text-[12px] text-fg-3">{items.length} shown{q.data.next_cursor ? ' (more exist)' : ''}</span>}
      </div>
      <Card>
        <Boundary
          query={q}
          compact={false}
          unavailableTitle="Artifacts are not built yet"
          unavailableText="Artifact storage with versioning comes with the work engine."
          isEmpty={(d) => d.items.length === 0}
          empty={{ title: filtered ? 'No artifacts match' : 'No artifacts yet', description: filtered ? 'Adjust the filters.' : 'Deliverables produced by agents are stored here, with every version.' }}
        >
          {() => (
            <Table>
              <THead>
                <TR>
                  <TH>Artifact</TH>
                  <TH>Kind</TH>
                  <TH>Status</TH>
                  <TH>Author</TH>
                  <TH>Version</TH>
                  <TH>Updated</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((a) => {
                  const Icon = ARTIFACT_ICON[a.kind]
                  return (
                    <TR key={a.id} interactive>
                      <TD className="max-w-[300px]">
                        <button type="button" onClick={() => openPanel({ kind: 'artifact', id: a.id })} className="flex max-w-full items-center gap-2 text-left font-medium hover:underline">
                          <Icon className="size-3.5 shrink-0 text-fg-3" aria-hidden />
                          <span className="truncate">{a.title}</span>
                        </button>
                      </TD>
                      <TD>
                        <Chip>{a.kind}</Chip>
                      </TD>
                      <TD>
                        <ArtifactStatusBadge status={a.status} />
                      </TD>
                      <TD>
                        <CreatorChip type={a.created_by_type} id={a.created_by_id} agents={map} />
                      </TD>
                      <TD className="tabular-nums text-fg-2">v{a.current_version}</TD>
                      <TD className="text-fg-3">{formatRelative(a.updated_at)}</TD>
                    </TR>
                  )
                })}
              </TBody>
            </Table>
          )}
        </Boundary>
      </Card>
    </PanelFrame>
  )
}
