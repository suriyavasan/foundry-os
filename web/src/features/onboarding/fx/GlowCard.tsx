import clsx from 'clsx'
import { useRef, type ComponentProps, type PointerEvent } from 'react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * A selectable card (a real button, role radio) whose border lights up under the pointer and that lifts a hair on hover
 * (own implementation of the "card hover" idea). The glow follows the cursor through two CSS variables, no re-render per
 * move. Reduced motion: no glow tracking and no lift, only the border colour change.
 */
export function GlowCard({
  selected = false,
  className,
  children,
  onPointerMove,
  ...rest
}: Omit<ComponentProps<'button'>, 'role'> & { selected?: boolean }) {
  const reduced = usePrefersReducedMotion()
  const ref = useRef<HTMLButtonElement>(null)

  function move(e: PointerEvent<HTMLButtonElement>) {
    onPointerMove?.(e)
    if (reduced) return
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--gx', `${e.clientX - r.left}px`)
    el.style.setProperty('--gy', `${e.clientY - r.top}px`)
  }

  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={selected}
      data-selected={selected || undefined}
      onPointerMove={move}
      className={clsx(
        'group relative overflow-hidden rounded-lg border bg-surface p-4 text-left transition-[border-color,transform,background-color] duration-200',
        selected ? 'border-accent bg-accent-subtle' : 'border-line-strong hover:border-fg-3',
        !reduced && 'hover:-translate-y-0.5',
        'disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
      {...rest}
    >
      {!reduced && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
          style={{ background: 'radial-gradient(180px circle at var(--gx, 50%) var(--gy, 0%), color-mix(in srgb, var(--accent) 22%, transparent), transparent 70%)' }}
        />
      )}
      <span className="relative block">{children}</span>
    </button>
  )
}
