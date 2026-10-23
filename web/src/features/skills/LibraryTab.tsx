import { Search, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSeedSkills, useSkills } from '@/api'
import { Boundary, Button, Card, EmptyState, Input, Select, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { errorMessage } from '@/api/client'
import {
  NO_FILTERS,
  SOURCE_KINDS,
  filterSkills,
  isFlagged,
  skillStatusInfo,
  sortSkills,
  sourceKindLabel,
  summarizeLibrary,
  tagOptions,
  type LibraryFilters,
} from '@/lib/skills'
import { useAssignedCounts } from './hooks'
import { SkillStatusBadge, SourceBadge, VerdictBadge } from './SkillBadges'

const STATUS_OPTIONS = ['active', 'quarantined', 'archived'] as const

export function LibraryTab() {
  const { companyId, openPanel } = useCanvas()
  const q = useSkills(companyId)
  const assigned = useAssignedCounts(companyId)
  const seed = useSeedSkills(companyId)
  const toast = useToast()
  const [filters, setFilters] = useState<LibraryFilters>(NO_FILTERS)
  const patch = (p: Partial<LibraryFilters>) => setFilters((f) => ({ ...f, ...p }))

  const all = q.data
  const tags = useMemo(() => tagOptions(all ?? []), [all])
  const rows = useMemo(() => sortSkills(filterSkills(all ?? [], filters)), [all, filters])
  const summary = useMemo(() => summarizeLibrary(all ?? []), [all])
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS)

  async function sync() {
    try {
      const r = await seed.mutateAsync()
      toast.success(r.status === 'already_queued' ? 'Library sync is already queued' : 'Library sync queued. New skills appear here when the worker finishes.')
    } catch (e) {
      toast.error(errorMessage(e, 'Could not queue the library sync'))
    }
  }

  return (
    <div className="space-y-3">
      <Boundary
        query={q}
        milestone="M4"
        compact={false}
        unavailableTitle="Skills are not built yet"
        unavailableText="The skill library comes with Skills & HR."
        isEmpty={(d) => d.length === 0}
        empty={{
          title: 'The skill library is empty',
          description: 'The built-in library is imported by the background worker. If it never fills, start the worker or queue a sync.',
          action: (
            <Button variant="primary" loading={seed.isPending} onClick={sync}>
              Sync built-in library
            </Button>
          ),
        }}
      >
        {() => (
          <>
            <div className="flex flex-wrap items-center gap-2" role="search" aria-label="Filter skills">
              <div className="relative min-w-52 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-3" aria-hidden />
                <Input aria-label="Search skills" placeholder="Search name, description, tag" className="pl-8" value={filters.q} onChange={(e) => patch({ q: e.target.value })} />
              </div>
              <Select aria-label="Status" className="w-auto" value={filters.status} onChange={(e) => patch({ status: e.target.value as LibraryFilters['status'] })}>
                <option value="all">Any status</option>
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {skillStatusInfo(s).label}
                  </option>
                ))}
              </Select>
              <Select aria-label="Source" className="w-auto" value={filters.source} onChange={(e) => patch({ source: e.target.value as LibraryFilters['source'] })}>
                <option value="all">Any source</option>
                {SOURCE_KINDS.map((s) => (
                  <option key={s} value={s}>
                    {sourceKindLabel(s)}
                  </option>
                ))}
              </Select>
              <Select aria-label="Tag" className="w-auto max-w-44" value={filters.tag} onChange={(e) => patch({ tag: e.target.value })}>
                <option value="all">Any tag</option>
                {tags.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
              {filtered && (
                <Button variant="ghost" size="sm" icon={<X className="size-3.5" />} onClick={() => setFilters(NO_FILTERS)}>
                  Clear
                </Button>
              )}
            </div>
            <p className="text-[12px] text-fg-3" aria-live="polite">
              {rows.length === summary.total ? `${summary.total} skills` : `${rows.length} of ${summary.total} skills`}
              {summary.quarantined > 0 && <span className="text-danger"> · {summary.quarantined} quarantined</span>}
              {summary.flagged - summary.quarantined > 0 && <span className="text-warning"> · {summary.flagged - summary.quarantined} flagged by the scan</span>}
              {assigned.failed && ' · agent counts unavailable'}
            </p>
            <Card>
              {rows.length === 0 ? (
                <EmptyState title="No skills match" description="Try different words or clear the filters." action={<Button onClick={() => setFilters(NO_FILTERS)}>Clear filters</Button>} />
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH>Skill</TH>
                      <TH>Source</TH>
                      <TH>License</TH>
                      <TH>Trust</TH>
                      <TH>Scan</TH>
                      <TH>Status</TH>
                      <TH className="text-right">Agents</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {rows.map((s) => (
                      <TR key={s.id} interactive className={s.status === 'quarantined' ? 'bg-danger-subtle' : isFlagged(s) ? 'bg-warning-subtle' : undefined}>
                        <TD className="max-w-80 py-2">
                          <button type="button" className="block text-left font-medium hover:underline" onClick={() => openPanel({ kind: 'skill', id: s.id })}>
                            {s.name}
                          </button>
                          <div className="line-clamp-1 text-[12px] text-fg-3" title={s.description}>
                            {s.category ? `${s.category} · ` : ''}
                            {s.description}
                          </div>
                        </TD>
                        <TD>
                          <SourceBadge kind={s.source_kind} />
                        </TD>
                        <TD className="text-fg-2">{s.license ?? <span className="text-fg-3">none</span>}</TD>
                        <TD className="text-fg-2">{s.trust}</TD>
                        <TD>
                          <VerdictBadge verdict={s.scan_verdict} bare />
                        </TD>
                        <TD>
                          <SkillStatusBadge status={s.status} bare />
                        </TD>
                        <TD className="text-right tabular-nums text-fg-2">{assigned.loading ? '…' : assigned.failed ? '—' : (assigned.counts.get(s.id) ?? 0)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </Card>
          </>
        )}
      </Boundary>
    </div>
  )
}
