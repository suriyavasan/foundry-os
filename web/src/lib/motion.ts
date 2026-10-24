import { useEffect, useState } from 'react'

/**
 * `prefers-reduced-motion`, read live. Every animated effect in the onboarding (and the Command Center's working state)
 * renders a still, complete version when this is true: same content, no movement. jsdom has no `matchMedia`; that is "no
 * preference", i.e. motion allowed, unless a test installs one.
 */
const QUERY = '(prefers-reduced-motion: reduce)'

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(QUERY).matches
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(QUERY)
    const on = () => setReduced(mq.matches)
    on()
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])
  return reduced
}
