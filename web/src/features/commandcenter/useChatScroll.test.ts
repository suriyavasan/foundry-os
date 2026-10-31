import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useChatScroll } from './useChatScroll'

/** A scroll container with fake metrics (jsdom does no layout). */
function box(m: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  const el = document.createElement('div')
  for (const k of ['scrollHeight', 'clientHeight'] as const) Object.defineProperty(el, k, { configurable: true, get: () => m[k] })
  Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => m.scrollTop, set: (v: number) => (m.scrollTop = v) })
  return { el, m }
}

type Props = Parameters<typeof useChatScroll>[0]

function setup(props: Props, m: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  const { el } = box(m)
  const hook = renderHook((p: Props) => useChatScroll(p), { initialProps: props })
  ;(hook.result.current.ref as { current: HTMLDivElement | null }).current = el
  const scrollTo = (top: number) => {
    m.scrollTop = top
    act(() => hook.result.current.onScroll())
  }
  return { ...hook, scrollTo }
}

describe('useChatScroll', () => {
  it('follows new messages to the bottom while the reader is at the bottom', () => {
    const m = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 }
    const h = setup({ firstKey: 'a', lastKey: 'b' }, m)
    m.scrollHeight = 1100
    h.rerender({ firstKey: 'a', lastKey: 'c' })
    expect(m.scrollTop).toBe(1100)
    expect(h.result.current.showNewPill).toBe(false)
  })

  it('does not pull a reader who scrolled up; it shows a "New messages" pill until they come back', () => {
    const m = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 }
    const h = setup({ firstKey: 'a', lastKey: 'b' }, m)
    h.scrollTo(200) // read older messages
    expect(h.result.current.showNewPill).toBe(false)
    m.scrollHeight = 1100
    h.rerender({ firstKey: 'a', lastKey: 'c' })
    expect(m.scrollTop).toBe(200)
    expect(h.result.current.showNewPill).toBe(true)
    act(() => h.result.current.toBottom())
    expect(m.scrollTop).toBe(1100)
    expect(h.result.current.showNewPill).toBe(false)
  })

  it('hides the pill when the reader scrolls back down by hand', () => {
    const m = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 }
    const h = setup({ firstKey: 'a', lastKey: 'b' }, m)
    h.scrollTo(100)
    h.rerender({ firstKey: 'a', lastKey: 'c' })
    expect(h.result.current.showNewPill).toBe(true)
    h.scrollTo(790)
    expect(h.result.current.showNewPill).toBe(false)
  })

  it('keeps the reading position when older messages are added above', () => {
    const m = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 }
    const h = setup({ firstKey: 'm', lastKey: 'z' }, m)
    h.scrollTo(10)
    m.scrollHeight = 1600 // 600 px of older messages arrived
    h.rerender({ firstKey: 'a', lastKey: 'z' })
    expect(m.scrollTop).toBe(610)
    expect(h.result.current.showNewPill).toBe(false)
  })

  it('asks for older messages when the reader reaches the top (and only then)', () => {
    const onReachTop = vi.fn()
    const m = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 }
    const h = setup({ firstKey: 'a', lastKey: 'b', onReachTop }, m)
    h.scrollTo(300)
    expect(onReachTop).not.toHaveBeenCalled()
    h.scrollTo(4)
    expect(onReachTop).toHaveBeenCalledTimes(1)
  })

  it('does not ask for more when everything fits without scrolling', () => {
    const onReachTop = vi.fn()
    const m = { scrollHeight: 200, clientHeight: 300, scrollTop: 0 }
    const h = setup({ firstKey: 'a', lastKey: 'b', onReachTop }, m)
    h.scrollTo(0)
    expect(onReachTop).not.toHaveBeenCalled()
  })
})
