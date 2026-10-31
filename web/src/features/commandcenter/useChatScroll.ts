import { useCallback, useLayoutEffect, useRef, useState } from 'react'

/** How close to the bottom (px) still counts as "at the bottom". */
export const BOTTOM_SLACK = 48
/** How close to the top (px) asks for older messages. */
export const TOP_SLACK = 24

export const distanceFromBottom = (el: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>): number => el.scrollHeight - el.scrollTop - el.clientHeight

/**
 * The scroll behaviour of a chat: stay at the bottom as messages arrive, unless the reader scrolled up (then say so with a
 * "New messages" pill instead of yanking them down); keep the reading position when older messages are added above; and ask for
 * older messages when the reader reaches the top. `firstKey`/`lastKey` identify the oldest and newest entry.
 */
export function useChatScroll({ firstKey, lastKey, onReachTop }: { firstKey: string | undefined; lastKey: string | undefined; onReachTop?: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const stuck = useRef(true)
  const lastKeyRef = useRef(lastKey)
  const prev = useRef<{ first: string | undefined; last: string | undefined; height: number }>({ first: undefined, last: undefined, height: 0 })
  const [view, setView] = useState<{ atBottom: boolean; seen: string | undefined }>({ atBottom: true, seen: undefined })

  useLayoutEffect(() => {
    lastKeyRef.current = lastKey
    const el = ref.current
    if (!el) return
    const p = prev.current
    if (p.first !== undefined && firstKey !== p.first && lastKey === p.last) {
      // Older messages were added above: keep what the reader was looking at where it was.
      el.scrollTop += el.scrollHeight - p.height
    } else if (stuck.current) {
      el.scrollTop = el.scrollHeight
    }
    prev.current = { first: firstKey, last: lastKey, height: el.scrollHeight }
  })

  const onScroll = useCallback(() => {
    const el = ref.current
    if (!el) return
    const near = distanceFromBottom(el) <= BOTTOM_SLACK
    stuck.current = near
    setView((s) => (near ? (s.atBottom && s.seen === lastKeyRef.current ? s : { atBottom: true, seen: lastKeyRef.current }) : s.atBottom ? { atBottom: false, seen: lastKeyRef.current } : s))
    if (el.scrollTop <= TOP_SLACK && el.scrollHeight > el.clientHeight) onReachTop?.()
  }, [onReachTop])

  const toBottom = useCallback(() => {
    const el = ref.current
    if (!el) return
    stuck.current = true
    el.scrollTop = el.scrollHeight
    setView({ atBottom: true, seen: lastKeyRef.current })
  }, [])

  return { ref, onScroll, toBottom, showNewPill: !view.atBottom && lastKey !== view.seen }
}
