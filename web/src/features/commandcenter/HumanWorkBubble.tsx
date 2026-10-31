import clsx from 'clsx'
import { HandHelping, UserRound } from 'lucide-react'
import { ApiError, errorMessage, useClaimHumanWork } from '@/api'
import type { Agent, HumanWorkItem } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { Button, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canTakeHumanWork, categoryLabel, dueText, holderText } from '@/lib/humanWork'
import { LeftMessage, replyBubbleClass } from './ChatBubbles'

/**
 * An AI employee needs a person for something (M22): it sits in the chat as a bubble from that employee ("needs a person: sign the
 * NDA") with "I'll do it" (take it from the pool) and "Open" (People ops). The title and instructions were written by an AI employee,
 * so they are plain text. Only open requests are drawn; a finished one simply disappears.
 */
export function HumanWorkBubble({ item: i, agent, companyId, role, showAuthor, tight }: { item: HumanWorkItem; agent: Agent | undefined; companyId: string; role: string | undefined; showAuthor: boolean; tight: boolean }) {
  const { openPanel } = useCanvas()
  const claim = useClaimHumanWork(companyId)
  const toast = useToast()
  const name = agent?.name ?? i.requested_by_agent_name ?? 'An employee'
  const due = dueText(i.due_at)
  return (
    <li id={`human-work-${i.id}`} data-testid="human-work" data-status={i.status} className={clsx('cos-msg-in', tight ? 'mt-1' : 'mt-3 first:mt-0')}>
      <LeftMessage name={name} showAuthor={showAuthor} face={agent ? <AgentAvatar agent={agent} size="sm" /> : <Avatar family="generic" size="sm" seed={i.requested_by_agent_id ?? i.id} />}>
        <div className={clsx(replyBubbleClass, 'space-y-2')}>
          <p className="flex items-start gap-1.5">
            <UserRound className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
            <span className="min-w-0 break-words whitespace-pre-wrap">
              <span className="font-medium">{name} needs a person:</span> {i.title}
            </span>
          </p>
          <p className="text-[12px] text-fg-3">
            {categoryLabel(i.category)} · {holderText(i)}
            {due ? ` · ${due.text}` : ''}
          </p>
          {i.blocking_task_ids.length > 0 && <p className="text-[12px] text-warning">This is holding up a task until it is done.</p>}
          <div className="flex flex-wrap gap-1.5">
            {i.pool && canTakeHumanWork(role) && (
              <Button
                size="sm"
                variant="primary"
                icon={<HandHelping className="size-3.5" />}
                loading={claim.isPending}
                onClick={() =>
                  claim.mutate(i.id, {
                    onSuccess: () => toast.success('It is yours now'),
                    onError: (err) => toast.error(err instanceof ApiError && err.status === 409 ? 'Somebody already took it' : errorMessage(err, 'Could not take it')),
                  })
                }
              >
                I&apos;ll do it
              </Button>
            )}
            <Button size="sm" onClick={() => openPanel({ kind: 'people-ops' })}>
              Open
            </Button>
          </div>
        </div>
      </LeftMessage>
    </li>
  )
}
