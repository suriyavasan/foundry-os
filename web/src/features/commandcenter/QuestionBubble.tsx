import clsx from 'clsx'
import { CornerDownLeft, MessageCircleQuestion } from 'lucide-react'
import { useId, useState } from 'react'
import { errorMessage, useAnswerQuestion, type FounderQuestion } from '@/api'
import type { Agent } from '@/api/types'
import { AgentAvatar, Avatar } from '@/components/avatar'
import { Button } from '@/components/ui'
import { LeftMessage, replyBubbleClass } from './ChatBubbles'

const MAX_ANSWER = 500

/**
 * A question an agent asked the Founder (M18). It sits in the chat as a bubble from the agent that asked, with its options, the
 * default it will go with if nobody answers, and a free-text reply. Once resolved it shrinks to a one-line receipt. The question
 * and options were written by an AI employee, so they are plain text here, never markup.
 */
export function QuestionBubble({ question: q, agent, companyId, canAnswer, showAuthor, tight }: { question: FounderQuestion; agent: Agent | undefined; companyId: string; canAnswer: boolean; showAuthor: boolean; tight: boolean }) {
  const name = agent?.name ?? 'An employee'
  if (q.status !== 'open') return <Receipt question={q} name={name} />
  return (
    <li id={`question-${q.id}`} data-testid="question" data-status={q.status} className={clsx('cos-msg-in', tight ? 'mt-1' : 'mt-3 first:mt-0')}>
      <LeftMessage name={name} showAuthor={showAuthor} face={agent ? <AgentAvatar agent={agent} size="sm" /> : <Avatar family="generic" size="sm" seed={q.agent_id} />}>
        <div className={clsx(replyBubbleClass, 'space-y-2')}>
          <p className="flex items-start gap-1.5">
            <MessageCircleQuestion className="mt-0.5 size-3.5 shrink-0 text-accent-fg" aria-hidden />
            <span className="min-w-0 break-words whitespace-pre-wrap">
              <span className="sr-only">{name} asks: </span>
              {q.question}
            </span>
          </p>
          {q.blocking && <p className="text-[12px] text-warning">This is holding up a task until you answer.</p>}
          {canAnswer ? <Answer question={q} companyId={companyId} /> : <p className="text-[12px] text-fg-3">You can read this question. Viewers cannot answer.</p>}
        </div>
      </LeftMessage>
    </li>
  )
}

function Answer({ question: q, companyId }: { question: FounderQuestion; companyId: string }) {
  const answer = useAnswerQuestion(companyId)
  const [replying, setReplying] = useState(false)
  const [text, setText] = useState('')
  const inputId = useId()
  const busy = answer.isPending
  const send = (a: string) => answer.mutate({ id: q.id, answer: a })
  const chip = 'inline-flex max-w-full items-center rounded-full border border-line-strong bg-surface px-2.5 py-1 text-[12.5px] text-fg transition-colors hover:bg-hover disabled:opacity-60'
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {q.options.map((o) => (
          <button key={o} type="button" disabled={busy} onClick={() => send(o)} className={chip}>
            <span className="truncate">{o}</span>
          </button>
        ))}
        <button type="button" disabled={busy} onClick={() => send(q.default_answer)} className={clsx(chip, 'border-accent/50 text-accent-fg')}>
          <span className="truncate">Use default ({q.default_answer})</span>
        </button>
        {!replying && (
          <button type="button" disabled={busy} onClick={() => setReplying(true)} className={clsx(chip, 'border-transparent text-fg-2')}>
            Reply…
          </button>
        )}
      </div>
      {replying && (
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            const a = text.trim()
            if (a) send(a)
          }}
        >
          <label htmlFor={inputId} className="sr-only">
            Your answer
          </label>
          <input
            id={inputId}
            autoFocus
            value={text}
            maxLength={MAX_ANSWER}
            onChange={(e) => setText(e.target.value)}
            placeholder="Your answer"
            className="h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-canvas px-2.5 text-[13px] text-fg outline-none placeholder:text-fg-3 focus:border-accent"
          />
          <Button type="submit" size="sm" variant="primary" loading={busy} disabled={!text.trim()} icon={<CornerDownLeft className="size-3.5" />}>
            Answer
          </Button>
        </form>
      )}
      {answer.isError && (
        <p role="alert" className="text-[12px] text-danger">
          {errorMessage(answer.error, 'Could not send the answer.')}
        </p>
      )}
    </div>
  )
}

/** A resolved question, in one line: who asked, what, and how it ended. */
function Receipt({ question: q, name }: { question: FounderQuestion; name: string }) {
  const how = q.status === 'answered' ? `You answered: ${q.answer ?? ''}` : q.status === 'defaulted' ? `Went with the default: ${q.answer ?? q.default_answer}` : 'Cancelled'
  return (
    <li data-testid="question" data-status={q.status} className="mt-2 flex justify-center">
      <p className="max-w-full truncate rounded-full bg-hover/60 px-3 py-0.5 text-[11.5px] text-fg-3" title={`${name} asked: ${q.question}`}>
        {name} asked “{q.question}” · {how}
      </p>
    </li>
  )
}
