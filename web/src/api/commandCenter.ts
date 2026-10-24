import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { commandChangesState, isActiveExchange, optimisticExchange } from '@/lib/commandCenter'
import { apiFetch } from './client'
import { qk } from './keys'
import type { CommandExchange, CommandRequest, Page } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const PAGE = 30

type Feed = InfiniteData<Page<CommandExchange>, string | null>

/**
 * The merged feed of commands and free-text exchanges, newest first, paginated by cursor. While anything is still
 * unanswered it is re-read every few seconds as a fallback for a missing event stream (`command.updated` normally
 * refreshes it at once).
 */
export function useCommandFeed(cid: string | undefined) {
  return useInfiniteQuery({
    queryKey: qk.commandFeed(cid ?? ''),
    enabled: !!cid,
    initialPageParam: null as string | null,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
    queryFn: ({ pageParam, signal }) => apiFetch<Page<CommandExchange>>(`${co(cid)}/command-center/feed`, { query: { cursor: pageParam, limit: PAGE }, signal }),
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: (q) => (q.state.data?.pages.some((p) => p.items.some(isActiveExchange)) ? 3000 : false),
  })
}

export const exchangesOf = (data: { pages: Array<Page<CommandExchange>> } | undefined): CommandExchange[] => data?.pages.flatMap((p) => p.items) ?? []

/** Apply `f` to the newest page. With nothing cached yet (a send before the first load finished) a page is started. */
function mapFirstPage(old: Feed | undefined, f: (items: CommandExchange[]) => CommandExchange[]): Feed {
  if (!old || old.pages.length === 0) return { pages: [{ items: f([]), next_cursor: null }], pageParams: [null] }
  const [first, ...rest] = old.pages
  return { ...old, pages: [{ ...first!, items: f(first!.items) }, ...rest] }
}

/**
 * Send an input. The row appears at once (optimistic, status pending/processing); the server's exchange replaces it. A
 * refusal removes the row and rejects, and the caller shows the reason as a failed local exchange.
 */
export function useSendCommand(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: string) => apiFetch<CommandExchange>(`${co(cid)}/command`, { method: 'POST', body: { input } satisfies CommandRequest }),
    onMutate: async (input) => {
      await qc.cancelQueries({ queryKey: qk.commandFeed(cid) })
      const tmp = optimisticExchange(input)
      qc.setQueryData<Feed>(qk.commandFeed(cid), (old) => mapFirstPage(old, (items) => [tmp, ...items]))
      return { tmpId: tmp.id }
    },
    onError: (_e, _input, ctx) => {
      if (ctx) qc.setQueryData<Feed>(qk.commandFeed(cid), (old) => mapFirstPage(old, (items) => items.filter((x) => x.id !== ctx.tmpId)))
      void qc.invalidateQueries({ queryKey: qk.commandFeed(cid) })
    },
    onSuccess: (real, input, ctx) => {
      qc.setQueryData<Feed>(qk.commandFeed(cid), (old) =>
        mapFirstPage(old, (items) => (items.some((x) => x.id === ctx?.tmpId) ? items.map((x) => (x.id === ctx?.tmpId ? real : x)) : [real, ...items.filter((x) => x.id !== real.id)])),
      )
      void qc.invalidateQueries({ queryKey: qk.commandFeed(cid) })
      if (commandChangesState(input)) {
        for (const key of [qk.companyDetail(cid), qk.companies, qk.tasksAll(cid), qk.approvalsAll(cid), qk.agents(cid), qk.overview(cid), qk.activityAll(cid), qk.goals(cid), qk.projects(cid)]) void qc.invalidateQueries({ queryKey: key })
      }
      if (real.input_kind === 'note') {
        void qc.invalidateQueries({ queryKey: qk.notesAll(cid) })
        void qc.invalidateQueries({ queryKey: qk.cofounderSummary(cid) })
      }
    },
  })
}

/** Ask the AI Co-founder to answer an exchange again (after a failure, or one that waited too long). */
export function useRetryExchange(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch<CommandExchange>(`${co(cid)}/command-center/${id}/retry`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.commandFeed(cid) }),
  })
}
