import { motion } from 'motion/react'
import { usePrefersReducedMotion } from '@/lib/motion'

/**
 * Words fade in one after another, sharpening out of a blur ("text generate" reveal; own implementation). The whole text is
 * always in the DOM (and in the accessible name), so assistive tech and tests read it at once. Reduced motion shows it
 * as plain text.
 */
export function TextGenerate({
  text,
  as: Tag = 'h1',
  className,
  stagger = 0.07,
  delay = 0.15,
}: {
  text: string
  as?: 'h1' | 'h2' | 'p'
  className?: string
  stagger?: number
  delay?: number
}) {
  const reduced = usePrefersReducedMotion()
  const words = text.split(/\s+/).filter(Boolean)
  return (
    <Tag className={className} aria-label={text}>
      {reduced
        ? text
        : words.map((w, i) => (
            <motion.span
              key={`${w}-${i}`}
              aria-hidden
              className="inline-block whitespace-pre"
              initial={{ opacity: 0, filter: 'blur(8px)', y: 6 }}
              animate={{ opacity: 1, filter: 'blur(0px)', y: 0 }}
              transition={{ duration: 0.55, delay: delay + i * stagger, ease: 'easeOut' }}
            >
              {w}
              {i < words.length - 1 ? ' ' : ''}
            </motion.span>
          ))}
    </Tag>
  )
}
