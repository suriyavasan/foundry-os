import { Download } from 'lucide-react'
import { useState } from 'react'
import { ApiError, artifactContentUrl, useAllTasks, useArtifact, useArtifactText } from '@/api'
import type { Artifact, ArtifactVersion } from '@/api/types'
import { Card, CardBody, CardHeader, Chip, DefinitionList, EmptyState, ErrorState, LoadingState, Select, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { CreatorChip } from '@/features/shared/AgentChip'
import { ArtifactStatusBadge } from '@/features/shared/badges'
import { formatDateTime } from '@/lib/format'
import { isPreviewable, prettyPreview } from '@/lib/preview'

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function ArtifactPanel({ artifactId }: { artifactId: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useArtifact(companyId, artifactId)

  if (q.isError && q.error instanceof ApiError && q.error.status === 404) {
    return (
      <PanelFrame title="Artifact not found" onBack={() => openPanel({ kind: 'artifacts' })}>
        <EmptyState title="Artifact not found" description="This artifact does not exist in this company." />
      </PanelFrame>
    )
  }
  if (q.isPending) {
    return (
      <PanelFrame title="Artifact" onBack={() => openPanel({ kind: 'artifacts' })}>
        <Card>
          <LoadingState rows={6} label="Loading artifact" />
        </Card>
      </PanelFrame>
    )
  }
  if (q.isError) {
    return (
      <PanelFrame title="Artifact" onBack={() => openPanel({ kind: 'artifacts' })}>
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      </PanelFrame>
    )
  }
  return <ArtifactBody artifact={q.data} companyId={companyId} />
}

function ArtifactBody({ artifact, companyId }: { artifact: Artifact; companyId: string }) {
  const { openPanel } = useCanvas()
  const { map } = useAgentMap(companyId)
  const tasks = useAllTasks(companyId)
  const versions: ArtifactVersion[] = [...(artifact.versions ?? [])].sort((a, b) => b.version - a.version)
  const [picked, setPicked] = useState<number | null>(null)
  const version = picked ?? artifact.current_version
  const meta = versions.find((v) => v.version === version)
  const task = tasks.data?.find((t) => t.id === artifact.task_id)
  const previewable = isPreviewable(artifact.mime_type, artifact.kind)
  const tooBig = (meta?.size_bytes ?? 0) > 1024 * 1024
  const text = useArtifactText(companyId, artifact.id, version, previewable && !tooBig)

  return (
    <PanelFrame title={artifact.title} subtitle={<ArtifactStatusBadge status={artifact.status} />} onBack={() => openPanel({ kind: 'artifacts' })}>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader
              title={`Version ${version}`}
              description={meta ? `${formatBytes(meta.size_bytes)} · ${formatDateTime(meta.created_at)}` : undefined}
              actions={
                <>
                  {versions.length > 1 && (
                    <Select aria-label="Version" className="h-7 w-auto text-[12.5px]" value={version} onChange={(e) => setPicked(Number(e.target.value))}>
                      {versions.map((v) => (
                        <option key={v.version} value={v.version}>
                          v{v.version}
                          {v.version === artifact.current_version ? ' (current)' : ''}
                        </option>
                      ))}
                    </Select>
                  )}
                  <a
                    href={artifactContentUrl(companyId, artifact.id, version)}
                    download
                    className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line-strong bg-raised px-2.5 text-[12.5px] font-medium text-fg hover:bg-hover"
                  >
                    <Download className="size-3.5" aria-hidden /> Download
                  </a>
                </>
              }
            />
            {!previewable ? (
              <EmptyState compact title="No inline preview" description={`${artifact.mime_type} cannot be shown here. Download it to open it.`} />
            ) : tooBig ? (
              <EmptyState compact title="Too large to preview" description="Download the file to read it." />
            ) : text.isPending ? (
              <LoadingState rows={6} label="Loading content" />
            ) : text.isError ? (
              <ErrorState compact error={text.error} onRetry={() => void text.refetch()} retrying={text.isFetching} />
            ) : (
              <div>
                <pre tabIndex={0} aria-label="Artifact content" className="max-h-[560px] overflow-auto p-4 font-mono text-[12.5px] leading-relaxed break-words whitespace-pre-wrap text-fg">
                  {prettyPreview(text.data.text, artifact.mime_type)}
                </pre>
                {text.data.truncated && <p className="border-t border-line px-4 py-2 text-[12px] text-fg-3">Preview truncated. Download for the full file.</p>}
                <p className="border-t border-line px-4 py-2 text-[12px] text-fg-3">Shown as plain text, not rendered.</p>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader title="Versions" />
            {versions.length === 0 ? (
              <EmptyState compact title="Version history not reported" description="The API did not include versions for this artifact." />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Version</TH>
                    <TH>Author</TH>
                    <TH>Size</TH>
                    <TH>Created</TH>
                    <TH>Note</TH>
                  </TR>
                </THead>
                <TBody>
                  {versions.map((v) => (
                    <TR key={v.version} interactive>
                      <TD>
                        <button type="button" className="font-medium hover:underline" onClick={() => setPicked(v.version)}>
                          v{v.version}
                        </button>
                        {v.version === artifact.current_version && <Chip className="ml-2">current</Chip>}
                      </TD>
                      <TD>
                        <CreatorChip type={v.created_by_type} id={v.created_by_id} agents={map} />
                      </TD>
                      <TD className="tabular-nums text-fg-2">{formatBytes(v.size_bytes)}</TD>
                      <TD className="text-fg-2">{formatDateTime(v.created_at)}</TD>
                      <TD className="max-w-[200px] truncate text-fg-3">{v.note ?? '—'}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Card>
        </div>

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DefinitionList
              items={[
                { label: 'Kind', value: <Chip>{artifact.kind}</Chip> },
                { label: 'Type', value: <span className="font-mono text-[12px]">{artifact.mime_type}</span> },
                { label: 'Author', value: <CreatorChip type={artifact.created_by_type} id={artifact.created_by_id} agents={map} size="sm" /> },
                {
                  label: 'Task',
                  value: artifact.task_id ? (
                    <button type="button" className="text-left text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: artifact.task_id! })}>
                      {task?.title ?? 'Open task'}
                    </button>
                  ) : (
                    <span className="text-fg-3">—</span>
                  ),
                },
                {
                  label: 'Run',
                  value:
                    artifact.run_id && artifact.task_id ? (
                      <button type="button" className="text-left text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: artifact.task_id!, run: artifact.run_id! })}>
                        Open run
                      </button>
                    ) : (
                      <span className="text-fg-3">—</span>
                    ),
                },
                { label: 'Created', value: formatDateTime(artifact.created_at) },
                { label: 'Updated', value: formatDateTime(artifact.updated_at) },
              ]}
            />
          </CardBody>
        </Card>
      </div>
    </PanelFrame>
  )
}
