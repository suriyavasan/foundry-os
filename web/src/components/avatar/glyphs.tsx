import type { ReactNode } from 'react'
import type { RoleFamily } from '@/api/types'

/**
 * Custom employee-type glyphs. One consistent system: 24x24 grid, 1.6 stroke, round caps and joins,
 * no fills except small solid accents, drawn in `currentColor` (the avatar sets the colour).
 * Not stock icons: each is a purpose-built mark for the role family.
 */
export type GlyphKey = RoleFamily | 'human'

const G: Record<GlyphKey, ReactNode> = {
  // four-point star: the person who sets direction
  founder: (
    <>
      <path d="M12 3.2 14.1 9.9 20.8 12 14.1 14.1 12 20.8 9.9 14.1 3.2 12 9.9 9.9Z" />
      <circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  // two people
  hr: (
    <>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.8 19.2c0-3.2 2.3-5.1 5.2-5.1s5.2 1.9 5.2 5.1" />
      <circle cx="16.6" cy="9.4" r="2.3" />
      <path d="M16.2 14.3c2.5 0 4.2 1.5 4.2 4.1" />
    </>
  ),
  // code brackets with a slash
  engineering: (
    <>
      <path d="m8 7.5-4.5 4.5L8 16.5" />
      <path d="m16 7.5 4.5 4.5-4.5 4.5" />
      <path d="m13.4 5.5-2.8 13" />
    </>
  ),
  // pen nib
  design: (
    <>
      <path d="M12 20.6 6.4 10.4 12 3.4l5.6 7Z" />
      <path d="M12 13.4v7.2" />
      <circle cx="12" cy="10" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
  // megaphone
  marketing: (
    <>
      <path d="M4 9.8v4.4h3.2L14.5 18V6L7.2 9.8Z" />
      <path d="m7.6 14.4.9 4.2h2.6l-.8-3.2" />
      <path d="M17.4 9.4c1.3 1.4 1.3 3.8 0 5.2" />
      <path d="M19.6 7.4c2.4 2.6 2.4 6.6 0 9.2" />
    </>
  ),
  // rising line with arrowhead
  sales: (
    <>
      <path d="m3.8 17.2 5.4-5.6 3.4 3.4 7.2-8" />
      <path d="M14.6 7h5.2v5.2" />
    </>
  ),
  // speech bubble with a smile
  customer_success: (
    <>
      <path d="M5.2 5.6h13.6a1.6 1.6 0 0 1 1.6 1.6v7.6a1.6 1.6 0 0 1-1.6 1.6h-6.4l-4 3.4v-3.4H5.2a1.6 1.6 0 0 1-1.6-1.6V7.2a1.6 1.6 0 0 1 1.6-1.6Z" />
      <path d="M8.8 11.2c1.7 2.1 4.7 2.1 6.4 0" />
    </>
  ),
  // magnifier with a spark
  research: (
    <>
      <circle cx="10.4" cy="10.4" r="5.8" />
      <path d="m14.8 14.8 5.4 5.4" />
      <path d="M10.4 7.8v5.2M7.8 10.4h5.2" />
    </>
  ),
  // shield with a check
  qa: (
    <>
      <path d="M12 3.2 19 6v5.4c0 4.2-2.9 7.3-7 9.4-4.1-2.1-7-5.2-7-9.4V6Z" />
      <path d="m8.8 12.2 2.3 2.3 4.2-4.5" />
    </>
  ),
  // server rack
  operations: (
    <>
      <rect x="3.8" y="4" width="16.4" height="6.2" rx="1.6" />
      <rect x="3.8" y="13.8" width="16.4" height="6.2" rx="1.6" />
      <circle cx="7.6" cy="7.1" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="7.6" cy="16.9" r="0.9" fill="currentColor" stroke="none" />
      <path d="M11.5 7.1h5.2M11.5 16.9h5.2" />
    </>
  ),
  // coin with an S
  finance: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M12 6.6v10.8" />
      <path d="M14.7 9.6c-.5-1-1.5-1.5-2.7-1.5-1.5 0-2.6.8-2.6 2 0 2.9 5.4 1.5 5.4 4.3 0 1.2-1.2 2.1-2.8 2.1-1.2 0-2.3-.5-2.8-1.6" />
    </>
  ),
  // bust
  generic: (
    <>
      <circle cx="12" cy="8.8" r="3.6" />
      <path d="M4.8 20c0-4 3.1-6.3 7.2-6.3s7.2 2.3 7.2 6.3" />
    </>
  ),
  // the human co-founder: bust with a small star
  human: (
    <>
      <circle cx="11" cy="9" r="3.4" />
      <path d="M4.4 20c0-3.9 2.9-6.1 6.6-6.1 1.1 0 2.1.2 3 .6" />
      <path d="m18.2 12.6.9 2 2.1.2-1.6 1.4.5 2.1-1.9-1.1-1.9 1.1.5-2.1-1.6-1.4 2.1-.2Z" fill="currentColor" stroke="none" />
    </>
  ),
}

export function Glyph({ name, size = 20, className }: { name: GlyphKey; size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
      className={className}
    >
      {G[name]}
    </svg>
  )
}

export const GLYPH_KEYS = Object.keys(G) as GlyphKey[]
