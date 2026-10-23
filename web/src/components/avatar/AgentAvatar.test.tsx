import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { normalizeLive } from '@/lib/liveState'
import { ROLE_FAMILIES } from '@/lib/roleFamily'
import { makeAgent } from '@/test/factories'
import { AgentAvatar } from './AgentAvatar'
import { GLYPH_KEYS, Glyph } from './glyphs'

describe('avatar glyph set', () => {
  it('has a glyph for every employee type plus the human co-founder', () => {
    for (const f of ROLE_FAMILIES) expect(GLYPH_KEYS).toContain(f)
    expect(GLYPH_KEYS).toContain('human')
    expect(GLYPH_KEYS).toHaveLength(ROLE_FAMILIES.length + 1)
  })

  it('draws every glyph with real geometry and no two the same', () => {
    const markup = new Set<string>()
    for (const k of GLYPH_KEYS) {
      const { container, unmount } = render(<Glyph name={k} />)
      const svg = container.querySelector('svg')!
      expect(svg.querySelectorAll('path,circle,rect').length).toBeGreaterThan(0)
      expect(svg).toHaveAttribute('aria-hidden', 'true')
      markup.add(svg.innerHTML)
      unmount()
    }
    expect(markup.size).toBe(GLYPH_KEYS.length)
  })
})

describe('AgentAvatar', () => {
  it('encodes type in data-family and live state in the ring style', () => {
    const working = makeAgent({ role_title: 'Backend Engineer', live: normalizeLive({ state: 'working' }) })
    const { container } = render(<AgentAvatar agent={working} />)
    const el = container.querySelector('.cos-avatar') as HTMLElement
    expect(el.dataset.family).toBe('engineering')
    expect(el.dataset.pulse).toBe('true')
    expect(el.style.getPropertyValue('--ring')).toBe('var(--live-working)')
  })

  it('dashes a proposed agent and dims a paused one', () => {
    const { container: a } = render(<AgentAvatar agent={makeAgent({ status: 'proposed' })} />)
    expect((a.querySelector('.cos-avatar') as HTMLElement).dataset.dashed).toBe('true')
    const { container: b } = render(<AgentAvatar agent={makeAgent({ status: 'paused' })} />)
    expect((b.querySelector('.cos-avatar') as HTMLElement).dataset.dimmed).toBe('true')
  })

  it('prefers the backend role_family over inference', () => {
    const { container } = render(<AgentAvatar agent={makeAgent({ role_title: 'Backend Engineer', role_family: 'design' })} />)
    expect((container.querySelector('.cos-avatar') as HTMLElement).dataset.family).toBe('design')
  })
})
