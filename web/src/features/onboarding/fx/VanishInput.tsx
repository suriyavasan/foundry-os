import clsx from 'clsx'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState, type ComponentProps } from 'react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * A text field whose placeholder cycles through example ideas, and whose text lifts off and dissolves when `vanishing`
 * turns true (the moment the idea is sent). Own implementation of the "placeholders and vanish input" idea with ordinary DOM:
 * the real <input> stays in place (labels, autofill and tests work as normal); while vanishing, a copy of the text is
 * split into letters that float up and fade. Reduced motion: the placeholder does not cycle (the first one is shown) and
 * the text simply clears.
 */
export function VanishInput({
  placeholders,
  vanishing = false,
  className,
  value,
  ...rest
}: Omit<ComponentProps<'input'>, 'placeholder'> & { placeholders: string[]; vanishing?: boolean; value: string }) {
  const reduced = usePrefersReducedMotion()
  const [i, setI] = useState(0)
  const empty = value.length === 0

  useEffect(() => {
    if (reduced || !empty || placeholders.length < 2) return
    const id = setInterval(() => setI((n) => (n + 1) % placeholders.length), 3200)
    return () => clearInterval(id)
  }, [reduced, empty, placeholders.length])

  const letters = Array.from(value)
  const showLetters = vanishing && !reduced && letters.length > 0
  return (
    <div className="relative">
      <input
        value={value}
        className={clsx(
          'h-12 w-full rounded-lg border border-line-strong bg-raised px-4 text-[15px] text-fg transition-colors outline-none placeholder:text-transparent',
          'hover:border-fg-3 focus:border-accent focus:ring-1 focus:ring-accent',
          showLetters && 'text-transparent',
          className,
        )}
        placeholder={placeholders[0] ?? ''}
        {...rest}
      />
      {empty && (
        <div aria-hidden className="pointer-events-none absolute inset-y-0 left-4 flex items-center overflow-hidden text-[15px] text-fg-3">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={reduced ? 0 : i}
              initial={reduced ? false : { y: 14, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={reduced ? { opacity: 0 } : { y: -14, opacity: 0 }}
              transition={{ duration: reduced ? 0 : 0.28, ease: 'easeOut' }}
              className="block max-w-full truncate"
            >
              {placeholders[reduced ? 0 : i]}
            </motion.span>
          </AnimatePresence>
        </div>
      )}
      {showLetters && (
        <div aria-hidden data-fx="vanish" className="pointer-events-none absolute inset-y-0 left-4 flex items-center overflow-hidden text-[15px] whitespace-pre text-fg">
          {letters.map((ch, n) => (
            <motion.span
              key={n}
              className="inline-block"
              initial={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              animate={{ opacity: 0, y: -18 - (n % 5) * 3, x: (n % 3) * 2 - 2, filter: 'blur(6px)' }}
              transition={{ duration: 0.7, delay: Math.min(n * 0.012, 0.5), ease: 'easeIn' }}
            >
              {ch}
            </motion.span>
          ))}
        </div>
      )}
    </div>
  )
}
