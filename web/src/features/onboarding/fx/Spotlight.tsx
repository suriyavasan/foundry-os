import { motion } from 'motion/react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * A soft cone of accent light that sweeps in from the top-left and settles (in the spirit of the "spotlight" effect seen
 * in modern landing pages; written from scratch). Pure decoration: `aria-hidden`, no pointer events. Reduced motion renders
 * the settled state without the sweep.
 */
export function Spotlight({ className }: { className?: string }) {
  const reduced = usePrefersReducedMotion()
  const style = {
    background: 'radial-gradient(60% 55% at 30% 0%, color-mix(in srgb, var(--accent) 38%, transparent) 0%, color-mix(in srgb, var(--accent) 10%, transparent) 45%, transparent 75%)',
    filter: 'blur(24px)',
  } as const
  return (
    <div aria-hidden data-fx="spotlight" data-reduced={reduced || undefined} className={'pointer-events-none absolute inset-0 overflow-hidden ' + (className ?? '')}>
      {reduced ? (
        <div className="absolute -top-1/4 -left-1/4 h-[90%] w-[80%] opacity-60" style={style} />
      ) : (
        <motion.div
          className="absolute -top-1/4 -left-1/4 h-[90%] w-[80%]"
          style={style}
          initial={{ opacity: 0, x: -140, y: -60, rotate: -8 }}
          animate={{ opacity: 0.75, x: 0, y: 0, rotate: 0 }}
          transition={{ duration: 1.6, ease: [0.16, 1, 0.3, 1] }}
        />
      )}
    </div>
  )
}
