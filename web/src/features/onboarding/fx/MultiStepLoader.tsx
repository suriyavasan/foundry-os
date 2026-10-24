import clsx from 'clsx'
import { Check, Circle, Loader2, Minus, X } from 'lucide-react'
import { motion } from 'motion/react'
import type { LoaderStep, LoaderStepState } from '@/lib/aiSetup'
import { usePrefersReducedMotion } from '@/lib/motion'

const STATE_TEXT: Record<LoaderStepState, string> = {
  pending: 'waiting',
  running: 'checking',
  passed: 'passed',
  failed: 'failed',
  unchecked: 'not checked',
}

function StateIcon({ state, reduced }: { state: LoaderStepState; reduced: boolean }) {
  switch (state) {
    case 'passed':
      return (
        <span className="flex size-5 items-center justify-center rounded-full bg-success-subtle text-success">
          <Check className="size-3" aria-hidden />
        </span>
      )
    case 'failed':
      return (
        <span className="flex size-5 items-center justify-center rounded-full bg-danger-subtle text-danger">
          <X className="size-3" aria-hidden />
        </span>
      )
    case 'running':
      return (
        <span className="flex size-5 items-center justify-center text-accent-fg">
          <Loader2 className={clsx('size-4', !reduced && 'cos-spin')} aria-hidden />
        </span>
      )
    case 'unchecked':
      return (
        <span className="flex size-5 items-center justify-center rounded-full bg-neutral-subtle text-fg-3">
          <Minus className="size-3" aria-hidden />
        </span>
      )
    default:
      return (
        <span className="flex size-5 items-center justify-center text-fg-3">
          <Circle className="size-2.5" aria-hidden />
        </span>
      )
  }
}

/**
 * The multi-step loader: one row per check, driven entirely by the rows it is given. It has no timers and invents no
 * progress: while the request is in flight the first row spins and the rest wait; when the result is in, every row shows its
 * real outcome with the server's detail. Rows ease in one after another (CSS delay only, so all of it is in the DOM at once).
 * Reduced motion: no easing, no spinner rotation.
 */
export function MultiStepLoader({ steps, label = 'Connection test' }: { steps: LoaderStep[]; label?: string }) {
  const reduced = usePrefersReducedMotion()
  const done = steps.every((s) => s.state === 'passed' || s.state === 'failed' || s.state === 'unchecked')
  const failed = steps.some((s) => s.state === 'failed')
  return (
    <div>
      <ol aria-label={label} className="space-y-1.5">
        {steps.map((s, i) => (
          <motion.li
            key={s.name}
            aria-label={`${s.label}: ${STATE_TEXT[s.state]}`}
            data-state={s.state}
            className={clsx('flex items-start gap-2.5 rounded-md px-2.5 py-1.5 text-[13px]', s.state === 'failed' ? 'bg-danger-subtle' : s.state === 'running' ? 'bg-accent-subtle' : 'bg-raised')}
            initial={reduced || !done ? false : { opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={reduced ? { duration: 0 } : { duration: 0.28, delay: done ? i * 0.12 : 0 }}
          >
            <StateIcon state={s.state} reduced={reduced} />
            <div className="min-w-0 flex-1">
              <p className={clsx('font-medium', s.state === 'pending' || s.state === 'unchecked' ? 'text-fg-3' : 'text-fg')}>{s.label}</p>
              {s.detail && <p className={clsx('mt-0.5 text-[12px] break-words', s.state === 'failed' ? 'text-danger' : 'text-fg-3')}>{s.detail}</p>}
              {s.state === 'unchecked' && <p className="mt-0.5 text-[12px] text-fg-3">The server did not run this check.</p>}
            </div>
          </motion.li>
        ))}
      </ol>
      <p role="status" aria-live="polite" className="sr-only">
        {!done ? 'Testing the connection' : failed ? 'The test found a problem' : 'The test passed'}
      </p>
    </div>
  )
}
