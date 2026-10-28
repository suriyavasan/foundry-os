import { useState } from 'react'
import { ApiError, errorMessage, usePolicy, useProjects } from '@/api'
import { useDecideRadarIdea, useRadarIdeas, type IdeaStatus, type RadarIdea } from '@/api/radar'
import type { CompanyPolicy } from '@/api/types'
import { Badge, Boundary, Button, Dialog, Field, FormError, Select, Textarea, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative } from '@/lib/format'
import { IDEA_FILTERS, IDEA_KIND_LABEL, IDEA_ORIGIN_LABEL, IDEA_STATUS, evidenceCount, ideaActions, priorityLabel } from './radarLogic'
import { ExternalLink, UntrustedBanner, UntrustedText } from './Untrusted'

export function IdeasTab({ companyId, canDecide }: { companyId: string; canDecide: boolean }) {
  const [filter, setFilter] = useState<IdeaStatus | 'all'>('open')
  const q = useRadarIdeas(companyId, filter)
  const policy = usePolicy(companyId)
  const autoAccept = (policy.data as (CompanyPolicy & { radar_auto_accept?: boolean }) | undefined)?.radar_auto_accept
  return (
    <div className="space-y-3">
      <UntrustedBanner>Ideas are written by agents from what they read. Their text is shown as plain text and nothing in it can change a setting or approve anything.</UntrustedBanner>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12.5px] text-fg-3">
          {autoAccept === false
            ? 'Every open idea waits for you: accept it into a project or reject it.'
            : autoAccept === true
              ? 'The AI Co-founder turns open ideas into work on its own. You can decide any open idea first.'
              : 'The backlog of changes worth making, with the evidence for each.'}
        </p>
        <Select aria-label="Filter ideas" className="h-7 w-auto text-[12.5px]" value={filter} onChange={(e) => setFilter(e.target.value as IdeaStatus | 'all')}>
          {IDEA_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
      </div>
      <Boundary
        query={q}
        compact={false}
        milestone="M16"
        unavailableTitle="Ideas are not built yet"
        unavailableText="The idea backlog comes with M16."
        isEmpty={(d) => d.length === 0}
        empty={{
          title: filter === 'open' ? 'No open ideas' : filter === 'all' ? 'No ideas yet' : 'Nothing with this status',
          description: filter === 'open' ? 'Ideas appear after a radar run or the weekly self-critique. Choose another filter to see the decided ones.' : 'Choose another filter to see the rest.',
        }}
      >
        {(ideas) => (
          <ul className="space-y-3">
            {ideas.map((idea) => (
              <IdeaCard key={idea.id} companyId={companyId} idea={idea} canDecide={canDecide} />
            ))}
          </ul>
        )}
      </Boundary>
    </div>
  )
}

export function IdeaCard({ companyId, idea, canDecide }: { companyId: string; idea: RadarIdea; canDecide: boolean }) {
  const { openPanel } = useCanvas()
  const [dialog, setDialog] = useState<'accept' | 'reject' | null>(null)
  const decide = useDecideRadarIdea(companyId)
  const toast = useToast()
  const status = IDEA_STATUS[idea.status] ?? { label: idea.status, tone: 'neutral' as const }
  const actions = ideaActions(idea, canDecide)
  const became = idea.task_id || idea.project_id || idea.goal_id
  return (
    <li className="space-y-2 rounded-md border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-3">
        <Badge tone={status.tone}>{status.label}</Badge>
        <Badge tone="neutral">{IDEA_KIND_LABEL[idea.kind] ?? idea.kind}</Badge>
        <span>{IDEA_ORIGIN_LABEL[idea.origin] ?? idea.origin}</span>
        <span title={`Impact ${idea.impact}, confidence ${idea.confidence}, effort ${idea.effort}. Score = impact x confidence x 4 / effort.`}>
          Priority {priorityLabel(idea.priority_score)} ({idea.priority_score})
        </span>
        <span>{idea.age_days === 0 ? 'today' : `${idea.age_days} d old`}</span>
        {idea.created_by_name && <span>by {idea.created_by_name}</span>}
      </div>
      <h3 className="text-[13.5px] font-semibold text-fg">
        <UntrustedText>{idea.title}</UntrustedText>
      </h3>
      {idea.problem && (
        <div>
          <p className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Problem</p>
          <UntrustedText as="p" className="text-[13px] text-fg-2">{idea.problem}</UntrustedText>
        </div>
      )}
      {idea.proposal && (
        <div>
          <p className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Proposal</p>
          <UntrustedText as="p" className="text-[13px] text-fg-2">{idea.proposal}</UntrustedText>
        </div>
      )}
      {evidenceCount(idea.evidence) > 0 && (
        <div className="space-y-1">
          <p className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Evidence</p>
          <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-fg-2">
            {idea.evidence.item_ids.length > 0 && <li>{idea.evidence.item_ids.length} radar {idea.evidence.item_ids.length === 1 ? 'item' : 'items'}</li>}
            {idea.evidence.artifact_ids.map((id) => (
              <li key={id}>
                <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'artifact', id })}>
                  An artifact
                </button>
              </li>
            ))}
            {idea.evidence.task_ids.map((id) => (
              <li key={id}>
                <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id })}>
                  A task
                </button>
              </li>
            ))}
            {idea.evidence.urls.map((u) => (
              <li key={u} className="max-w-full truncate">
                <ExternalLink href={u} showHost>
                  Link
                </ExternalLink>
              </li>
            ))}
          </ul>
        </div>
      )}
      {became && (
        <p className="text-[12.5px] text-fg-2">
          Became:{' '}
          {idea.task_id && (
            <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: idea.task_id! })}>
              <UntrustedText>{idea.task_title || 'the task'}</UntrustedText>
            </button>
          )}
          {idea.task_status && <span className="text-fg-3"> ({idea.task_status.replace(/_/g, ' ')})</span>}
          {idea.project_name && <span> in project <UntrustedText>{idea.project_name}</UntrustedText></span>}
          {idea.goal_id && (
            <>
              {' '}
              <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'goals' })}>
                goal{idea.goal_title ? `: ${idea.goal_title}` : ''}
              </button>
            </>
          )}
        </p>
      )}
      {idea.status === 'merged' && idea.merged_into_id && <p className="text-[12.5px] text-fg-3">Merged into another idea.</p>}
      {idea.decided_at && (
        <p className="text-[12px] text-fg-3">
          Decided {formatRelative(idea.decided_at)}
          {idea.decided_by_name ? ` by ${idea.decided_by_name}` : idea.decided_by_type === 'human' ? ' by a person' : idea.decided_by_type === 'agent' ? ' by an agent' : ''}
          {idea.decision_note && (
            <>
              : <UntrustedText>{idea.decision_note}</UntrustedText>
            </>
          )}
        </p>
      )}
      {actions.length > 0 && (
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          {actions.includes('reject') && (
            <Button size="sm" variant="ghost" onClick={() => setDialog('reject')}>
              Reject
            </Button>
          )}
          {actions.includes('accept') && (
            <Button size="sm" variant="primary" onClick={() => setDialog('accept')}>
              Accept
            </Button>
          )}
          {actions.includes('reopen') && (
            <Button
              size="sm"
              loading={decide.isPending}
              onClick={() =>
                decide.mutate(
                  { id: idea.id, action: 'reopen' },
                  { onSuccess: () => toast.success('Idea reopened'), onError: (err) => toast.error(errorMessage(err, 'Could not reopen the idea')) },
                )
              }
            >
              Reopen
            </Button>
          )}
        </div>
      )}
      {dialog === 'accept' && <AcceptDialog companyId={companyId} idea={idea} onClose={() => setDialog(null)} />}
      {dialog === 'reject' && <RejectDialog companyId={companyId} idea={idea} onClose={() => setDialog(null)} />}
    </li>
  )
}

function AcceptDialog({ companyId, idea, onClose }: { companyId: string; idea: RadarIdea; onClose: () => void }) {
  const projects = useProjects(companyId)
  const decide = useDecideRadarIdea(companyId)
  const toast = useToast()
  const open = (projects.data ?? []).filter((p) => p.status === 'planned' || p.status === 'active')
  const [projectId, setProjectId] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const chosen = projectId || open[0]?.id || ''

  async function submit() {
    setError(null)
    try {
      await decide.mutateAsync({ id: idea.id, action: 'accept', project_id: chosen, note: note.trim() || undefined })
      toast.success('Idea accepted: a task was created')
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : errorMessage(err, 'Could not accept the idea'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !decide.isPending && onClose()}
      title="Accept this idea"
      description="A work task is created in the project you choose, with the idea and its evidence as the description."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={decide.isPending}>
            Cancel
          </Button>
          <Button variant="primary" loading={decide.isPending} disabled={!chosen} onClick={() => void submit()}>
            Accept and create the task
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <UntrustedText as="p" className="text-[13px] font-medium text-fg">{idea.title}</UntrustedText>
        {projects.isPending ? (
          <p className="text-[13px] text-fg-3">Loading projects...</p>
        ) : projects.isError ? (
          <FormError message={errorMessage(projects.error, 'Could not load projects')} />
        ) : open.length === 0 ? (
          <p className="text-[13px] text-fg-2">There is no active project to put the work in. Create a project first, then accept the idea.</p>
        ) : (
          <Field label="Project">
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={chosen} onChange={(e) => setProjectId(e.target.value)}>
                {open.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Field label="Note (optional)">{({ id }) => <Textarea id={id} rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}

function RejectDialog({ companyId, idea, onClose }: { companyId: string; idea: RadarIdea; onClose: () => void }) {
  const decide = useDecideRadarIdea(companyId)
  const toast = useToast()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setError(null)
    try {
      await decide.mutateAsync({ id: idea.id, action: 'reject', note: note.trim() || undefined })
      toast.success('Idea rejected')
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'Could not reject the idea'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !decide.isPending && onClose()}
      title="Reject this idea"
      description="It leaves the open backlog. You can reopen it later."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={decide.isPending}>
            Cancel
          </Button>
          <Button variant="danger" loading={decide.isPending} onClick={() => void submit()}>
            Reject idea
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <UntrustedText as="p" className="text-[13px] font-medium text-fg">{idea.title}</UntrustedText>
        <Field label="Why (optional)">{({ id }) => <Textarea id={id} rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}
