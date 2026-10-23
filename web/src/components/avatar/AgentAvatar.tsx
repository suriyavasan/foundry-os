import clsx from 'clsx'
import type { CSSProperties } from 'react'
import type { Agent } from '@/api/types'
import { ringStyleFor } from '@/lib/liveState'
import { ROLE_FAMILY_LABEL, inferRoleFamily } from '@/lib/roleFamily'
import { Glyph, type GlyphKey } from './glyphs'

export const AVATAR_SIZES = { xs: 20, sm: 26, md: 34, lg: 44, xl: 56 } as const
export type AvatarSize = keyof typeof AVATAR_SIZES

/**
 * Presentational avatar: a circle with the employee-type glyph. `ring` is the live state
 * (see lib/liveState). Decorative by default (`aria-hidden`); parents supply the accessible name.
 */
export function Avatar({
  family,
  size = 'md',
  ring,
  className,
  title,
}: {
  family: GlyphKey
  size?: AvatarSize
  ring?: { token: string; dashed: boolean; pulse: boolean; dimmed: boolean }
  className?: string
  title?: string
}) {
  const px = AVATAR_SIZES[size]
  return (
    <span
      aria-hidden
      title={title}
      data-family={family}
      data-dashed={ring?.dashed || undefined}
      data-dimmed={ring?.dimmed || undefined}
      data-pulse={ring?.pulse || undefined}
      className={clsx('cos-avatar', className)}
      style={{ width: px, height: px, ...(ring ? ({ '--ring': `var(${ring.token})` } as CSSProperties) : null) }}
    >
      <Glyph name={family} size={Math.round(px * 0.54)} />
    </span>
  )
}

/** Avatar for an agent: type from `role_family`/inference, ring from lifecycle + live state. */
export function AgentAvatar({ agent, size = 'md', className, titled = false }: { agent: Agent; size?: AvatarSize; className?: string; titled?: boolean }) {
  const family = inferRoleFamily(agent)
  const ring = ringStyleFor(agent)
  return <Avatar family={family} size={size} ring={ring} className={className} title={titled ? `${ROLE_FAMILY_LABEL[family]} · ${ring.label}` : undefined} />
}
