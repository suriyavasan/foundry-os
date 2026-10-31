import { MessageSquareOff, Users, X } from 'lucide-react'
import { Fragment, useState } from 'react'
import { ApiError, errorMessage, isNotDeployed, useCancelMeeting, useMeeting } from '@/api'
import type { Agent, MeetingDetail, MeetingMessage } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { Badge, Button, Card, CardBody, CardHeader, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, Markdown, NotAvailable, Textarea, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { canEditPolicy, useAgentMap, useCompanyRole } from '@/features/canvas/hooks'
import { LeftMessage, replyBubbleClass } from '@/features/commandcenter/ChatBubbles'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatDateTime, formatRelative } from '@/lib/format'
import { MEETING_STATUS_LABEL, MEETING_STATUS_TONE, actionItemNote, isMeetingOpen, isPassMessage, orderedParticipants, readOutcome, readProposals, roundLabel } from '@/lib/meetings'

/**
 * `?panel=meeting:<id>`: one meeting between AI employees. The agenda, who is in it, the transcript as a chat (each employee's
 * face and name), and how it ended. Everything said was written by an AI employee, so it is rendered as markdown with no raw HTML.
 */
export function MeetingPanel({ meetingId }: { meetingId: string }) {
  const { companyId } = useCanvas()
  const q = useMeeting(companyId, meetingId)
  const { map } = useAgentMap(companyId)
  const role = useCompanyRole(companyId)
  const [cancelling, setCancelling] = useState(false)

  if (q.isPending) {
    return (
      <PanelFrame title="Meeting">
        <Card>
          <LoadingState rows={6} label="Loading meeting" />
        </Card>
      </PanelFrame>
    )
  }
  if (q.isError) {
    return (
      <PanelFrame title="Meeting">
        <Card>
          {isNotDeployed(q.error) ? (
            <NotAvailable milestone="M21" title="This meeting is not available" description={q.error instanceof ApiError && q.error.status === 404 ? 'It does not exist in this company, or this server has no agent meetings yet.' : 'This server has no agent meetings yet.'} />
          ) : (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )}
        </Card>
      </PanelFrame>
    )
  }

  const m = q.data
  const canCancel = canEditPolicy(role) && isMeetingOpen(m)
  return (
    <PanelFrame
      title={m.title}
      subtitle={
        <span className="inline-flex items-center gap-2">
          <Badge tone={MEETING_STATUS_TONE[m.status]}>{MEETING_STATUS_LABEL[m.status]}</Badge>
          {m.status === 'running' && roundLabel(m)}
        </span>
      }
      actions={
        canCancel && (
          <Button size="sm" variant="danger" icon={<X className="size-3.5" />} onClick={() => setCancelling(true)}>
            Cancel meeting
          </Button>
        )
      }
    >
      <div className="space-y-4">
        <Card>
          <CardHeader title="Agenda" />
          <CardBody className="space-y-3">
            <div className="text-[13px] text-fg">
              <Markdown source={m.agenda} />
            </div>
            <p className="text-[12.5px] text-fg-2">
              <span className="font-medium text-fg">Expected outcome:</span> {m.expected_outcome}
            </p>
            <Participants meeting={m} agents={map} />
          </CardBody>
        </Card>
        <Transcript meeting={m} agents={map} />
        <Outcome meeting={m} agents={map} />
      </div>
      {cancelling && <CancelDialog companyId={companyId} meeting={m} onClose={() => setCancelling(false)} />}
    </PanelFrame>
  )
}

function Participants({ meeting: m, agents }: { meeting: MeetingDetail; agents: Map<string, Agent> }) {
  return (
    <div>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">
        <Users className="size-3.5" aria-hidden /> Participants
      </h3>
      <ul aria-label="Participants" className="flex flex-wrap gap-x-4 gap-y-2">
        {orderedParticipants(m.participants).map((p) => (
          <li key={p.agent_id} className="flex items-center gap-2 text-[13px]">
            <Face agentId={p.agent_id} name={p.name} agents={agents} />
            <span className="min-w-0">
              <span className="font-medium text-fg">{p.name}</span>
              {p.role === 'organizer' && <span className="ml-1.5 text-[11.5px] text-fg-3">organizer</span>}
              {p.role_title && <span className="block text-[11.5px] text-fg-3">{p.role_title}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Face({ agentId, name, agents }: { agentId: string; name: string; agents: Map<string, Agent> }) {
  const agent = agents.get(agentId)
  return agent ? <AgentAvatar agent={agent} size="sm" /> : <Avatar family="generic" size="sm" seed={agentId} title={name} />
}

function Transcript({ meeting: m, agents }: { meeting: MeetingDetail; agents: Map<string, Agent> }) {
  const messages = m.messages
  return (
    <Card>
      <CardHeader title="Transcript" description={messages.length > 0 ? `${messages.length} message${messages.length === 1 ? '' : 's'}` : undefined} />
      {messages.length === 0 ? (
        <EmptyState
          compact
          icon={MessageSquareOff}
          title="Nobody has spoken yet"
          description={m.status === 'scheduled' ? `The meeting starts ${formatRelative(m.start_at)}.` : m.status === 'running' ? 'The first turn is being prepared.' : 'Nothing was said before this meeting ended.'}
        />
      ) : (
        <ol aria-label="Transcript" className="flex flex-col px-3.5 py-3">
          {messages.map((msg, i) => {
            const prev = messages[i - 1]
            const newRound = !prev || prev.round !== msg.round
            const showAuthor = newRound || prev.agent_id !== msg.agent_id || isPassMessage(prev.proposals)
            return (
              <Fragment key={msg.id}>
                {newRound && (
                  <li role="separator" aria-label={`Round ${msg.round}`} className="my-3 flex items-center gap-3 text-[11px] font-medium text-fg-3 first:mt-0">
                    <span aria-hidden className="h-px flex-1 bg-line" />
                    Round {msg.round}
                    <span aria-hidden className="h-px flex-1 bg-line" />
                  </li>
                )}
                <MessageRow message={msg} agents={agents} showAuthor={showAuthor} />
              </Fragment>
            )
          })}
        </ol>
      )}
    </Card>
  )
}

function MessageRow({ message: msg, agents, showAuthor }: { message: MeetingMessage; agents: Map<string, Agent>; showAuthor: boolean }) {
  const name = msg.agent_name
  // A turn that produced nothing (failed, timed out, passed) is a quiet one-line receipt, not a bubble.
  if (isPassMessage(msg.proposals)) {
    return (
      <li data-testid="meeting-pass" className="mt-2 flex justify-center">
        <p className="max-w-full truncate rounded-full bg-hover/60 px-3 py-0.5 text-[11.5px] text-fg-3" title={msg.body}>
          {name} passed{msg.body && !msg.body.startsWith('[no contribution') ? '' : ` · ${msg.body.replace(/^\[no contribution:\s*|\]$/g, '')}`}
        </p>
      </li>
    )
  }
  const props = readProposals(msg.proposals)
  const wrapUp = msg.kind === 'synthesis'
  return (
    <li data-testid="meeting-message" data-kind={msg.kind} className="mt-1 first:mt-0">
      <LeftMessage name={name} showAuthor={showAuthor} face={<Face agentId={msg.agent_id} name={name} agents={agents} />}>
        <div className={replyBubbleClass}>
          {wrapUp && <p className="mb-1 text-[11px] font-medium tracking-wide text-fg-3 uppercase">Wrap-up</p>}
          <Markdown source={msg.body} />
          {(props.proposed.length > 0 || props.accepted > 0 || props.declined > 0) && (
            <ul className="mt-1.5 space-y-0.5 border-t border-line pt-1.5 text-[12px] text-fg-2">
              {props.proposed.map((p) => (
                <li key={p.id}>
                  Proposes: {p.title}
                  {p.ownerAgentId && agents.get(p.ownerAgentId) ? ` (${agents.get(p.ownerAgentId)!.name})` : ''}
                </li>
              ))}
              {props.accepted > 0 && <li>Accepted {props.accepted === 1 ? 'an item' : `${props.accepted} items`}</li>}
              {props.declined > 0 && <li>Declined {props.declined === 1 ? 'an item' : `${props.declined} items`}</li>}
            </ul>
          )}
        </div>
        <time dateTime={msg.created_at} title={formatDateTime(msg.created_at)} className="mt-0.5 block pl-1 text-[11px] text-fg-3">
          {formatRelative(msg.created_at)}
        </time>
      </LeftMessage>
    </li>
  )
}

function Outcome({ meeting: m, agents }: { meeting: MeetingDetail; agents: Map<string, Agent> }) {
  const { openPanel } = useCanvas()
  const outcome = readOutcome(m.outcome)
  const ended = m.status === 'concluded'
  return (
    <Card>
      <CardHeader title="Outcome" />
      <CardBody className="space-y-4">
        {m.status === 'cancelled' || m.status === 'failed' ? (
          <p role="status" className="text-[13px] text-fg-2">
            {m.status === 'cancelled' ? 'This meeting was cancelled' : 'This meeting failed'}
            {m.cancel_reason ? `: ${m.cancel_reason}` : '.'}
          </p>
        ) : !ended ? (
          <p className="text-[13px] text-fg-3">The summary, decisions and action items appear when the meeting ends.</p>
        ) : (
          <>
            {m.summary && (
              <div className="text-[13px] text-fg">
                <Markdown source={m.summary} />
              </div>
            )}
            <section aria-label="Decisions">
              <h3 className="mb-1.5 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Decisions</h3>
              {outcome.decisions.length === 0 ? (
                <p className="text-[13px] text-fg-3">No decisions were recorded.</p>
              ) : (
                <ul className="space-y-1.5">
                  {outcome.decisions.map((d) => (
                    <li key={d.title} className="text-[13px] text-fg">
                      <span className="font-medium">{d.title}</span>
                      {d.rationale && <span className="block text-[12.5px] text-fg-2">{d.rationale}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section aria-label="Action items">
              <h3 className="mb-1.5 text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Action items</h3>
              {outcome.actionItems.length === 0 ? (
                <p className="text-[13px] text-fg-3">No action items.</p>
              ) : (
                <ul className="divide-y divide-line rounded-md border border-line">
                  {outcome.actionItems.map((it) => (
                    <li key={it.id} data-state={it.state} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-[13px]">
                      <span className="min-w-0 flex-1 break-words text-fg">{it.title}</span>
                      <AgentChip agentId={it.ownerAgentId} agents={agents} emptyLabel="No owner" className="text-[12.5px] text-fg-2" />
                      <Badge tone={it.state === 'task' || it.state === 'agreed' ? 'success' : it.state === 'refused' ? 'danger' : 'neutral'}>{actionItemNote(it)}</Badge>
                      {it.taskId && (
                        <Button size="sm" variant="ghost" onClick={() => openPanel({ kind: 'task', id: it.taskId! })}>
                          Open task
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </CardBody>
    </Card>
  )
}

function CancelDialog({ companyId, meeting, onClose }: { companyId: string; meeting: MeetingDetail; onClose: () => void }) {
  const cancel = useCancelMeeting(companyId)
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Cancel this meeting?"
      description={
        <div className="space-y-3">
          <p>
            The turn in progress is stopped and nothing is agreed or created. Nothing already said is deleted.
          </p>
          <Field label="Reason (optional)">{({ id }) => <Textarea id={id} rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
        </div>
      }
      confirmLabel="Cancel meeting"
      loading={cancel.isPending}
      error={error}
      onConfirm={async () => {
        setError(null)
        try {
          await cancel.mutateAsync({ id: meeting.id, reason: reason.trim() })
          toast.success('Meeting cancelled')
          onClose()
        } catch (e) {
          setError(errorMessage(e))
        }
      }}
    />
  )
}
