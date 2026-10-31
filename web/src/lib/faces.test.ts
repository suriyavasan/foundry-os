/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { AVATAR_BG_COUNT, avatarBgIndex, avatarBgVar, faceSrc as maybeFaceSrc, hashSeed, loadFaces } from './faces'

const tokens = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8')

const faceSrc = (kind: 'agent' | 'human', seed: string): string => maybeFaceSrc(kind, seed)!

beforeAll(() => loadFaces())

describe('illustrated faces', () => {
  it('gives the same seed the same picture, and different seeds different ones', () => {
    expect(faceSrc('agent', 'agent-1')).toBe(faceSrc('agent', 'agent-1'))
    expect(faceSrc('agent', 'agent-1')).not.toBe(faceSrc('agent', 'agent-2'))
    // agents and humans are drawn in different styles
    expect(faceSrc('agent', 'same')).not.toBe(faceSrc('human', 'same'))
  })

  it('is a data: image URI, built locally (no network), and has no embedded licence XML or script', () => {
    const src = faceSrc('human', 'u-1')
    expect(src.startsWith('data:image/svg+xml;utf8,')).toBe(true)
    const svg = decodeURIComponent(src.slice('data:image/svg+xml;utf8,'.length))
    expect(svg.startsWith('<svg')).toBe(true)
    expect(svg).not.toContain('<metadata')
    expect(svg.toLowerCase()).not.toContain('<script')
  })

  it('picks one of eight background tokens by a hash of the id, stably', () => {
    const seen = new Set<number>()
    for (let i = 0; i < 200; i++) {
      const n = avatarBgIndex(`agent-${i}`)
      expect(n).toBeGreaterThanOrEqual(1)
      expect(n).toBeLessThanOrEqual(AVATAR_BG_COUNT)
      expect(avatarBgIndex(`agent-${i}`)).toBe(n)
      seen.add(n)
    }
    expect(seen.size).toBe(AVATAR_BG_COUNT)
    expect(avatarBgVar('x')).toBe(`var(--avatar-bg-${avatarBgIndex('x')})`)
    expect(hashSeed('x')).toBe(hashSeed('x'))
  })

  it('defines all eight background tokens for both themes', () => {
    const dark = tokens.slice(0, tokens.indexOf(":root[data-theme='light']"))
    const light = tokens.slice(tokens.indexOf(":root[data-theme='light']"))
    for (let i = 1; i <= AVATAR_BG_COUNT; i++) {
      expect(dark).toMatch(new RegExp(`--avatar-bg-${i}: #[0-9a-f]{6};`))
      expect(light).toMatch(new RegExp(`--avatar-bg-${i}: #[0-9a-f]{6};`))
    }
  })
})

describe('loading the artwork on demand', () => {
  it('has no face until the generator has loaded, then draws it (the entry chunk stays small)', async () => {
    vi.resetModules()
    const fresh = await import('./faces')
    expect(fresh.faceSrc('agent', 'a')).toBeNull()
    await fresh.loadFaces()
    expect(fresh.faceSrc('agent', 'a')).toMatch(/^data:image\/svg\+xml;utf8,/)
    // asking again does not fetch again
    await expect(fresh.loadFaces()).resolves.toBeUndefined()
  })
})
