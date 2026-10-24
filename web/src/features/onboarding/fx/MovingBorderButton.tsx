import clsx from 'clsx'
import { motion } from 'motion/react'
import type { ComponentProps, ReactNode } from 'react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * The call-to-action with a light travelling around its border (own implementation of the "moving border" idea: a rotating
 * conic gradient clipped to a 1.5px ring behind an opaque button face). The ring only moves while the button is enabled and
 * motion is allowed; otherwise it is a still accent border. The element is a real <button>.
 */
export function MovingBorderButton({ className, children, disabled, icon, ...rest }: ComponentProps<'button'> & { icon?: ReactNode }) {
  const reduced = usePrefersReducedMotion()
  const moving = !reduced && !disabled
  return (
    <span className={clsx('relative inline-flex overflow-hidden rounded-lg p-[1.5px]', disabled && 'opacity-60')} data-moving={moving || undefined}>
      <span aria-hidden className="absolute inset-0 bg-line-strong" />
      {moving ? (
        <motion.span
          aria-hidden
          className="absolute top-1/2 left-1/2 aspect-square w-[240%] -translate-x-1/2 -translate-y-1/2"
          style={{ background: 'conic-gradient(from 0deg, transparent 0 62%, var(--accent) 80%, var(--accent-fg) 90%, transparent 100%)' }}
          animate={{ rotate: 360 }}
          transition={{ duration: 3.2, repeat: Infinity, ease: 'linear' }}
        />
      ) : (
        <span aria-hidden className="absolute inset-0 opacity-70" style={{ background: disabled ? 'transparent' : 'linear-gradient(135deg, var(--accent), var(--accent-fg))' }} />
      )}
      <button
        type="button"
        disabled={disabled}
        className={clsx(
          'relative inline-flex h-10 items-center justify-center gap-2 rounded-[calc(var(--radius-lg,0.5rem)-1px)] bg-surface px-5 text-[14px] font-medium text-fg transition-colors',
          'hover:bg-raised disabled:cursor-not-allowed',
          className,
        )}
        {...rest}
      >
        {icon}
        {children}
      </button>
    </span>
  )
}
