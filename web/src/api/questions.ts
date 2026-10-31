import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch } from './client'
import { qk } from './keys'
import type { components } from './schema'
import type { Page } from './types'

/**
 * Founder questions (M18 A2): an agent asks the founder something and keeps working on its default. They appear in the Command Center
 * chat. Types are generated from the OpenAPI schema.
 *
 *   GET  /companies/{cid}/questions[?status=open]        Page<FounderQuestion>   (newest first)
 *   POST /companies/{cid}/questions/{qid}/answer {answer}
 *   SSE  question.created / question.updated
 */
/** `question` is written by an AI employee: shown as plain text, never as markup. */
export type FounderQuestion = components['schemas']['QuestionOut']
export type QuestionStatus = FounderQuestion['status']

/** The backend does not have the questions API (404/405), or this person may not read questions (403): the chat shows none, silently. */
export const questionsUnavailable = (err: unknown): boolean => err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 403)

const co = (cid: string | undefined) => `/companies/${cid}`
const LIMIT = 50

/** The recent questions, open and resolved (resolved ones become one-line receipts). */
export function useQuestions(cid: string | undefined) {
  return useQuery({
    queryKey: qk.questions(cid ?? ''),
    enabled: !!cid,
    retry: (n, e) => !questionsUnavailable(e) && n < 2,
    queryFn: ({ signal }) => apiFetch<Page<FounderQuestion>>(`${co(cid)}/questions`, { query: { limit: LIMIT }, signal }),
    select: (page) => page.items,
  })
}

/** Answer with one of the options, the default, or free text. The question turns into a receipt once the server confirms. */
export function useAnswerQuestion(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, answer }: { id: string; answer: string }) => apiFetch<FounderQuestion | null>(`${co(cid)}/questions/${id}/answer`, { method: 'POST', body: { answer } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.questions(cid) })
      void qc.invalidateQueries({ queryKey: qk.tasksAll(cid) })
    },
  })
}
