import { motion } from 'motion/react'
import { useMemo } from 'react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * Thin curved beams of light drifting across the background (own implementation of the "background beams" idea:
 * a fan of SVG curves, each with a short bright dash travelling along it). Deterministic geometry, no randomness at render,
 * so it never flickers on re-render. Reduced motion draws the curves, still and faint.
 */
const COUNT = 14

function pathFor(i: number): string {
  const t = i / (COUNT - 1)
  const startX = -80 + t * 560
  const endX = 420 + t * 1100
  const c1 = 120 + t * 220
  const c2 = 620 - t * 180
  return `M${startX} -60 C ${c1} 180, ${c2} 360, ${endX} 760`
}

export function BackgroundBeams({ className }: { className?: string }) {
  const reduced = usePrefersReducedMotion()
  const paths = useMemo(() => Array.from({ length: COUNT }, (_, i) => ({ d: pathFor(i), delay: (i % 7) * 0.9, duration: 7 + (i % 5) * 1.3 })), [])
  return (
    <div aria-hidden data-fx="beams" data-reduced={reduced || undefined} className={'pointer-events-none absolute inset-0 overflow-hidden ' + (className ?? '')}>
      <svg className="absolute inset-0 size-full" viewBox="0 0 1440 700" preserveAspectRatio="xMidYMid slice" fill="none">
        <defs>
          <linearGradient id="cos-beam" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0" />
            <stop offset="50%" stopColor="var(--accent)" stopOpacity="0.9" />
            <stop offset="100%" stopColor="var(--accent-fg)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {paths.map((p, i) => (
          <g key={i}>
            <path d={p.d} stroke="var(--line-strong)" strokeOpacity={reduced ? 0.35 : 0.22} strokeWidth="0.6" />
            {!reduced && (
              <motion.path
                d={p.d}
                stroke="url(#cos-beam)"
                strokeWidth="1.2"
                strokeLinecap="round"
                pathLength={1}
                strokeDasharray="0.08 1"
                initial={{ strokeDashoffset: 0.08, opacity: 0 }}
                animate={{ strokeDashoffset: -1, opacity: [0, 1, 1, 0] }}
                transition={{ duration: p.duration, delay: p.delay, repeat: Infinity, ease: 'linear' }}
              />
            )}
          </g>
        ))}
      </svg>
    </div>
  )
}
