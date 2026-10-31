import clsx from 'clsx'
import type { CSSProperties } from 'react'
import type { Agent } from '@/api/types'
import { avatarBgVar, useFaceSrc, type FaceKind } from '@/lib/faces'
import { ringStyleFor } from '@/lib/liveState'
import { initials } from '@/lib/people'
import { ROLE_FAMILY_LABEL, inferRoleFamily } from '@/lib/roleFamily'
import { Glyph, type GlyphKey } from './glyphs'

export const AVATAR_SIZES = { xs: 20, sm: 26, md: 34, lg: 44, xl: 56 } as const
export type AvatarSize = keyof typeof AVATAR_SIZES

/** A named size, or an exact diameter in px (the organization map sizes circles by role). */
export const avatarPx = (size: AvatarSize | number): number => (typeof size === 'number' ? size : AVATAR_SIZES[size])

/** Sizes from which the employee-type glyph is drawn as a badge on a face. */
const BADGE_MIN_PX = 34

/** The employee-type glyph as a small badge at the bottom-right of a face. */
function RoleBadge({ glyph, px }: { glyph: GlyphKey; px: number }) {
  const badge = Math.max(14, Math.round(px * 0.36))
  return (
    <span className="cos-avatar-role" style={{ width: badge, height: badge }}>
      <Glyph name={glyph} size={Math.round(badge * 0.66)} />
    </span>
  )
}

/**
 * Presentational avatar: a circle with the employee-type glyph. `ring` is the live state (see lib/liveState). With a `seed` the
 * circle shows a locally generated illustrated face instead (stable per seed, on a token-coloured background) and the glyph
 * moves to a small badge, so the employee type stays visible. Decorative by default (`aria-hidden`); parents supply the
 * accessible name.
 */
export function Avatar({
  family,
  size = 'md',
  ring,
  className,
  title,
  seed,
  face,
}: {
  family: GlyphKey
  size?: AvatarSize | number
  ring?: { token: string; dashed: boolean; pulse: boolean; dimmed: boolean }
  className?: string
  title?: string
  /** Id of the person: gives them a stable illustrated face. Without it the circle shows the type glyph alone. */
  seed?: string
  /** Which art style; defaults to the human style for the Founder and the agent style for everyone else. */
  face?: FaceKind
}) {
  const px = avatarPx(size)
  const wanted: FaceKind | null = seed ? (face ?? (family === 'human' ? 'human' : 'agent')) : null
  const src = useFaceSrc(wanted, seed)
  // Until the artwork has loaded the circle shows the type glyph, as it does for an avatar with no seed.
  const kind = src ? wanted : null
  const style: CSSProperties = { width: px, height: px }
  if (ring) (style as Record<string, string>)['--ring'] = `var(${ring.token})`
  if (kind && seed) (style as Record<string, string>)['--face-bg'] = avatarBgVar(seed)
  return (
    <span
      aria-hidden
      title={title}
      data-family={family}
      data-face={kind ?? undefined}
      data-dashed={ring?.dashed || undefined}
      data-dimmed={ring?.dimmed || undefined}
      data-pulse={ring?.pulse || undefined}
      className={clsx('cos-avatar', className)}
      style={style}
    >
      {kind && src ? (
        <>
          <img className="cos-avatar-face" src={src} alt="" draggable={false} />
          {px >= BADGE_MIN_PX && <RoleBadge glyph={family === 'human' ? 'founder' : family} px={px} />}
        </>
      ) : (
        <Glyph name={family} size={Math.round(px * 0.54)} />
      )}
    </span>
  )
}

/** Avatar for an agent: type from `role_family`/inference, ring from lifecycle + live state. */
export function AgentAvatar({ agent, size = 'md', className, titled = false }: { agent: Agent; size?: AvatarSize | number; className?: string; titled?: boolean }) {
  const family = inferRoleFamily(agent)
  const ring = ringStyleFor(agent)
  return <Avatar family={family} size={size} ring={ring} className={className} seed={agent.id} title={titled ? `${ROLE_FAMILY_LABEL[family]} · ${ring.label}` : undefined} />
}

/**
 * Avatar of a human member (M7): an illustrated face in the human style (stable per `seed`, the member id; the name when none is
 * given), with a double ring and a small human glyph, so a person is never mistaken for an agent. Decorative; the parent
 * supplies the accessible name.
 */
export function MemberAvatar({ name, seed, size = 'md', className, title }: { name: string; seed?: string; size?: AvatarSize | number; className?: string; title?: string }) {
  const px = avatarPx(size)
  const key = seed ?? name
  const src = useFaceSrc('human', key)
  const style = { width: px, height: px, ...(src ? { '--face-bg': avatarBgVar(key) } : { fontSize: Math.max(8, Math.round(px * 0.36)) }) } as CSSProperties
  return (
    <span aria-hidden title={title} data-family="member" data-face={src ? 'human' : undefined} className={clsx('cos-avatar', className)} style={style}>
      {src ? <img className="cos-avatar-face" src={src} alt="" draggable={false} /> : <span className="leading-none font-semibold tracking-tight">{initials(name)}</span>}
      {px >= 26 && (
        <span className="cos-avatar-role" style={{ width: Math.max(12, Math.round(px * 0.34)), height: Math.max(12, Math.round(px * 0.34)), color: 'var(--av-human)' }}>
          <Glyph name="human" size={Math.max(8, Math.round(px * 0.22))} />
        </span>
      )}
    </span>
  )
}
