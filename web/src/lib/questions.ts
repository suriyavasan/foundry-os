import type { FounderQuestion } from '@/api/questions'

/** The open question that has waited longest (the one to answer first), or null. */
export function oldestOpenQuestion<Q extends Pick<FounderQuestion, 'status' | 'created_at'>>(questions: readonly Q[]): Q | null {
  let oldest: Q | null = null
  for (const q of questions) if (q.status === 'open' && (!oldest || q.created_at < oldest.created_at)) oldest = q
  return oldest
}
