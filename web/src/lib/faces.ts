import { useEffect, useSyncExternalStore } from 'react'

/**
 * Illustrated faces (docs/specs/m20 §4). Generated locally with DiceBear: no network and no image service. Both art styles are CC0
 * (lorelei by Lisa Wischofsky, notionists by Zoish) and the library is MIT; see web/THIRD_PARTY.md. The same seed always gives the
 * same face, and the picture has a transparent background: the circle behind it is one of the `--avatar-bg-*` design tokens.
 *
 * The artwork is large, so it is a separate chunk fetched the first time a face is wanted. Until it arrives (a few milliseconds
 * from the local server) the plain type-glyph avatar is drawn, and each face then replaces it in place.
 */
export type FaceKind = 'agent' | 'human'

export const AVATAR_BG_COUNT = 8

type Generator = (kind: FaceKind, seed: string) => string

let generate: Generator | null = null
let loading: Promise<void> | null = null
let version = 0
const listeners = new Set<() => void>()

/** Fetch the face generator (once). Resolves when faces can be drawn. */
export function loadFaces(): Promise<void> {
  loading ??= import('./facesImpl').then((m) => {
    generate = m.faceSvg
    version++
    listeners.forEach((l) => l())
  })
  return loading
}

const cache = new Map<string, string>()

/**
 * `data:` URI of the face for `seed`, memoized per (kind, seed); null until the generator has loaded. Safe in an `<img src>`; never
 * injected as markup.
 */
export function faceSrc(kind: FaceKind, seed: string): string | null {
  const key = `${kind}\u0000${seed}`
  const hit = cache.get(key)
  if (hit) return hit
  if (!generate) return null
  const svg = generate(kind, seed)
  // The embedded licence metadata is ~1 kB of XML per face; the artwork is CC0 and credited in THIRD_PARTY.md.
  const slim = svg.replace(/<metadata[\s\S]*?<\/metadata>/, '')
  const src = `data:image/svg+xml;utf8,${encodeURIComponent(slim)}`
  cache.set(key, src)
  return src
}

const subscribe = (l: () => void): (() => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
const snapshot = (): number => version

/** The face for `seed`, re-rendering once the generator arrives; null while it is still loading. */
export function useFaceSrc(kind: FaceKind | null, seed: string | undefined): string | null {
  useSyncExternalStore(subscribe, snapshot)
  useEffect(() => {
    if (kind) void loadFaces()
  }, [kind])
  return kind && seed ? faceSrc(kind, seed) : null
}

/** Stable 32-bit FNV-1a hash of a string. */
export function hashSeed(seed: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** 1..8: which `--avatar-bg-N` token colours the circle behind this seed's face. */
export function avatarBgIndex(seed: string): number {
  return (hashSeed(seed) % AVATAR_BG_COUNT) + 1
}

export const avatarBgVar = (seed: string): string => `var(--avatar-bg-${avatarBgIndex(seed)})`
