import { createAvatar } from '@dicebear/core'
import * as lorelei from '@dicebear/lorelei'
import * as notionists from '@dicebear/notionists'
import type { FaceKind } from './faces'

/**
 * The only module that imports DiceBear: it is loaded on demand (see `loadFaces`), so ~500 kB of artwork definitions stay out of
 * the entry chunk. Output is the SVG of one face with a transparent background.
 */
export function faceSvg(kind: FaceKind, seed: string): string {
  return (kind === 'agent' ? createAvatar(lorelei, { seed }) : createAvatar(notionists, { seed })).toString()
}
