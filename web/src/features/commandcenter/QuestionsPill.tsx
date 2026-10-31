import { MessageCircleQuestion } from 'lucide-react'
import { useQuestions } from '@/api'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { oldestOpenQuestion } from '@/lib/questions'

const HIGHLIGHT_MS = 1800

/** Scroll the bubble of a question into view and mark it for a moment. Returns false when it is not on screen. */
export function focusQuestion(id: string): boolean {
  const el = document.getElementById(`question-${id}`)
  if (!el) return false
  el.scrollIntoView({ block: 'center', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  el.setAttribute('data-highlight', 'true')
  window.setTimeout(() => el.removeAttribute('data-highlight'), HIGHLIGHT_MS)
  return true
}

/**
 * "2 questions" in the Command Center header while agents are waiting for an answer. A click takes you to the oldest open one in
 * the conversation (the full Command Center when this chat is the small widget and the question is not in view).
 */
export function QuestionsPill({ companyId, inPanel = false }: { companyId: string; inPanel?: boolean }) {
  const { openPanel } = useCanvas()
  const questions = useQuestions(companyId)
  const open = (questions.data ?? []).filter((q) => q.status === 'open')
  const oldest = oldestOpenQuestion(questions.data ?? [])
  if (!oldest) return null
  return (
    <button
      type="button"
      onClick={() => {
        if (!focusQuestion(oldest.id) && !inPanel) openPanel({ kind: 'command-center' })
      }}
      className="inline-flex h-6 items-center gap-1 rounded-full bg-accent-subtle px-2 text-[11.5px] font-medium text-accent-fg transition-colors hover:bg-hover"
      title="Jump to the oldest open question"
    >
      <MessageCircleQuestion className="size-3.5" aria-hidden />
      {open.length} {open.length === 1 ? 'question' : 'questions'}
    </button>
  )
}
