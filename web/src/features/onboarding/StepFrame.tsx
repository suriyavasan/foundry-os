import clsx from 'clsx'
import { ArrowLeft } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Wordmark } from '@/features/auth/AuthShell'
import { STEP_LABEL, progressText, stepIndex, stepsFor, type FlowState, type StepId } from '@/lib/onboarding'
import { usePrefersReducedMotion } from '@/lib/motion'
import { BackgroundBeams } from './fx/BackgroundBeams'
import { Spotlight } from './fx/Spotlight'

/**
 * The full-screen frame every onboarding step sits in: dark, quiet, one decision on the screen. The progress strip and the
 * back arrow are the only chrome. Decoration (spotlight, beams) is aria-hidden and optional per step.
 */
export function StepFrame({
  flow,
  step,
  onBack,
  fx,
  wide = false,
  children,
}: {
  flow: Pick<FlowState, 'mode' | 'step'>
  step: StepId
  onBack?: () => void
  fx?: 'spotlight' | 'beams' | 'both' | 'none'
  wide?: boolean
  children: ReactNode
}) {
  const reduced = usePrefersReducedMotion()
  const steps = stepsFor(flow.mode)
  const at = stepIndex(flow)
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-canvas" data-step={step}>
      {(fx === 'spotlight' || fx === 'both') && <Spotlight />}
      {(fx === 'beams' || fx === 'both') && <BackgroundBeams />}

      <header className="relative z-10 flex items-center justify-between gap-4 px-5 py-4 sm:px-8">
        <div className="flex items-center gap-3">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              aria-label="Back"
              className="inline-flex size-8 items-center justify-center rounded-md text-fg-2 transition-colors hover:bg-hover hover:text-fg"
            >
              <ArrowLeft className="size-4" aria-hidden />
            </button>
          ) : (
            <span className="size-8" aria-hidden />
          )}
          <Wordmark />
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <ol aria-label="Setup progress" className="flex gap-1.5">
            {steps.map((s, i) => (
              <li key={s} aria-current={i === at ? 'step' : undefined} className={clsx('h-1 w-6 rounded-full transition-colors duration-300 sm:w-9', i <= at ? 'bg-accent' : 'bg-active')}>
                <span className="sr-only">
                  {STEP_LABEL[s]}
                  {i < at ? ' (done)' : i === at ? ' (current)' : ''}
                </span>
              </li>
            ))}
          </ol>
          <p className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">{progressText(flow)}</p>
        </div>
      </header>

      <main className="relative z-10 flex flex-1 items-center justify-center px-5 pb-14 sm:px-8">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={step}
            className={clsx('w-full', wide ? 'max-w-3xl' : 'max-w-lg')}
            initial={reduced ? false : { opacity: 0, y: 14, filter: 'blur(4px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -10, filter: 'blur(4px)' }}
            transition={{ duration: reduced ? 0 : 0.28, ease: 'easeOut' }}
          >
            {children}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}

/** Heading block used by the steps: a small eyebrow, the title, one line of explanation. */
export function StepHeading({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <div className="mb-6">
      {eyebrow && <p className="mb-2 text-[11.5px] font-medium tracking-[0.14em] text-accent-fg uppercase">{eyebrow}</p>}
      <h1 className="text-[26px] leading-8 font-semibold tracking-tight text-fg">{title}</h1>
      {children && <p className="mt-2 text-[14px] leading-relaxed text-fg-2">{children}</p>}
    </div>
  )
}
