import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { normalizeLive } from '@/lib/liveState'
import { ROLE_FAMILIES } from '@/lib/roleFamily'
import { makeAgent } from '@/test/factories'
import { avatarBgVar } from '@/lib/faces'
import { AgentAvatar, Avatar, MemberAvatar } from './AgentAvatar'
import { GLYPH_KEYS, Glyph } from './glyphs'

describe('avatar glyph set', () => {
  it('has a glyph for every employee type plus the human Founder', () => {
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

describe('illustrated faces', () => {
  const faceOf = (c: HTMLElement) => c.querySelector('img.cos-avatar-face') as HTMLImageElement

  it('gives an agent a stable face from its id: same id, same picture, on a token-coloured circle', () => {
    const agent = makeAgent({ id: 'agent-42', role_title: 'Backend Engineer' })
    const a = render(<AgentAvatar agent={agent} size="lg" />)
    const b = render(<AgentAvatar agent={{ ...agent, name: 'Renamed' }} size="sm" />)
    const first = faceOf(a.container)
    expect(first.getAttribute('src')).toMatch(/^data:image\/svg\+xml;utf8,/)
    expect(first.getAttribute('src')).toBe(faceOf(b.container).getAttribute('src'))
    expect(first).toHaveAttribute('alt', '')
    const circle = a.container.querySelector('.cos-avatar') as HTMLElement
    expect(circle.style.getPropertyValue('--face-bg')).toBe(avatarBgVar('agent-42'))
    // another agent looks different
    const c = render(<AgentAvatar agent={makeAgent({ id: 'agent-43' })} />)
    expect(faceOf(c.container).getAttribute('src')).not.toBe(first.getAttribute('src'))
  })

  it('keeps the live-state ring and the employee type (a glyph badge from md up)', () => {
    const agent = makeAgent({ id: 'a1', role_title: 'Backend Engineer', live: normalizeLive({ state: 'working' }) })
    const { container } = render(<AgentAvatar agent={agent} size="lg" />)
    const el = container.querySelector('.cos-avatar') as HTMLElement
    expect(el.dataset.family).toBe('engineering')
    expect(el.dataset.pulse).toBe('true')
    expect(el.style.getPropertyValue('--ring')).toBe('var(--live-working)')
    expect(el.querySelector('.cos-avatar-role svg')).not.toBeNull()
    const small = render(<AgentAvatar agent={agent} size="xs" />)
    expect(small.container.querySelector('.cos-avatar-role')).toBeNull()
    expect(faceOf(small.container)).not.toBeNull()
  })

  it('draws no markup from the picture: it is an <img>, nothing is injected', () => {
    const { container } = render(<AgentAvatar agent={makeAgent({ id: '<script>x</script>' })} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelectorAll('.cos-avatar > svg').length).toBe(0)
  })

  it('an Avatar without a seed is still the plain glyph, and takes an exact pixel size', () => {
    const { container } = render(<Avatar family="generic" size={30} />)
    expect(container.querySelector('img')).toBeNull()
    expect((container.querySelector('.cos-avatar') as HTMLElement).style.width).toBe('30px')
  })

  it('the Founder gets the human style and a founder glyph badge; a member gets a face too', () => {
    const founder = render(<Avatar family="human" size="xl" seed="u1" />)
    expect(faceOf(founder.container)).not.toBeNull()
    expect(founder.container.querySelector('.cos-avatar')).toHaveAttribute('data-face', 'human')
    const agent = render(<Avatar family="engineering" size="xl" seed="u1" />)
    expect(faceOf(agent.container).getAttribute('src')).not.toBe(faceOf(founder.container).getAttribute('src'))
    const member = render(<MemberAvatar name="Maya Chen" seed="m1" size="lg" />)
    expect(faceOf(member.container)).not.toBeNull()
    expect(member.container.querySelector('[data-family="member"]')).not.toBeNull()
  })

  it('draws the type glyph until the artwork has loaded, then swaps in the face (and a member shows initials meanwhile)', async () => {
    vi.resetModules()
    const faces = await import('@/lib/faces')
    const fresh = await import('./AgentAvatar')
    const { container } = render(
      <>
        <fresh.Avatar family="engineering" size="lg" seed="s1" />
        <fresh.MemberAvatar name="Maya Chen" seed="m1" size="lg" />
      </>,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).not.toBeNull() // the glyph
    expect(container).toHaveTextContent('MC')
    await act(() => faces.loadFaces())
    expect(container.querySelectorAll('img.cos-avatar-face')).toHaveLength(2)
    expect(container).not.toHaveTextContent('MC')
  })
})
