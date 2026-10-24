import { useCallback, useMemo, useState } from 'react'
import { errorMessage, exchangesOf, useCommandFeed, useRetryExchange, useSendCommand } from '@/api'
import type { CommandExchange } from '@/api/types'
import { localFailure, isLocalExchange, type LocalExchange } from '@/lib/commandCenter'

/**
 * Everything the Command Center widget and panel share: the feed (newest first, paginated), sending an input, and retrying an
 * exchange. A send that never reached the server leaves a failed local exchange, so even a refused input visibly gets an output.
 */
export function useCommandCenter(companyId: string) {
  const feed = useCommandFeed(companyId)
  const send = useSendCommand(companyId)
  const retry = useRetryExchange(companyId)
  const [local, setLocal] = useState<LocalExchange[]>([])
  const [retryingId, setRetryingId] = useState<string | null>(null)

  const exchanges: CommandExchange[] = useMemo(() => {
    const server = exchangesOf(feed.data)
    return [...local, ...server].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
  }, [feed.data, local])

  const submit = useCallback(
    (input: string) => {
      send.mutate(input, { onError: (e) => setLocal((l) => [localFailure(input, errorMessage(e)), ...l]) })
    },
    [send],
  )

  const retryExchange = useCallback(
    (ex: CommandExchange) => {
      if (isLocalExchange(ex)) {
        setLocal((l) => l.filter((x) => x.id !== ex.id))
        submit(ex.input)
        return
      }
      setRetryingId(ex.id)
      retry.mutate(ex.id, {
        onSettled: () => setRetryingId(null),
        onError: (e) => setLocal((l) => [{ ...localFailure(ex.input, `Could not retry: ${errorMessage(e)}`) }, ...l]),
      })
    },
    [retry, submit],
  )

  return { feed, exchanges, submit, retryExchange, retryingId, sending: send.isPending }
}
