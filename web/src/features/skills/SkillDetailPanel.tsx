import { AlertTriangle, Archive, ShieldAlert, ShieldOff } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, errorMessage, useSkill, useSkillAction, useSkillFile } from '@/api'
import type { Agent, SkillDetail } from '@/api/types'
import { Avatar } from '@/components/avatar'
import { AgentAvatar } from '@/components/avatar'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  ConfirmDialog,
  DefinitionList,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  FormError,
  LoadingState,
  Markdown,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  useToast,
} from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { formatDateTime } from '@/lib/format'
import { readScanReport, scanVerdictInfo, severityTone, skillMdPath, sourceKindLabel, trustLabel } from '@/lib/skills'
import { SkillStatusBadge, VerdictBadge } from './SkillBadges'

export function SkillDetailPanel({ skillId }: { skillId: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useSkill(companyId, skillId)
  const back = () => openPanel({ kind: 'skills' })

  if (q.isError && q.error instanceof ApiError && q.error.status === 404) {
    return (
      <PanelFrame title="Skill not found" onBack={back}>
        <EmptyState title="Skill not found" description="This skill does not exist in this company's library." action={<button type="button" className="text-accent-fg hover:underline" onClick={back}>Back to the library</button>} />
      </PanelFrame>
    )
  }
  if (q.isPending) {
    return (
      <PanelFrame title="Skill" onBack={back}>
        <Card>
          <LoadingState rows={6} label="Loading skill" />
        </Card>
      </PanelFrame>
    )
  }
  if (q.isError) {
    return (
      <PanelFrame title="Skill" onBack={back}>
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      </PanelFrame>
    )
  }
  return <SkillDetail skill={q.data} onBack={back} />
}

function SkillDetail({ skill, onBack }: { skill: SkillDetail; onBack: () => void }) {
  const { companyId } = useCanvas()
  const toast = useToast()
  const action = useSkillAction(companyId, skill.id)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [unquarantineOpen, setUnquarantineOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const report = useMemo(() => readScanReport(skill), [skill])
  const verdict = scanVerdictInfo(skill.scan_verdict)

  async function archive() {
    setError(null)
    try {
      await action.mutateAsync({ action: 'archive' })
      toast.success(`${skill.name} archived`)
      setArchiveOpen(false)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <PanelFrame
      title={
        <span className="flex items-center gap-2.5">
          {skill.name}
          <SkillStatusBadge status={skill.status} />
        </span>
      }
      subtitle={skill.slug}
      onBack={onBack}
      actions={
        <>
          {skill.status === 'quarantined' && (
            <Button variant="danger" icon={<ShieldOff className="size-3.5" />} onClick={() => setUnquarantineOpen(true)}>
              Unquarantine…
            </Button>
          )}
          {skill.status !== 'archived' && (
            <Button icon={<Archive className="size-3.5" />} onClick={() => { setError(null); setArchiveOpen(true) }}>
              Archive
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {skill.status === 'quarantined' && (
          <Banner tone="danger" icon={<ShieldAlert className="size-4" aria-hidden />} title="Quarantined">
            The security scan verdict is <strong>{verdict.label.toLowerCase()}</strong>
            {report.finding_count ? ` with ${report.finding_count} finding${report.finding_count === 1 ? '' : 's'}` : ''}. It is stored so you can inspect it, but no agent can use it. Read the scan report below before deciding anything.
          </Banner>
        )}
        {skill.status !== 'quarantined' && verdict.flagged && (
          <Banner tone={verdict.tone === 'danger' ? 'danger' : 'warning'} icon={<AlertTriangle className="size-4" aria-hidden />} title={`Scan verdict: ${verdict.label}`}>
            {verdict.help} This skill is {skill.status}
            {skill.trust === 'builtin' ? ' because it is built in (the verdict is recorded, not enforced).' : '.'}
          </Banner>
        )}
        {skill.status === 'archived' && (
          <Banner tone="neutral" icon={<Archive className="size-4" aria-hidden />} title="Archived">
            Archived skills are never given to the runtime, and library syncs do not bring them back.
          </Banner>
        )}

        <Card>
          <CardBody className="space-y-4">
            <p className="text-[13.5px] text-fg">{skill.description}</p>
            <DefinitionList
              items={[
                { label: 'Slug', value: <span className="font-mono text-[12px]">{skill.slug}</span> },
                { label: 'Scan', value: <VerdictBadge verdict={skill.scan_verdict} /> },
                { label: 'Source', value: <span>{sourceKindLabel(skill.source_kind)}</span> },
                { label: 'Source reference', value: <span className="font-mono text-[12px] break-all">{skill.source_ref}</span> },
                { label: 'Trust', value: <span title="builtin: Hermes and Foundry AI OS skills. trusted: a few named GitHub orgs. community: everything else.">{trustLabel(skill.trust)}</span> },
                { label: 'License', value: skill.license ?? <span className="text-fg-3">none declared</span> },
                { label: 'Version', value: skill.version ?? <span className="text-fg-3">not set</span> },
                { label: 'Author', value: skill.author ?? <span className="text-fg-3">not set</span> },
                { label: 'Category', value: skill.category ?? <span className="text-fg-3">none</span> },
                {
                  label: 'Tags',
                  value: skill.tags.length ? (
                    <span className="flex flex-wrap gap-1">
                      {skill.tags.map((t) => (
                        <Chip key={t}>{t}</Chip>
                      ))}
                    </span>
                  ) : (
                    <span className="text-fg-3">none</span>
                  ),
                },
                {
                  label: 'Needs toolsets',
                  value: skill.requires_toolsets.length ? skill.requires_toolsets.map((t) => <Chip key={t} className="mr-1">{t}</Chip>) : <span className="text-fg-3">none</span>,
                },
                {
                  label: 'Needs env variables',
                  value: skill.required_env.length ? skill.required_env.map((t) => <Chip key={t} className="mr-1">{t}</Chip>) : <span className="text-fg-3">none</span>,
                },
                { label: 'Added', value: formatDateTime(skill.created_at) },
                { label: 'Content hash', value: <span className="font-mono text-[11.5px] break-all text-fg-3">{skill.blob_sha256}</span> },
              ]}
            />
          </CardBody>
        </Card>

        <Tabs defaultValue="preview">
          <TabsList aria-label="Skill sections">
            <TabsTrigger value="preview">SKILL.md</TabsTrigger>
            <TabsTrigger value="files">Files ({skill.files.length})</TabsTrigger>
            <TabsTrigger value="scan">
              Scan report{report.finding_count ? ` (${report.finding_count})` : ''}
            </TabsTrigger>
            <TabsTrigger value="frontmatter">Frontmatter</TabsTrigger>
            <TabsTrigger value="agents">Agents ({skill.assigned_agents.length})</TabsTrigger>
          </TabsList>
          <TabsContent value="preview">
            <PreviewTab skill={skill} />
          </TabsContent>
          <TabsContent value="files">
            <FilesTab skill={skill} />
          </TabsContent>
          <TabsContent value="scan">
            <ScanTab skill={skill} />
          </TabsContent>
          <TabsContent value="frontmatter">
            <FrontmatterTab skill={skill} />
          </TabsContent>
          <TabsContent value="agents">
            <AssignedAgentsTab skill={skill} />
          </TabsContent>
        </Tabs>
      </div>

      <ConfirmDialog
        open={archiveOpen}
        onOpenChange={setArchiveOpen}
        title={`Archive ${skill.name}?`}
        confirmLabel="Archive skill"
        loading={action.isPending}
        error={error}
        onConfirm={archive}
        description={
          <>
            Archived skills are removed from the runtime and cannot be assigned. Library syncs will not bring it back.
            {skill.assigned_agents.length > 0 && (
              <>
                {' '}
                It is currently assigned to {skill.assigned_agents.length} agent{skill.assigned_agents.length === 1 ? '' : 's'}: {skill.assigned_agents.map((a) => a.name).join(', ')}.
              </>
            )}
          </>
        }
      />
      <UnquarantineDialog open={unquarantineOpen} onOpenChange={setUnquarantineOpen} skill={skill} findings={report.finding_count} />
    </PanelFrame>
  )
}

function Banner({ tone, icon, title, children }: { tone: 'danger' | 'warning' | 'neutral'; icon: React.ReactNode; title: string; children: React.ReactNode }) {
  const cls = tone === 'danger' ? 'border-danger/40 bg-danger-subtle' : tone === 'warning' ? 'border-warning/40 bg-warning-subtle' : 'border-line bg-surface'
  const iconCls = tone === 'danger' ? 'text-danger' : tone === 'warning' ? 'text-warning' : 'text-fg-3'
  return (
    <div role="status" className={`flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-[13px] ${cls}`}>
      <span className={`mt-0.5 shrink-0 ${iconCls}`}>{icon}</span>
      <div className="min-w-0">
        <p className="font-semibold text-fg">{title}</p>
        <p className="mt-0.5 text-fg-2">{children}</p>
      </div>
    </div>
  )
}

/** The explicit human override of the scanner. Needs an acknowledgement; the server audits the overridden verdict. */
function UnquarantineDialog({ open, onOpenChange, skill, findings }: { open: boolean; onOpenChange: (o: boolean) => void; skill: SkillDetail; findings: number }) {
  const { companyId } = useCanvas()
  const action = useSkillAction(companyId, skill.id)
  const toast = useToast()
  const [ack, setAck] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const verdict = scanVerdictInfo(skill.scan_verdict)

  async function confirm() {
    setError(null)
    try {
      await action.mutateAsync({ action: 'unquarantine', note: note.trim() || undefined })
      toast.success(`${skill.name} is active. Overriding the ${verdict.label.toLowerCase()} scan verdict was recorded in the audit log.`)
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !action.isPending && onOpenChange(o)}
      size="sm"
      title={`Unquarantine ${skill.name}?`}
      description="This overrides the security scanner."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={action.isPending}>
            Cancel
          </Button>
          <Button variant="danger" disabled={!ack} loading={action.isPending} onClick={confirm}>
            Override scanner and activate
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-[13px] text-fg-2">
        <p>
          The scanner rated this skill <strong className="text-fg">{verdict.label.toLowerCase()}</strong> ({findings} finding{findings === 1 ? '' : 's'}, trust: {trustLabel(skill.trust).toLowerCase()}). Once active, it can be assigned to agents, and they will follow its instructions and run its scripts with their own tool permissions.
        </p>
        <p>The override is recorded in the audit log with your name and the verdict you overrode. Archive the skill again to undo it.</p>
        <label className="flex items-start gap-2 text-fg">
          <input type="checkbox" className="mt-0.5" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          <span>I read the scan report and accept the risk of using this skill.</span>
        </label>
        <Field label="Note (optional)" hint="Why this is safe to use. Saved with the audit record.">
          {({ id, describedBy }) => <Textarea id={id} rows={2} maxLength={1000} aria-describedby={describedBy} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}

function PreviewTab({ skill }: { skill: SkillDetail }) {
  const { companyId } = useCanvas()
  const path = skillMdPath(skill.files)
  const q = useSkillFile(companyId, skill.id, path)
  return (
    <Card>
      {!path ? (
        <EmptyState title="No SKILL.md" description="This skill has no SKILL.md at its root." />
      ) : q.isPending ? (
        <LoadingState rows={6} label="Loading SKILL.md" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <CardBody>
          <Markdown source={q.data.content} />
          {q.data.truncated && <p className="mt-3 border-t border-line pt-2 text-[12px] text-fg-3">Preview truncated at 200 KB.</p>}
        </CardBody>
      )}
    </Card>
  )
}

function FilesTab({ skill }: { skill: SkillDetail }) {
  const { companyId } = useCanvas()
  const [selected, setSelected] = useState<string | null>(null)
  const q = useSkillFile(companyId, skill.id, selected)
  if (skill.files.length === 0) return <Card><EmptyState title="No files" /></Card>
  return (
    <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_1fr]">
      <Card>
        <ul className="max-h-96 divide-y divide-line overflow-y-auto" aria-label="Skill files">
          {skill.files.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                aria-pressed={selected === f.path}
                onClick={() => setSelected(f.path)}
                className={`flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left text-[12.5px] hover:bg-hover ${selected === f.path ? 'bg-active' : ''}`}
              >
                <span className="min-w-0 truncate font-mono">{f.path}</span>
                <span className="shrink-0 text-[11.5px] text-fg-3 tabular-nums">{formatBytes(f.size_bytes)}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <Card className="min-w-0">
        {!selected ? (
          <EmptyState compact title="Pick a file" description="Text files can be previewed here." />
        ) : q.isPending ? (
          <LoadingState rows={5} label="Loading file" />
        ) : q.isError ? (
          q.error instanceof ApiError && q.error.status === 409 ? (
            <EmptyState compact title="Binary file" description="There is no text preview for this file." />
          ) : (
            <ErrorState compact error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )
        ) : (
          <>
            <pre tabIndex={0} aria-label={`Contents of ${q.data.path}`} className="max-h-96 overflow-auto p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-fg">{q.data.content}</pre>
            {q.data.truncated && <p className="border-t border-line px-3 py-2 text-[12px] text-fg-3">Preview truncated at 200 KB.</p>}
          </>
        )}
      </Card>
    </div>
  )
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function ScanTab({ skill }: { skill: SkillDetail }) {
  const report = useMemo(() => readScanReport(skill), [skill])
  return (
    <Card>
      <CardHeader
        title="Security scan"
        description={report.scanner_version ? `Scanner ${report.scanner_version}` : 'The scanner version was not recorded'}
        actions={<VerdictBadge verdict={String(report.verdict)} />}
      />
      {report.summary && <p className="border-b border-line px-4 py-2.5 text-[13px] text-fg-2">{report.summary}</p>}
      {skill.scan_verdict === 'unscanned' ? (
        <EmptyState title="Not scanned" description="The scanner was not available when this built-in skill was stored, so there is no report. Nothing is claimed about its safety." />
      ) : report.findings.length === 0 ? (
        <EmptyState title="No findings" description="The scanner reported nothing for this skill." />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Severity</TH>
              <TH>Category</TH>
              <TH>Pattern</TH>
              <TH>Location</TH>
              <TH>What it means</TH>
            </TR>
          </THead>
          <TBody>
            {report.findings.map((f, i) => (
              <TR key={i} className="align-top">
                <TD className="align-top">
                  <Badge tone={severityTone(f.severity)}>{f.severity ?? 'unknown'}</Badge>
                </TD>
                <TD className="align-top text-fg-2">{f.category ?? '—'}</TD>
                <TD className="align-top font-mono text-[12px] break-all">{f.pattern ?? '—'}</TD>
                <TD className="align-top font-mono text-[12px] whitespace-nowrap text-fg-2">
                  {f.file ?? '—'}
                  {f.line != null ? `:${f.line}` : ''}
                </TD>
                <TD className="align-top text-fg-2">{f.description ?? '—'}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  )
}

function FrontmatterTab({ skill }: { skill: SkillDetail }) {
  const entries = Object.entries(skill.frontmatter ?? {})
  return (
    <Card>
      {entries.length === 0 ? (
        <EmptyState title="No frontmatter" description="SKILL.md has no YAML frontmatter." />
      ) : (
        <CardBody>
          <DefinitionList
            items={entries.map(([k, v]) => ({
              label: k,
              value: typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : <pre className="font-mono text-[12px] whitespace-pre-wrap">{JSON.stringify(v, null, 2)}</pre>,
            }))}
          />
        </CardBody>
      )}
    </Card>
  )
}

function AssignedAgentsTab({ skill }: { skill: SkillDetail }) {
  const { companyId, openPanel } = useCanvas()
  const { map } = useAgentMap(companyId)
  if (skill.assigned_agents.length === 0) {
    return (
      <Card>
        <EmptyState title="Not assigned to anyone" description={skill.status === 'active' ? 'Assign it from an agent’s Skills tab.' : 'Only active skills can be assigned to agents.'} />
      </Card>
    )
  }
  return (
    <Card>
      <ul className="divide-y divide-line">
        {skill.assigned_agents.map((a) => {
          const full: Agent | undefined = map.get(a.id)
          return (
            <li key={a.id} className="flex items-center gap-3 px-4 py-2.5">
              {full ? <AgentAvatar agent={full} size="sm" /> : <Avatar family="generic" size="sm" />}
              <button type="button" className="min-w-0 flex-1 text-left hover:underline" onClick={() => openPanel({ kind: 'agent', id: a.id })}>
                <span className="font-medium text-fg">{a.name}</span>
                <span className="ml-2 text-[12px] text-fg-3">{a.role_title}</span>
              </button>
              {full ? <StatusBadge status={full.status} /> : <Badge tone="neutral">{a.status}</Badge>}
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
